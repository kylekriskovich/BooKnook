from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from rapidfuzz import fuzz

from app.models import (
    LibraryCatalogEntry,
    add_tbr_entry,
    covers_dir,
    create_book,
    get_connection,
    get_library_settings,
    list_books,
    list_tbr_entries_with_books,
    list_users,
    remove_tbr_entry,
    replace_library_catalog,
    set_book_cover_url,
    set_book_grimmory_id,
    set_book_page_count,
    set_library_sync_state,
    set_tbr_entry_rating,
    set_tbr_entry_started_at,
    set_tbr_entry_status,
)

logger = logging.getLogger(__name__)

# Grimmory REST API client + book matching for the library cross-check (phase 2). Connection
# settings live in the library_settings SQLite table, editable at runtime from /admin/settings -
# not environment variables - so they can be changed without a container restart.

LOGIN_PATH = "/api/v1/auth/login"
BOOKS_PATH = "/api/v1/books"
COVER_PATH = "/api/v1/media/book/{book_id}/cover"
READING_SESSIONS_PATH = "/api/v1/reading-sessions/book/{book_id}"
READING_SESSIONS_PAGE_SIZE = 100

# Prefix set_book_cover_url always writes (see _maybe_download_cover) - distinguishes an already
#-downloaded Grimmory cover from a book/isbn-search placeholder (e.g. an Open Library thumbnail
# stored straight off a search result, see POST /tbr), which should still be replaced once we know
# the real Grimmory cover, rather than treated as "already has a cover, don't bother".
COVER_URL_PREFIX = "/covers/"


def _has_local_cover(cover_url: Optional[str]) -> bool:
    return bool(cover_url) and cover_url.startswith(COVER_URL_PREFIX)

DEFAULT_SYNC_INTERVAL_MINUTES = 60
POLL_INTERVAL_WHEN_UNCONFIGURED_SECONDS = 60

COVER_CONTENT_TYPE_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}

TITLE_MATCH_THRESHOLD = 90
AUTHOR_MATCH_THRESHOLD = 80
TITLE_ONLY_MATCH_THRESHOLD = 95

# Grimmory ReadStatus values (model/enums/ReadStatus.java) that map onto our finished/reading
# shelves. UNREAD/PAUSED/UNSET or no catalog match at all leave the TBR entry's current status
# untouched — genuinely ambiguous, v1 deliberately never downgrades a reading/finished entry back
# toward wanted on those. WONT_READ/ABANDONED are different: an explicit "I'm done with this"
# signal, not ambiguous, so those actively remove a "reading" entry — see ABANDONED_READ_STATUSES.
FINISHED_READ_STATUSES = {"READ"}
READING_READ_STATUSES = {"READING", "RE_READING", "PARTIALLY_READ"}
ABANDONED_READ_STATUSES = {"WONT_READ", "ABANDONED"}


class LibraryCheckUnavailable(Exception):
    """Raised when the Grimmory API can't be reached, or isn't configured."""

# Function Name: _config
# Description: Reads the Grimmory connection settings, if fully configured.
# Parameters:
# - db_connection: Database connection.
# Returns: (base_url, username, password) tuple, or None if not configured.
def _config(db_connection) -> Optional[tuple[str, str, str]]:
    settings = get_library_settings(db_connection)
    if settings is None:
        return None
    if not (settings.base_url and settings.username and settings.password):
        return None
    return settings.base_url.rstrip("/"), settings.username, settings.password

# Function Name: is_configured
# Description: Checks whether the Grimmory library-check connection is configured.
# Parameters:
# - db_connection: Database connection.
# Returns: True if configured, False otherwise.
def is_configured(db_connection) -> bool:
    return _config(db_connection) is not None

# Function Name: _sync_interval_seconds
# Description: Reads the configured catalog sync interval, in seconds.
# Parameters:
# - db_connection: Database connection.
# Returns: Sync interval in seconds (int)
def _sync_interval_seconds(db_connection) -> int:
    settings = get_library_settings(db_connection)
    minutes = settings.sync_interval_minutes if settings else DEFAULT_SYNC_INTERVAL_MINUTES
    return minutes * 60

# Function Name: _book_to_catalog_entry
# Description: Converts a raw Grimmory book dict into a LibraryCatalogEntry.
# Parameters:
# - book (dict): Raw book payload from the Grimmory API.
# Returns: Converted catalog entry (LibraryCatalogEntry)
def _book_to_catalog_entry(book: dict) -> LibraryCatalogEntry:
    metadata = book.get("metadata") or {}
    return LibraryCatalogEntry(
        title=metadata.get("title") or "",
        isbn13=metadata.get("isbn13"),
        isbn10=metadata.get("isbn10"),
        authors=metadata.get("authors") or [],
        published_date=metadata.get("publishedDate"),
        grimmory_id=book.get("id"),
    )

# Function Name: fetch_catalog
# Description: Logs into Grimmory and fetches the full book catalog.
# Parameters:
# - db_connection: Database connection.
# Returns: The current Grimmory catalog (list[LibraryCatalogEntry])
def fetch_catalog(db_connection) -> list[LibraryCatalogEntry]:
    config = _config(db_connection)
    if config is None:
        raise LibraryCheckUnavailable("Grimmory API is not configured")
    base_url, username, password = config

    try:
        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            # No token persistence - a fresh login happens on every call, since this is only
            # invoked periodically/on manual sync, never per-request.
            login_response = client.post(
                LOGIN_PATH, json={"username": username, "password": password}
            )
            login_response.raise_for_status()
            access_token = login_response.json()["accessToken"]

            books_response = client.get(
                BOOKS_PATH,
                params={"stripForListView": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            books_response.raise_for_status()
            books = books_response.json()
    except httpx.HTTPError as exc:
        raise LibraryCheckUnavailable(f"Grimmory API request failed: {exc}") from exc

    entries = [_book_to_catalog_entry(book) for book in books]
    # Backfill grimmory_book_id/covers for any locally-known book that matches this catalog -
    # reuses the read-only service account's own token, so this runs unattended on every catalog
    # sync rather than depending on a household member happening to be logged in (unlike the
    # per-user cover download in sync_user_reading_status, which this complements).
    _apply_catalog_matches_to_local_books(db_connection, base_url, access_token, entries)
    return entries

# Function Name: _apply_catalog_matches_to_local_books
# Description: For every local book that matches a freshly-fetched Grimmory catalog entry,
#   records the catalog entry's grimmory id (same "always overwritten from Grimmory" convention
#   as _sync_book_metadata) and best-effort fills in the real Grimmory cover, unless one's already
#   been downloaded locally (see _has_local_cover - a search-result placeholder cover doesn't count
#   and gets replaced).
# Parameters:
# - db_connection: Database connection.
# - base_url (str): Grimmory base URL.
# - access_token (str): The read-only service account's access token.
# - catalog (list[LibraryCatalogEntry]): Freshly-fetched catalog, with grimmory_id populated.
# Returns: None
def _apply_catalog_matches_to_local_books(
    db_connection, base_url: str, access_token: str, catalog: list[LibraryCatalogEntry]
) -> None:
    for book in list_books(db_connection):
        match = find_catalog_match(book.title, book.isbn, book.author, catalog)
        if match is None or match.grimmory_id is None:
            continue
        set_book_grimmory_id(db_connection, book.id, match.grimmory_id)
        if not _has_local_cover(book.cover_url):
            _maybe_download_cover(db_connection, base_url, access_token, book.id, match.grimmory_id)

# Function Name: _normalize_isbn
# Description: Normalizes an ISBN for comparison (strips hyphens, lowercases).
# Parameters:
# - isbn (str): Raw ISBN string.
# Returns: Normalized ISBN (str)
def _normalize_isbn(isbn: str) -> str:
    return isbn.replace("-", "").strip().lower()

# Function Name: find_catalog_match
# Description: Finds the catalog entry a given TBR book appears to already match, if any.
# Parameters:
# - title (str): TBR book title.
# - isbn (Optional[str]): TBR book ISBN, if known.
# - author (Optional[str]): TBR book author, if known.
# - catalog (list[LibraryCatalogEntry]): Catalog entries to search.
# Returns: Matching catalog entry, or None if no match is found.
def find_catalog_match(
    title: str, isbn: Optional[str], author: Optional[str], catalog: list[LibraryCatalogEntry]
) -> Optional[LibraryCatalogEntry]:
    if isbn:
        normalized = _normalize_isbn(isbn)
        for entry in catalog:
            if entry.isbn13 and _normalize_isbn(entry.isbn13) == normalized:
                return entry
            if entry.isbn10 and _normalize_isbn(entry.isbn10) == normalized:
                return entry

    for entry in catalog:
        title_score = fuzz.token_sort_ratio(title, entry.title)
        if not author:
            if title_score >= TITLE_ONLY_MATCH_THRESHOLD:
                return entry
            continue
        if title_score < TITLE_MATCH_THRESHOLD:
            continue
        author_score = fuzz.token_sort_ratio(author, ", ".join(entry.authors))
        if author_score >= AUTHOR_MATCH_THRESHOLD:
            return entry

    return None

# Function Name: fetch_user_books
# Description: Fetches a user's own Grimmory book list using their own access token.
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): The user's own access token (not the read-only sync account).
# Returns: Raw book payloads for the user's library (list[dict])
def fetch_user_books(base_url: str, access_token: str) -> list[dict]:
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            # Includes readStatus/dateFinished (no stripForListView, unlike fetch_catalog) - called
            # once at login for reading-status auto-detection; token isn't persisted here or by
            # the caller.
            response = client.get(BOOKS_PATH, headers={"Authorization": f"Bearer {access_token}"})
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise LibraryCheckUnavailable(f"Grimmory API request failed: {exc}") from exc

# Function Name: fetch_reading_sessions_for_book
# Description: Fetches every reading session Grimmory has recorded for one book, for the calling
#   user, walking all pages.
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): The calling user's own access token.
# - grimmory_book_id (int): Grimmory's own id for the book.
# Returns: Concatenated raw session list across all pages (list[dict])
def fetch_reading_sessions_for_book(
    base_url: str, access_token: str, grimmory_book_id: int
) -> list[dict]:
    sessions: list[dict] = []
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            page = 0
            while True:
                response = client.get(
                    READING_SESSIONS_PATH.format(book_id=grimmory_book_id),
                    params={"page": page, "size": READING_SESSIONS_PAGE_SIZE},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
                body = response.json()
                sessions.extend(body.get("content") or [])
                page += 1
                # Grimmory paginates this endpoint with a *nested* Page format ({"content": [...],
                # "page": {"totalPages": N, ...}}), not the flat shape a plain reading of "Spring
                # Page response" suggests - getting this wrong silently truncates results
                # (confirmed: undercounted a 118-session book by ~4h of reading time, no error).
                total_pages = (body.get("page") or {}).get("totalPages") or 0
                if page >= total_pages:
                    break
    except httpx.HTTPError as exc:
        raise LibraryCheckUnavailable(f"Grimmory API request failed: {exc}") from exc
    return sessions

# Function Name: _target_status
# Description: Determines the shelf a Grimmory book belongs on, based on its readStatus.
# Parameters:
# - book (dict): Raw Grimmory book payload.
# Returns: "finished", "reading", or None if readStatus doesn't map to either shelf.
def _target_status(book: dict) -> Optional[str]:
    read_status = book.get("readStatus")
    if read_status in FINISHED_READ_STATUSES:
        return "finished"
    if read_status in READING_READ_STATUSES:
        return "reading"
    return None

# Function Name: _sync_book_metadata
# Description: Refreshes page_count/rating/grimmory_id from Grimmory's own book metadata.
# Parameters:
# - db_connection: Database connection.
# - book_id (int): Local TBR book id.
# - entry_id (int): Local TBR entry id.
# - book (dict): Raw Grimmory book payload.
# Returns: None
def _sync_book_metadata(db_connection, book_id: int, entry_id: int, book: dict) -> None:
    metadata = book.get("metadata") or {}
    # Always overwritten from Grimmory, unlike tbr_entries.started_at.
    set_book_page_count(db_connection, book_id, metadata.get("pageCount"))
    set_tbr_entry_rating(db_connection, entry_id, book.get("personalRating"))
    # Captured from the same response so reading sessions can be fetched on demand later (see
    # app/stat_tiles.py), with no extra HTTP call here.
    set_book_grimmory_id(db_connection, book_id, book.get("id"))

# Function Name: _apply_status
# Description: Applies a target shelf (finished/reading) to a TBR entry, without downgrading it.
# Parameters:
# - db_connection: Database connection.
# - entry_id (int): Local TBR entry id.
# - current_status (str): The entry's current shelf status.
# - current_started_at (Optional[str]): The entry's current started_at, if any.
# - target_status (str): The shelf Grimmory's readStatus maps onto ("finished" or "reading").
# - book (dict): Raw Grimmory book payload.
# Returns: None
def _apply_status(
    db_connection,
    entry_id: int,
    current_status: str,
    current_started_at: Optional[str],
    target_status: str,
    book: dict,
) -> None:
    # Never downgrades an already reading/finished entry back toward wanted on an ambiguous
    # Grimmory status (see FINISHED_READ_STATUSES/READING_READ_STATUSES above).
    if target_status == "finished" and current_status != "finished":
        finished_at = book.get("dateFinished") or datetime.now(timezone.utc).isoformat()
        set_tbr_entry_status(db_connection, entry_id, "finished", finished_at)
    elif target_status == "reading" and current_status == "wanted":
        set_tbr_entry_status(db_connection, entry_id, "reading")
        if current_started_at is None:
            # Best-effort only - Grimmory has no session-independent "date started" field, so this
            # is just "whenever we happened to sync". Only set when absent, so a later sync never
            # clobbers a manual correction made via POST /tbr/{id}/started (see app/main.py).
            set_tbr_entry_started_at(
                db_connection, entry_id, datetime.now(timezone.utc).date().isoformat()
            )

# Function Name: fetch_book_cover
# Description: Downloads a book's cover image from Grimmory.
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): Access token to authenticate the request.
# - grimmory_book_id: Grimmory's own id for the book.
# Returns: (content bytes, content_type) tuple, or None if there's no cover or the request fails.
def fetch_book_cover(
    base_url: str, access_token: str, grimmory_book_id
) -> Optional[tuple[bytes, str]]:
    # Grimmory only serves covers behind a short-lived per-request JWT (no stable public URL this
    # app could store directly), so this downloads the bytes once and the caller serves them back
    # out from local disk from then on.
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            response = client.get(
                COVER_PATH.format(book_id=grimmory_book_id),
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError:
        return None
    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        return None
    return response.content, response.headers.get("content-type", "image/jpeg")

# Function Name: _maybe_download_cover
# Description: Fetches and saves a cover for a book, if Grimmory has one.
# Parameters:
# - db_connection: Database connection.
# - base_url (str): Grimmory base URL.
# - access_token (str): Access token to authenticate the request.
# - book_id (int): Local TBR book id.
# - grimmory_book_id: Grimmory's own id for the book, or None.
# Returns: None
def _maybe_download_cover(
    db_connection, base_url: str, access_token: str, book_id: int, grimmory_book_id
) -> None:
    if grimmory_book_id is None:
        return
    # Best-effort - failures are already swallowed inside fetch_book_cover, this never raises.
    result = fetch_book_cover(base_url, access_token, grimmory_book_id)
    if result is None:
        return
    content, content_type = result
    ext = COVER_CONTENT_TYPE_EXT.get(content_type.split(";")[0].strip(), "jpg")
    path = os.path.join(covers_dir(), f"{book_id}.{ext}")
    with open(path, "wb") as cover_file:
        cover_file.write(content)
    set_book_cover_url(db_connection, book_id, f"/covers/{book_id}.{ext}")

# Function Name: _login_service_account
# Description: Logs into Grimmory as the read-only sync account and returns an access token.
# Parameters:
# - base_url (str): Grimmory base URL.
# - username (str): Service account username.
# - password (str): Service account password.
# Returns: Access token (str), or None if the login fails.
def _login_service_account(base_url: str, username: str, password: str) -> Optional[str]:
    try:
        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            login_response = client.post(LOGIN_PATH, json={"username": username, "password": password})
            login_response.raise_for_status()
            return login_response.json()["accessToken"]
    except httpx.HTTPError:
        return None

# Function Name: download_cover_for_book
# Description: Logs in with the read-only service account and downloads one book's cover -
#   used to backfill a cover right away when a book is added straight from an "already in your
#   library" search result (see POST /tbr), rather than waiting for the next periodic catalog
#   sync to catch it.
# Parameters:
# - db_connection: Database connection.
# - book_id (int): Local book id.
# - grimmory_book_id (int): Grimmory's own id for the book.
# Returns: None
def download_cover_for_book(db_connection, book_id: int, grimmory_book_id: int) -> None:
    config = _config(db_connection)
    if config is None:
        return
    base_url, username, password = config
    access_token = _login_service_account(base_url, username, password)
    if access_token is None:
        return
    _maybe_download_cover(db_connection, base_url, access_token, book_id, grimmory_book_id)

# Function Name: download_cover_for_book_now
# Description: Runs download_cover_for_book with its own self-contained database connection -
#   safe to run as a FastAPI background task (see POST /tbr), which outlives the request's own
#   connection.
# Parameters:
# - book_id (int): Local book id.
# - grimmory_book_id (int): Grimmory's own id for the book.
# Returns: None
def download_cover_for_book_now(book_id: int, grimmory_book_id: int) -> None:
    db_connection = get_connection()
    try:
        download_cover_for_book(db_connection, book_id, grimmory_book_id)
    finally:
        db_connection.close()

# Function Name: sync_user_reading_status
# Description: Reflects a user's own Grimmory reading status onto their TBR shelves.
# Parameters:
# - db_connection: Database connection.
# - user_id (int): Local user id.
# - base_url (str): Grimmory base URL.
# - access_token (str): The user's own access token.
# Returns: None
def sync_user_reading_status(
    db_connection, user_id: int, base_url: str, access_token: str
) -> None:
    # Callers are expected to treat this as best-effort - a failed Grimmory call must not block
    # login (see /login) or the on-demand sync (see /settings/sync).
    books = fetch_user_books(base_url, access_token)
    catalog = [_book_to_catalog_entry(book) for book in books]

    matched_indices: set[int] = set()

    # Pass 1: match Grimmory books against existing tbr_entries (by ISBN, then fuzzy title+author)
    # and update status/finished_at, or remove a "reading" entry outright if Grimmory now says
    # WONT_READ/ABANDONED. Also downloads the real Grimmory cover (see
    # fetch_book_cover/_maybe_download_cover) for any matched book that doesn't have one locally
    # yet - see _has_local_cover.
    for entry in list_tbr_entries_with_books(db_connection, user_id):
        match = find_catalog_match(entry.book.title, entry.book.isbn, entry.book.author, catalog)
        if match is None:
            continue
        idx = next(i for i, catalog_entry in enumerate(catalog) if catalog_entry is match)
        matched_indices.add(idx)
        book = books[idx]
        # An explicit abandon/won't-read signal removes a "reading" entry outright, rather than
        # leaving it stuck on the Currently Reading shelf — unlike the ambiguous statuses above,
        # this isn't "unknown", it's "not pursuing this anymore". If it's picked back up and
        # marked reading/finished again later, the import pass below re-adds it (unmatched once
        # removed), so this isn't a one-way trip.
        if entry.status == "reading" and book.get("readStatus") in ABANDONED_READ_STATUSES:
            remove_tbr_entry(db_connection, entry.id)
            continue
        _sync_book_metadata(db_connection, entry.book.id, entry.id, book)
        target = _target_status(book)
        if target is not None:
            _apply_status(db_connection, entry.id, entry.status, entry.started_at, target, book)
        if not _has_local_cover(entry.book.cover_url):
            _maybe_download_cover(
                db_connection, base_url, access_token, entry.book.id, book.get("id")
            )

    # Pass 2: any unmatched Grimmory books are added to the user's TBR, with status/started_at
    for i, book in enumerate(books):
        if i in matched_indices:
            continue
        target = _target_status(book)
        if target is None:
            continue
        catalog_entry = catalog[i]
        if not catalog_entry.title:
            continue
        new_book = create_book(
            db_connection,
            title=catalog_entry.title,
            author=", ".join(catalog_entry.authors) or None,
            isbn=catalog_entry.isbn13 or catalog_entry.isbn10,
            published_date=catalog_entry.published_date,
        )

        new_entry = add_tbr_entry(db_connection, user_id, new_book.id)
        _apply_status(
            db_connection, new_entry.id, new_entry.status, new_entry.started_at, target, book
        )
        _sync_book_metadata(db_connection, new_book.id, new_entry.id, book)
        _maybe_download_cover(db_connection, base_url, access_token, new_book.id, book.get("id"))

# Function Name: check_ownership
# Description: Checks whether a given TBR book appears to already be in the library catalog.
# Parameters:
# - title (str): TBR book title.
# - isbn (Optional[str]): TBR book ISBN, if known.
# - author (Optional[str]): TBR book author, if known.
# - catalog (list[LibraryCatalogEntry]): Catalog entries to search.
# Returns: True if a matching catalog entry is found, False otherwise.
def check_ownership(
    title: str, isbn: Optional[str], author: Optional[str], catalog: list[LibraryCatalogEntry]
) -> bool:
    return find_catalog_match(title, isbn, author, catalog) is not None

# Function Name: sync_catalog
# Description: Fetches the current Grimmory catalog and replaces the local cache.
# Parameters:
# - db_connection: Database connection.
# Returns: None
def sync_catalog(db_connection) -> None:
    try:
        entries = fetch_catalog(db_connection)
    except LibraryCheckUnavailable as exc:
        # On failure, records the error but leaves the last-good cached catalog in place.
        set_library_sync_state(db_connection, last_error=str(exc))
        raise

    replace_library_catalog(db_connection, entries)
    set_library_sync_state(
        db_connection, last_synced_at=datetime.now(timezone.utc).isoformat(), last_error=None
    )

# Function Name: sync_catalog_now
# Description: Syncs the Grimmory catalog using a fresh, self-contained database connection.
# Parameters: None
# Returns: None
def sync_catalog_now() -> None:
    db_connection = get_connection()
    try:
        sync_catalog(db_connection)
    finally:
        db_connection.close()

# Function Name: _sync_all_user_reading_status
# Description: Runs the reading-status sync for every local user with a stored Grimmory session.
# Parameters:
# - db_connection: Database connection.
# Returns: None
def _sync_all_user_reading_status(db_connection) -> None:
    from app import grimmory_auth  # local import: grimmory_auth imports LOGIN_PATH from this

    base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
    if not base_url:
        return
    for user in list_users(db_connection):
        if not user.grimmory_refresh_token:
            continue
        access_token = grimmory_auth.get_valid_access_token(db_connection, user)
        if access_token is None:
            continue
        try:
            sync_user_reading_status(db_connection, user.id, base_url, access_token)
        except LibraryCheckUnavailable:
            # One user's failure never blocks the others or the catalog sync that follows.
            logger.exception("Background reading-status sync failed for user_id=%s", user.id)

# Function Name: _run_sync_cycle
# Description: Runs one sync cycle (per-user reading status, plus catalog sync if configured).
# Parameters: None
# Returns: Seconds to sleep before the next cycle (int)
def _run_sync_cycle() -> int:
    db_connection = get_connection()
    try:
        _sync_all_user_reading_status(db_connection)
        if not is_configured(db_connection):
            return POLL_INTERVAL_WHEN_UNCONFIGURED_SECONDS
        try:
            sync_catalog(db_connection)
        except LibraryCheckUnavailable:
            logger.exception("Grimmory catalog sync failed")
        return _sync_interval_seconds(db_connection)
    finally:
        db_connection.close()

# Function Name: run_periodic_sync
# Description: Runs continuously for the app's lifetime, syncing then sleeping between cycles.
# Parameters: None
# Returns: None
async def run_periodic_sync() -> None:
    while True:
        interval = await asyncio.to_thread(_run_sync_cycle)
        await asyncio.sleep(interval)
