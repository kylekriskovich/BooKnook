from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, Optional

import httpx
from rapidfuzz import fuzz

from app import grimmory_http
from app.models import (
    Book,
    LibraryCatalogEntry,
    add_tbr_entry,
    covers_dir,
    create_book,
    get_connection,
    get_library_settings,
    get_user,
    list_books,
    list_tbr_entries_with_books,
    list_users,
    remove_tbr_entry,
    replace_library_catalog,
    set_book_cover_url,
    set_book_format,
    set_book_grimmory_id,
    set_book_page_count,
    set_library_sync_state,
    set_sync_to_device_shelf_id,
    set_tbr_entry_audiobook_progress_percent,
    set_tbr_entry_rating,
    set_tbr_entry_started_at,
    set_tbr_entry_status,
    set_want_to_read_shelf_id,
)

logger = logging.getLogger(__name__)

# Grimmory REST API client + book matching for the library cross-check (phase 2). Connection
# settings live in the library_settings SQLite table, editable at /admin/settings.

LOGIN_PATH = "/api/v1/auth/login"
BOOKS_PATH = "/api/v1/books"
COVER_PATH = "/api/v1/media/book/{book_id}/cover"
READING_SESSIONS_PATH = "/api/v1/reading-sessions/book/{book_id}"
READING_SESSIONS_PAGE_SIZE = 100
SHELVES_PATH = "/api/v1/shelves"
SHELF_BOOKS_PATH = "/api/v1/shelves/{shelf_id}/books"
BOOKS_SHELVES_PATH = "/api/v1/books/shelves"

# Default names for the two Grimmory shelves this app keeps in sync - only used the first time a
# user's shelf id is resolved; after that it's persisted on users.want_to_read_shelf_id/
# sync_to_device_shelf_id.
DEFAULT_WANT_TO_READ_SHELF_NAME = "Want to Read"
DEFAULT_SYNC_TO_DEVICE_SHELF_NAME = "Booknook: Sync to Device"

# Prefix set_book_cover_url always writes - distinguishes an already-downloaded Grimmory cover from
# a search-result placeholder, which should still be replaced once we know the real cover.
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

# Grimmory ReadStatus values that map onto our finished/reading shelves. UNREAD/PAUSED/UNSET leave
# the entry's status untouched (ambiguous); WONT_READ/ABANDONED actively remove a "reading" entry.
FINISHED_READ_STATUSES = {"READ"}
READING_READ_STATUSES = {"READING", "RE_READING", "PARTIALLY_READ"}
ABANDONED_READ_STATUSES = {"WONT_READ", "ABANDONED"}


class LibraryCheckUnavailable(Exception):
    """Raised when the Grimmory API can't be reached, isn't configured, or rejected a request.

    status_code carries the HTTP status Grimmory returned, when known. is_auth_rejection is True
    only for a real 401/403 - callers use it to decide whether a cached access token is worth
    evicting (a transient 5xx/connection error doesn't mean the token itself is bad).
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_auth_rejection(self) -> bool:
        return self.status_code in (401, 403)

    @classmethod
    def from_http_error(cls, exc: httpx.HTTPError, message: str) -> "LibraryCheckUnavailable":
        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        return cls(message, status_code=status_code)

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
        with grimmory_http.client(base_url=base_url, timeout=10.0) as client:
            # No token persistence - a fresh login happens on every call.
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
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory catalog fetch failed: %s", exc)
        raise LibraryCheckUnavailable.from_http_error(exc, f"Grimmory API request failed: {exc}") from exc

    entries = [_book_to_catalog_entry(book) for book in books]
    # Backfill grimmory_book_id/covers for any locally-known book that matches - reuses the
    # read-only service account's token, so this runs unattended without a user being logged in.
    _apply_catalog_matches_to_local_books(db_connection, base_url, access_token, entries)
    return entries

# Function Name: _apply_catalog_matches_to_local_books
# Description: For every local book that matches a freshly-fetched Grimmory catalog entry, records
#   the grimmory id and best-effort fills in the real cover if one isn't already local.
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
        match = resolve_catalog_match(book, catalog)
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

# Function Name: resolve_catalog_match
# Description: Finds the catalog entry a book should be considered to own — an admin-asserted
#   manual pin (see POST /api/admin/books/{id}/match) always wins over the fuzzy matcher.
# Parameters:
# - book (Book): The local book to resolve a match for.
# - catalog (list[LibraryCatalogEntry]): Catalog entries to search.
# Returns: Matching catalog entry, or None if no match is found.
def resolve_catalog_match(book: Book, catalog: list[LibraryCatalogEntry]) -> Optional[LibraryCatalogEntry]:
    if book.manual_match_grimmory_id is not None:
        # Looked up by grimmory_id in the given catalog, not book.grimmory_book_id (which may be
        # stale) - returns None rather than guessing if the pin no longer resolves.
        for entry in catalog:
            if entry.grimmory_id == book.manual_match_grimmory_id:
                return entry
        return None
    return find_catalog_match(book.title, book.isbn, book.author, catalog)

# Function Name: find_owning_book_id
# Description: Finds which local book (if any) currently owns a given Grimmory catalog id, via
#   either a manual match or an auto-match - used to block matching the same catalog book to two
#   different needed entries.
# Parameters:
# - db_connection: Database connection.
# - grimmory_id (int): The Grimmory book id being claimed.
# - exclude_book_id (Optional[int]): A book id to skip - lets a book be re-confirmed against its
#   own already-matched entry without tripping the conflict check.
# Returns: The owning book's local id, or None if unowned.
def find_owning_book_id(
    db_connection, grimmory_id: int, exclude_book_id: Optional[int] = None
) -> Optional[int]:
    # Compares against each book's own persisted claim (manual pin, else grimmory_book_id) - the
    # source of truth, rather than a possibly-stale freshly-loaded catalog list.
    for book in list_books(db_connection):
        if book.id == exclude_book_id:
            continue
        claimed_id = book.manual_match_grimmory_id
        if claimed_id is None:
            claimed_id = book.grimmory_book_id
        if claimed_id == grimmory_id:
            return book.id
    return None

# Function Name: fetch_user_books
# Description: Fetches a user's own Grimmory book list using their own access token.
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): The user's own access token (not the read-only sync account).
# Returns: Raw book payloads for the user's library (list[dict])
def fetch_user_books(base_url: str, access_token: str) -> list[dict]:
    try:
        with grimmory_http.client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            # Includes readStatus/dateFinished (no stripForListView, unlike fetch_catalog).
            response = client.get(BOOKS_PATH, headers={"Authorization": f"Bearer {access_token}"})
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory user-books fetch failed: %s", exc)
        raise LibraryCheckUnavailable.from_http_error(exc, f"Grimmory API request failed: {exc}") from exc

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
        with grimmory_http.client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
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
                # Nested Page format ({"content": [...], "page": {"totalPages": N}}), not flat -
                # getting this wrong silently truncates results (confirmed in production).
                total_pages = (body.get("page") or {}).get("totalPages") or 0
                if page >= total_pages:
                    break
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning(
                "Grimmory reading-sessions fetch failed for book %s: %s", grimmory_book_id, exc
            )
        raise LibraryCheckUnavailable.from_http_error(exc, f"Grimmory API request failed: {exc}") from exc
    return sessions

# Function Name: list_own_shelves
# Description: Fetches the calling user's own Grimmory shelves (never someone else's public ones).
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): The calling user's own access token.
# - own_grimmory_user_id (int): The calling user's own Grimmory numeric id (see
#   app/grimmory_auth.py:get_own_grimmory_user_id).
# Returns: Raw shelf payloads owned by this user (list[dict])
def list_own_shelves(base_url: str, access_token: str, own_grimmory_user_id: int) -> list[dict]:
    # GET /api/v1/shelves returns own + public shelves mixed, no server-side owner filter.
    try:
        with grimmory_http.client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            response = client.get(SHELVES_PATH, headers={"Authorization": f"Bearer {access_token}"})
            response.raise_for_status()
            shelves = response.json()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory shelves fetch failed: %s", exc)
        raise LibraryCheckUnavailable.from_http_error(exc, f"Grimmory API request failed: {exc}") from exc
    return [shelf for shelf in shelves if shelf.get("userId") == own_grimmory_user_id]

# Function Name: _shelf_name_key
# Description: Normalizes a shelf name for matching - Grimmory's duplicate-name check is
#   case-insensitive (MariaDB's default collation), so this must be too or get_or_create_shelf_by_name
#   loops forever on a 409 it can never resolve (confirmed in production).
# Parameters:
# - name (str): Raw shelf name.
# Returns: Normalized name (str)
def _shelf_name_key(name: str) -> str:
    return name.strip().casefold()

# Function Name: get_or_create_shelf_by_name
# Description: Idempotent get-or-create for a Grimmory shelf, keyed by name.
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): The calling user's own access token.
# - own_grimmory_user_id (int): The calling user's own Grimmory numeric id.
# - name (str): Shelf name to find or create.
# Returns: The shelf's Grimmory id (int)
def get_or_create_shelf_by_name(
    base_url: str, access_token: str, own_grimmory_user_id: int, name: str
) -> int:
    # GET-then-POST rather than relying on Grimmory's SHELF_ALREADY_EXISTS (409) - also means a
    # shelf the user already made under this name is silently adopted.
    target_key = _shelf_name_key(name)
    for shelf in list_own_shelves(base_url, access_token, own_grimmory_user_id):
        if shelf.get("id") is not None and _shelf_name_key(shelf.get("name") or "") == target_key:
            return shelf["id"]
    try:
        with grimmory_http.client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            response = client.post(
                SHELVES_PATH,
                json={"name": name, "publicShelf": False},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 409:
                # Lost a create race against a concurrent sync for the same user - not an error,
                # adopt the shelf someone else just created.
                for shelf in list_own_shelves(base_url, access_token, own_grimmory_user_id):
                    if shelf.get("id") is not None and _shelf_name_key(shelf.get("name") or "") == target_key:
                        return shelf["id"]
                raise LibraryCheckUnavailable(
                    f"Grimmory reported shelf {name!r} already exists but it isn't visible yet"
                )
            response.raise_for_status()
            body = response.json()
            if body.get("id") is None:
                raise LibraryCheckUnavailable(
                    f"Grimmory created shelf {name!r} but returned no id"
                )
            return body["id"]
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory get-or-create shelf %r failed: %s", name, exc)
        raise LibraryCheckUnavailable.from_http_error(exc, f"Grimmory API request failed: {exc}") from exc

# Function Name: fetch_shelf_books
# Description: Fetches every book currently on a Grimmory shelf.
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): The calling user's own access token.
# - shelf_id (int): Grimmory's own id for the shelf.
# Returns: Raw book payloads (list[dict]), same shape as fetch_user_books' response.
def fetch_shelf_books(base_url: str, access_token: str, shelf_id: int) -> list[dict]:
    try:
        with grimmory_http.client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            response = client.get(
                SHELF_BOOKS_PATH.format(shelf_id=shelf_id),
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory shelf-books fetch failed for shelf %s: %s", shelf_id, exc)
        raise LibraryCheckUnavailable.from_http_error(exc, f"Grimmory API request failed: {exc}") from exc

# Function Name: assign_book_shelves
# Description: Batched shelf-membership assign/unassign for one or more books.
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): The calling user's own access token.
# - book_ids (set[int]): Grimmory book ids to apply the assign/unassign sets to.
# - shelves_to_assign (set[int]): Shelf ids to add every book in book_ids to.
# - shelves_to_unassign (set[int]): Shelf ids to remove every book in book_ids from.
# Returns: None
def assign_book_shelves(
    base_url: str,
    access_token: str,
    book_ids: Iterable[int],
    shelves_to_assign: Iterable[int] = frozenset(),
    shelves_to_unassign: Iterable[int] = frozenset(),
) -> None:
    # shelves_to_assign/unassign apply uniformly to every id in book_ids (confirmed against
    # Grimmory's BookUpdateService) - this is not a per-book instruction list, so callers must
    # issue one call per distinct (assign-set, unassign-set) combination they need.
    if not book_ids:
        return
    try:
        with grimmory_http.client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            response = client.post(
                BOOKS_SHELVES_PATH,
                json={
                    "bookIds": list(book_ids),
                    "shelvesToAssign": list(shelves_to_assign),
                    "shelvesToUnassign": list(shelves_to_unassign),
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory assign-book-shelves failed: %s", exc)
        raise LibraryCheckUnavailable.from_http_error(exc, f"Grimmory API request failed: {exc}") from exc

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
# Description: Refreshes page_count/rating/grimmory_id/format/audiobook progress from Grimmory's
#   own book metadata.
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
    set_book_grimmory_id(db_connection, book_id, book.get("id"))
    # primaryFile.bookType ("EPUB", "AUDIOBOOK", ...) lets app/stat_tiles.py label tiles correctly.
    primary_file = book.get("primaryFile") or {}
    set_book_format(db_connection, book_id, primary_file.get("bookType"))
    # Grimmory never populates session progress deltas for audiobooks - this comes from its
    # separate progress-update endpoint instead, used as a fallback in app/main.py:api_book_detail.
    audiobook_progress = book.get("audiobookProgress") or {}
    set_tbr_entry_audiobook_progress_percent(db_connection, entry_id, audiobook_progress.get("percentage"))

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
    # Never downgrades an already reading/finished entry back toward wanted.
    if target_status == "finished" and current_status != "finished":
        finished_at = book.get("dateFinished") or datetime.now(timezone.utc).isoformat()
        set_tbr_entry_status(db_connection, entry_id, "finished", finished_at)
    elif target_status == "reading" and current_status == "wanted":
        set_tbr_entry_status(db_connection, entry_id, "reading")
        if current_started_at is None:
            # Best-effort "whenever we synced" - only set when absent so a manual correction via
            # POST /tbr/{id}/started is never clobbered.
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
    # Grimmory only serves covers behind a short-lived JWT, so this downloads once and the caller
    # serves the bytes back out from local disk from then on.
    try:
        with grimmory_http.client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            response = client.get(
                COVER_PATH.format(book_id=grimmory_book_id),
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("Grimmory cover fetch failed for book %s: %s", grimmory_book_id, exc)
        return None
    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        # Already logged by grimmory_http's Client event hooks when the response came in above.
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
        with grimmory_http.client(base_url=base_url, timeout=10.0) as client:
            login_response = client.post(LOGIN_PATH, json={"username": username, "password": password})
            login_response.raise_for_status()
            return login_response.json()["accessToken"]
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory service-account login failed: %s", exc)
        return None

# Function Name: download_cover_for_book
# Description: Logs in with the read-only service account and downloads one book's cover -
#   backfills right away when a book is added from an "already in your library" search result.
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
# Description: Runs download_cover_for_book with its own connection - safe as a FastAPI background
#   task, which outlives the request's own connection.
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

# Function Name: _ensure_want_to_read_shelf
# Description: Resolves this user's Want to Read shelf id, lazily get-or-creating it in Grimmory
#   by name the first time (see DEFAULT_WANT_TO_READ_SHELF_NAME) and persisting it thereafter.
# Parameters:
# - db_connection: Database connection.
# - user (User): The user whose shelf id to resolve (mutated in place on first resolution).
# - base_url (str): Grimmory base URL.
# - access_token (str): The user's own access token.
# Returns: Grimmory shelf id (int)
def _ensure_want_to_read_shelf(db_connection, user, base_url: str, access_token: str) -> int:
    if user.want_to_read_shelf_id is not None:
        return user.want_to_read_shelf_id
    from app import grimmory_auth  # local import: grimmory_auth imports LOGIN_PATH from this

    own_id = grimmory_auth.get_own_grimmory_user_id(base_url, access_token)
    shelf_id = get_or_create_shelf_by_name(
        base_url, access_token, own_id, DEFAULT_WANT_TO_READ_SHELF_NAME
    )
    set_want_to_read_shelf_id(db_connection, user.id, shelf_id)
    user.want_to_read_shelf_id = shelf_id
    return shelf_id

# Function Name: _ensure_sync_to_device_shelf
# Description: Resolves this user's Sync to Device shelf id, lazily get-or-creating it in Grimmory
#   by name the first time (see DEFAULT_SYNC_TO_DEVICE_SHELF_NAME) and persisting it thereafter.
# Parameters:
# - db_connection: Database connection.
# - user (User): The user whose shelf id to resolve (mutated in place on first resolution).
# - base_url (str): Grimmory base URL.
# - access_token (str): The user's own access token.
# Returns: Grimmory shelf id (int)
def _ensure_sync_to_device_shelf(db_connection, user, base_url: str, access_token: str) -> int:
    if user.sync_to_device_shelf_id is not None:
        return user.sync_to_device_shelf_id
    from app import grimmory_auth  # local import: grimmory_auth imports LOGIN_PATH from this

    own_id = grimmory_auth.get_own_grimmory_user_id(base_url, access_token)
    shelf_id = get_or_create_shelf_by_name(
        base_url, access_token, own_id, DEFAULT_SYNC_TO_DEVICE_SHELF_NAME
    )
    set_sync_to_device_shelf_id(db_connection, user.id, shelf_id)
    user.sync_to_device_shelf_id = shelf_id
    return shelf_id

# Function Name: _dedupe_by_grimmory_id
# Description: Keeps only the first occurrence of each Grimmory book id. Grimmory's own
#   GET /api/v1/books response has been observed to include the same book id more than once in a
#   single call (see issue #22) - left un-deduped, only the first occurrence gets matched in Pass 1
#   below, and every repeat reads as "unmatched" to Pass 2, minting a duplicate local book per repeat.
# Parameters:
# - books (list[dict]): Raw Grimmory book payloads, as returned by fetch_user_books.
# Returns: The same payloads with repeat ids removed, order preserved.
def _dedupe_by_grimmory_id(books: list[dict]) -> list[dict]:
    seen: set[int] = set()
    deduped = []
    for book in books:
        book_id = book.get("id")
        if book_id is not None:
            if book_id in seen:
                continue
            seen.add(book_id)
        deduped.append(book)
    return deduped

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
    # Best-effort - a failed Grimmory call must not block login or the on-demand sync.
    user = get_user(db_connection, user_id)
    if user is None:
        # Deleted mid-sync - surface the exception type callers expect, not an AttributeError.
        raise LibraryCheckUnavailable(f"No such user_id={user_id}")
    books = _dedupe_by_grimmory_id(fetch_user_books(base_url, access_token))
    catalog = [_book_to_catalog_entry(book) for book in books]

    # Grimmory's book id, once known, is a fully reliable match key - unlike title/isbn/author,
    # which can drift between syncs and silently fail the fuzzy matcher below.
    books_by_grimmory_id: dict[int, int] = {}
    for i, book in enumerate(books):
        book_id = book.get("id")
        if book_id is not None:
            books_by_grimmory_id.setdefault(book_id, i)

    matched_indices: set[int] = set()

    # Pass 1: match Grimmory books against existing tbr_entries, by grimmory_book_id when already
    # known, else fuzzy ISBN/title/author (matching by id first prevents re-adding an already
    # tracked book as a duplicate when its metadata drifts slightly - see issue #22). Updates
    # status/finished_at, removes a "reading" entry if now WONT_READ/ABANDONED, and downloads a
    # missing cover.
    for entry in list_tbr_entries_with_books(db_connection, user_id):
        idx = None
        if entry.book.grimmory_book_id is not None:
            idx = books_by_grimmory_id.get(entry.book.grimmory_book_id)
        if idx is None:
            match = find_catalog_match(entry.book.title, entry.book.isbn, entry.book.author, catalog)
            if match is None:
                continue
            idx = next(i for i, catalog_entry in enumerate(catalog) if catalog_entry is match)
        matched_indices.add(idx)
        book = books[idx]
        # Explicit abandon/won't-read removes a "reading" entry outright - not a one-way trip, the
        # import pass below re-adds it if picked back up later.
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

    # Pass 3: Want to Read shelf (always on) - pulls in any unknown Grimmory-shelf book (additive
    # only), then re-diffs desired vs. current membership every sync as a standing invariant.
    want_shelf_id = _ensure_want_to_read_shelf(db_connection, user, base_url, access_token)
    shelf_books = fetch_shelf_books(base_url, access_token, want_shelf_id)
    entries = list_tbr_entries_with_books(db_connection, user_id)
    known_grimmory_ids = {
        entry.book.grimmory_book_id for entry in entries if entry.book.grimmory_book_id is not None
    }
    for book in shelf_books:
        grimmory_id = book.get("id")
        if grimmory_id is None or grimmory_id in known_grimmory_ids:
            continue
        catalog_entry = _book_to_catalog_entry(book)
        if not catalog_entry.title:
            continue
        new_book = create_book(
            db_connection,
            title=catalog_entry.title,
            author=", ".join(catalog_entry.authors) or None,
            isbn=catalog_entry.isbn13 or catalog_entry.isbn10,
            published_date=catalog_entry.published_date,
        )
        new_entry = add_tbr_entry(db_connection, user_id, new_book.id, status="wanted")
        _sync_book_metadata(db_connection, new_book.id, new_entry.id, book)
        _maybe_download_cover(db_connection, base_url, access_token, new_book.id, grimmory_id)
        known_grimmory_ids.add(grimmory_id)

    # Re-fetch to pick up this pass's own inserts above before computing the diff.
    entries = list_tbr_entries_with_books(db_connection, user_id)
    desired_want = {
        entry.book.grimmory_book_id
        for entry in entries
        if entry.status == "wanted" and entry.book.grimmory_book_id is not None
    }
    current_want = {book["id"] for book in shelf_books if book.get("id") is not None}
    to_assign = desired_want - current_want
    to_unassign = current_want - desired_want
    if to_assign:
        assign_book_shelves(base_url, access_token, to_assign, shelves_to_assign={want_shelf_id})
    if to_unassign:
        assign_book_shelves(
            base_url, access_token, to_unassign, shelves_to_unassign={want_shelf_id}
        )

    # Pass 4: Sync to Device shelf (opt-in) - strictly additive, feeds the external KOReader
    # plugin. Never unassigned, even across a status change or delete.
    if user.sync_to_device_enabled:
        device_shelf_id = _ensure_sync_to_device_shelf(db_connection, user, base_url, access_token)
        device_ids = {
            entry.book.grimmory_book_id
            for entry in entries
            if entry.book.grimmory_book_id is not None
        }
        if device_ids:
            assign_book_shelves(
                base_url, access_token, device_ids, shelves_to_assign={device_shelf_id}
            )

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
        except LibraryCheckUnavailable as exc:
            # One user's failure never blocks the others. Evict the cached token only on an
            # actual auth rejection, not a transient failure.
            if exc.is_auth_rejection:
                grimmory_auth.evict_access_token(access_token)
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
