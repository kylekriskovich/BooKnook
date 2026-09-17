"""FastAPI JSON API backing the BooKnook SvelteKit frontend (see frontend/) — also serves that
frontend's static production build in this same process (see FRONTEND_DIST / spa_fallback)."""

import asyncio
import calendar
import contextlib
import hashlib
import hmac
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Optional

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import cover_color, dates, grimmory_auth, library_check, reading_calendar, schemas, stat_tiles
from app.grimmory_auth import GrimmoryLoginError
from app.hardcover import HardcoverSearchError, search_hardcover
from app.library_check import LibraryCheckUnavailable
from app.metadata import SearchResult, search_books
from app.models import (
    User,
    add_physical_reading_session,
    add_tbr_entry,
    clear_audiobook_pairing,
    clear_linked_edition,
    create_book,
    delete_physical_reading_session,
    get_audiobook_pairings,
    get_book,
    get_connection,
    get_goal,
    get_grimmory_admin_settings,
    get_library_catalog,
    get_library_settings,
    get_library_sync_state,
    get_linked_editions,
    get_linked_editions_for_ebook,
    get_or_create_user,
    get_physical_reading_session,
    get_search_settings,
    get_tbr_entry,
    get_user,
    group_duplicate_reading_sessions,
    init_db,
    list_aggregate_tbr,
    list_cached_reading_sessions,
    list_physical_reading_sessions,
    list_tbr_entries_with_books,
    remove_tbr_entry,
    search_library_catalog,
    set_audiobook_pairing,
    set_book_manual_match_and_grimmory_id,
    set_calendar_view_preference,
    set_grimmory_admin_settings,
    set_grimmory_refresh_token,
    set_library_settings,
    set_linked_edition,
    set_onboarded,
    set_search_settings,
    set_spice_level,
    set_sync_to_device_enabled,
    set_sync_to_device_shelf_id,
    set_tbr_entry_finished_at,
    set_tbr_entry_owns_physical,
    set_tbr_entry_physical_page_count,
    set_tbr_entry_started_at,
    set_view_preference,
    set_wanted_order,
    set_want_to_read_shelf_id,
    soft_delete_cached_reading_session,
    update_physical_reading_session,
    upsert_goal,
)

# Function Name: _resolve_log_level_name
# Description: Validates a TBR_LOG_LEVEL value against logging's level names, falling back to
#   INFO so a typo'd env var doesn't crash the app at import time.
# Parameters:
# - raw (str): Raw TBR_LOG_LEVEL env var value, already uppercased.
# Returns: A valid logging level name (str)
def _resolve_log_level_name(raw: str) -> str:
    return raw if raw in logging.getLevelNamesMapping() else "INFO"


# Configured explicitly so app.* loggers (see app/grimmory_http.py) show INFO by default.
logging.basicConfig(
    level=_resolve_log_level_name(os.environ.get("TBR_LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

APP_DIR = os.path.dirname(__file__)
# SvelteKit adapter-static build output, COPY'd here by the Dockerfile. Absent in backend-only dev.
FRONTEND_DIST = os.path.join(os.path.dirname(APP_DIR), "frontend-dist")
COOKIE_NAME = "tbr_user_id"
SECRET_KEY_ENV = "TBR_SECRET_KEY"
ADMIN_USERNAME_ENV = "TBR_ADMIN_USERNAME"

# 30 days — matches Grimmory's own refresh-token lifetime (see app/grimmory_auth.py).
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def _secret_key() -> bytes:
    key = os.environ.get(SECRET_KEY_ENV)
    if not key:
        raise RuntimeError(f"{SECRET_KEY_ENV} must be set (used to sign session cookies)")
    return key.encode()


def sign_session_cookie(user_id: int, issued_at: "int | None" = None) -> str:
    """Cookie is "<user_id>.<issued_at>.<hmac>"; issued_at is signed so it can't be extended,
    letting _verify_session_cookie enforce SESSION_MAX_AGE_SECONDS server-side."""
    if issued_at is None:
        issued_at = int(time.time())
    payload = f"{user_id}.{issued_at}"
    signature = hmac.new(_secret_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _verify_session_cookie(value: str) -> "int | None":
    parts = value.split(".")
    if len(parts) != 3:
        return None
    user_id_str, issued_at_str, signature = parts
    expected = hmac.new(
        _secret_key(), f"{user_id_str}.{issued_at_str}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        user_id, issued_at = int(user_id_str), int(issued_at_str)
    except ValueError:
        return None
    if time.time() - issued_at > SESSION_MAX_AGE_SECONDS:
        return None
    return user_id


async def spa_fallback(full_path: str) -> FileResponse:
    """Serves a real build file if `full_path` matches one, else index.html for client-side
    routing. Registered from lifespan(), not as a module decorator — see that comment for why."""
    if full_path.startswith("api/") or full_path.startswith("covers/"):
        raise HTTPException(status_code=404, detail="Not found")

    if full_path:
        candidate = os.path.abspath(os.path.join(FRONTEND_DIST, full_path))
        # Path-traversal guard - only serve a file inside the build output.
        if candidate.startswith(os.path.abspath(FRONTEND_DIST) + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)

    response = FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
    # index.html must always revalidate so it picks up the current content-hashed bundle.
    response.headers["Cache-Control"] = "no-cache"
    return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_connection = get_connection()
    try:
        init_db(db_connection)
    finally:
        db_connection.close()

    # Mounted here, not at import time, so it picks up TBR_DB_PATH as configured at real startup.
    app.mount("/covers", StaticFiles(directory=library_check.covers_dir()), name="covers")

    # Registered after /covers - Starlette matches routes in registration order, and
    # spa_fallback's /{full_path:path} would otherwise shadow /covers/* if registered first.
    if os.path.isdir(FRONTEND_DIST):
        app.mount("/_app", StaticFiles(directory=os.path.join(FRONTEND_DIST, "_app")), name="frontend-app")
        icons_dir = os.path.join(FRONTEND_DIST, "icons")
        if os.path.isdir(icons_dir):
            app.mount("/icons", StaticFiles(directory=icons_dir), name="frontend-icons")
        app.add_api_route("/{full_path:path}", spa_fallback, methods=["GET"], include_in_schema=False)

    sync_task = asyncio.create_task(library_check.run_periodic_sync())

    yield

    sync_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sync_task


app = FastAPI(title="BooKnook", lifespan=lifespan)


def _user_from_cookie(request: Request, db_connection: sqlite3.Connection) -> Optional[User]:
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value is None:
        return None
    user_id = _verify_session_cookie(cookie_value)
    if user_id is None:
        return None
    return get_user(db_connection, user_id)


def get_db():
    """One DB connection per request, closed when it finishes. FastAPI caches this across every
    `Depends(get_db)` in the request, so multiple dependencies never open a second connection."""
    db_connection = get_connection()
    try:
        yield db_connection
    finally:
        db_connection.close()


def get_current_user(
    request: Request, db_connection: sqlite3.Connection = Depends(get_db)
) -> Optional[User]:
    return _user_from_cookie(request, db_connection)


class _LoginRequired(Exception):
    """Raised by require_user when there's no valid session. Caught by the exception handler
    below and turned into a 401 — every route that depends on require_user is under /api/*, so a
    JSON client is always what's on the other end."""


def require_user(user: Optional[User] = Depends(get_current_user)) -> User:
    if user is None:
        raise _LoginRequired()
    return user


@app.exception_handler(_LoginRequired)
async def _login_required_handler(request: Request, exc: _LoginRequired) -> JSONResponse:
    return JSONResponse({"detail": "Not authenticated"}, status_code=401)


@app.get("/health")
def health():
    return {"status": "ok"}


def _tbr_entries_for_user(db_connection, user_id: int):
    """TBR entries with books, enriched with the library "owned" flag, whether the matched book has
    a paired audiobook edition, and, where the library match has one, its publishedDate — when the
    Grimmory cross-check is configured."""
    entries = list_tbr_entries_with_books(db_connection, user_id)
    if library_check.is_configured(db_connection):
        catalog = get_library_catalog(db_connection)
        paired_ebook_ids = {
            le.ebook_grimmory_id for le in get_linked_editions(db_connection) if le.format == "AUDIOBOOK"
        }
        for entry in entries:
            # resolve_catalog_match, not find_catalog_match - must honor an admin's manual match
            # (POST /api/admin/books/{id}/match), same as the admin "In Library" view does.
            match = library_check.resolve_catalog_match(entry.book, catalog)
            entry.owned = match is not None
            entry.has_paired_audiobook = match is not None and match.grimmory_id in paired_ebook_ids
            if match and match.published_date:
                entry.book.published_date = match.published_date
    return entries


SHELF_STATUSES = ("reading", "wanted", "finished")


def _shelf_label(status: str, year: int) -> str:
    return {
        "reading": "Currently Reading",
        "wanted": "To Be Read",
        "finished": f"Finished in {year}",
    }[status]


def _finished_at_sort_key(entry) -> datetime:
    """Parses finished_at for sorting — see app/dates.py:parse_instant for the format handling —
    so a plain string sort can't misorder entries at their differing suffixes/precision."""
    parsed = dates.parse_instant(entry.finished_at)
    return parsed if parsed is not None else datetime.min.replace(tzinfo=timezone.utc)


def _entries_for_shelf(entries, status: str, year: int):
    """Entries for one shelf — "finished" is further restricted to finished_at falling within
    `year` (matching the "Finished in {year}" label) and sorted most-recently-finished first.
    "wanted" sorts by the user's own manual order instead (see models.py:set_wanted_order)."""
    matching = [e for e in entries if e.status == status]
    if status == "finished":
        matching = [e for e in matching if e.finished_at and e.finished_at.startswith(str(year))]
        matching.sort(key=_finished_at_sort_key, reverse=True)
    elif status == "wanted":
        matching.sort(key=lambda e: e.sort_order)
    return matching


def _shelves_for_user(db_connection, user_id: int, year: int):
    entries = _tbr_entries_for_user(db_connection, user_id)
    return [
        {
            "status": status,
            "label": _shelf_label(status, year),
            "entries": _entries_for_shelf(entries, status, year),
        }
        for status in SHELF_STATUSES
    ]


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Month arithmetic with year rollover, e.g. (2026, 1, -1) -> (2025, 12)."""
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def _resolve_client_today(raw: str) -> date:
    """Parses a client-supplied "YYYY-MM-DD" local date, falling back to the deployment's
    TBR_TIMEZONE today if missing/malformed - the frontend sends the browser's own local date
    since the server's clock can otherwise lag/lead a viewer's actual local day (see
    app/dates.py:DEFAULT_ZONE), which would show stale calendar/stats/estimates."""
    parsed = dates.parse_date(raw) if raw else None
    return parsed if parsed is not None else dates.today_local()


def _parse_calendar_month(raw: str, today: date) -> tuple[int, int]:
    """Parses a "YYYY-MM" query param, falling back to the given "today"'s month for anything
    missing/malformed rather than erroring — a bad/stale query param shouldn't break the page."""
    if raw:
        try:
            year_str, month_str = raw.split("-", 1)
            year, month = int(year_str), int(month_str)
            if 1 <= month <= 12:
                return year, month
        except ValueError:
            pass
    return today.year, today.month


def _fetch_sessions_by_entry_id(db_connection, user: User, entries: list) -> dict[int, list[dict]]:
    """Ebook + physical reading sessions per entry, for stat_tiles.build_collection_tiles's "Avg
    pages read". Audiobook listening sessions are excluded - they carry no real page data, only a
    percentage-of-ebook-pages estimate, which would skew a pages-read average. A "finished" entry
    with a warm cache costs no Grimmory call at all (library_check.get_or_fetch_reading_sessions);
    only a "reading" one, or a "finished" one still cold, does - so only call this for a bounded
    set of already-finished entries, not the full library."""
    base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
    access_token = grimmory_auth.get_valid_access_token(db_connection, user) if base_url else None
    result: dict[int, list[dict]] = {}
    for entry in entries:
        sessions: list[dict] = []
        if access_token is not None and entry.book.grimmory_book_id:
            try:
                sessions = library_check.get_or_fetch_reading_sessions(
                    db_connection, entry.id, entry.status, "EBOOK", base_url, access_token,
                    entry.book.grimmory_book_id,
                )
            except LibraryCheckUnavailable as exc:
                grimmory_auth.evict_on_rejection(access_token, exc)
        if entry.owns_physical:
            sessions = sessions + [
                stat_tiles.physical_session_to_grimmory_shape(s, entry.physical_page_count)
                for s in list_physical_reading_sessions(db_connection, entry.id)
            ]
        result[entry.id] = sessions
    return result


def _fetch_stats_page_sessions(db_connection, user: User, entries: list) -> dict[int, dict[str, list[dict]]]:
    """Ebook, audiobook, and physical sessions per entry, kept separate by source (unlike
    _fetch_sessions_by_entry_id, which merges ebook+physical for build_collection_tiles's "Avg
    pages read") - powers the Stats page's Reading/Listening/Physical tab stats
    (stat_tiles.reading_session_tiles/listening_session_tiles/physical_session_tiles). Up to two
    Grimmory API calls per entry (ebook + any paired audiobook) once, on top of the calls
    _fetch_sessions_by_entry_id already makes for the same page - a "finished" entry with a warm
    cache costs neither (library_check.get_or_fetch_reading_sessions) - only call this for the
    bounded, already-finished-this-year set, not the full library."""
    base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
    access_token = grimmory_auth.get_valid_access_token(db_connection, user) if base_url else None
    result: dict[int, dict[str, list[dict]]] = {}
    for entry in entries:
        ebook_sessions: list[dict] = []
        audiobook_sessions: list[dict] = []
        if access_token is not None:
            if entry.book.grimmory_book_id:
                try:
                    ebook_sessions = library_check.get_or_fetch_reading_sessions(
                        db_connection, entry.id, entry.status, "EBOOK", base_url, access_token,
                        entry.book.grimmory_book_id,
                    )
                except LibraryCheckUnavailable as exc:
                    grimmory_auth.evict_on_rejection(access_token, exc)
            audiobook_grimmory_id = None
            if entry.book.grimmory_book_id is not None:
                linked = get_linked_editions_for_ebook(db_connection, entry.book.grimmory_book_id)
                audiobook_edition = next((le for le in linked if le.format == "AUDIOBOOK"), None)
                audiobook_grimmory_id = (
                    audiobook_edition.edition_grimmory_id if audiobook_edition else None
                )
            if audiobook_grimmory_id is not None:
                try:
                    audiobook_sessions = library_check.get_or_fetch_reading_sessions(
                        db_connection, entry.id, entry.status, "AUDIOBOOK", base_url, access_token,
                        audiobook_grimmory_id,
                    )
                except LibraryCheckUnavailable as exc:
                    grimmory_auth.evict_on_rejection(access_token, exc)
        physical_sessions = (
            [
                stat_tiles.physical_session_to_grimmory_shape(s, entry.physical_page_count)
                for s in list_physical_reading_sessions(db_connection, entry.id)
            ]
            if entry.owns_physical
            else []
        )
        result[entry.id] = {
            "ebook": ebook_sessions,
            "audiobook": audiobook_sessions,
            "physical": physical_sessions,
        }
    return result


def _calendar_context(db_connection, user: User, year: int, month: int, today: date) -> dict:
    """Shared aggregation behind GET /api/calendar."""
    entries = list_tbr_entries_with_books(db_connection, user.id)
    spans = reading_calendar.month_spans(entries, year, month, today)
    for span in spans:
        cover_color.ensure_cover_color(db_connection, span.entry.book)
    days = reading_calendar.days_active(spans, year, month)
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)

    calendar_tiles = [
        {"label": "Best Streak", "value": f"{reading_calendar.best_streak(days)}d"},
        {"label": "Days Read", "value": str(len(days))},
    ]
    estimated_pages = reading_calendar.estimated_pages(spans, year, month)
    if estimated_pages is not None:
        calendar_tiles.append({"label": "Pages", "value": f"~{estimated_pages}"})
    finished_this_month = [
        entry
        for entry in entries
        if entry.status == "finished"
        and entry.finished_at
        and (parsed := dates.parse_instant(entry.finished_at)) is not None
        and parsed.year == year
        and parsed.month == month
    ]
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    sessions_by_entry_id = _fetch_sessions_by_entry_id(db_connection, user, finished_this_month)
    calendar_tiles += stat_tiles.build_collection_tiles(
        finished_this_month, month_start, month_end, sessions_by_entry_id
    )

    return {
        "calendar_year": year,
        "calendar_month": month,
        "calendar_month_label": f"{calendar.month_name[month]} {year}",
        "calendar_prev": f"{prev_year:04d}-{prev_month:02d}",
        "calendar_next": f"{next_year:04d}-{next_month:02d}",
        "calendar_grid": reading_calendar.calendar_grid(year, month, spans, today),
        "calendar_spans": spans,
        "calendar_tiles": calendar_tiles,
    }


def _open_library_results(query: str) -> "tuple[list[SearchResult], bool]":
    """Runs an Open Library search - (results, error), error=True on any network failure."""
    try:
        return search_books(query), False
    except httpx.HTTPError:
        return [], True


def _spice_labels() -> list[str]:
    """One label per chili level (0-5), derived from grimmory_auth.RESTRICTION_TIERS so the UI
    text can never drift out of sync with the actual thresholds sync_restriction_level applies."""
    tiers = grimmory_auth.RESTRICTION_TIERS
    labels = [f"All ages / {tiers[0]}+"]
    labels += [f"{tier}+" for tier in tiers[1:-1]]
    labels.append(f"{tiers[-1]}+ (everything)")
    return labels


def _requested_row(entry) -> dict:
    """Normalizes an AggregateTBREntry into the plain dict shape schemas.AdminEntryOut expects —
    see _catalog_row for the other source (a full catalog entry has no book id/cover, no
    wanted_by unless it happens to also be requested)."""
    return {
        "id": entry.book.id,
        "title": entry.book.title,
        "author": entry.book.author,
        "cover_url": entry.book.cover_url,
        "wanted_by": entry.wanted_by,
    }


def _catalog_matches_to_search_results(catalog_matches) -> list[schemas.SearchResultOut]:
    return [
        schemas.SearchResultOut(
            title=entry.title,
            author=", ".join(entry.authors) if entry.authors else None,
            isbn=entry.isbn13 or entry.isbn10,
            cover_url=None,
            published_date=entry.published_date,
            grimmory_id=entry.grimmory_id,
        )
        for entry in catalog_matches
    ]


def _catalog_by_grimmory_id(db_connection) -> dict:
    return {c.grimmory_id: c for c in get_library_catalog(db_connection) if c.grimmory_id is not None}


def _catalog_row(catalog_entry, wanted_by: list[str]) -> dict:
    return {
        "title": catalog_entry.title,
        "author": ", ".join(catalog_entry.authors) if catalog_entry.authors else None,
        "cover_url": None,
        "wanted_by": wanted_by,
        "grimmory_id": catalog_entry.grimmory_id,
    }


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------
# /admin* routes have no in-app auth, intentionally - gated at the reverse proxy instead.


def _keep_if_blank(new: str, existing: Optional[str]) -> Optional[str]:
    """A blank submitted secret means "leave unchanged" (schemas.py documents this convention) -
    falls back to the existing stored value rather than overwriting it with blank."""
    return new or existing


def _is_admin(user: User) -> bool:
    """Whether `user` is TBR_ADMIN_USERNAME — purely a UI shortcut (their account sheet gets an
    Admin link), not an access check: /admin itself stays gated externally at the reverse proxy."""
    admin_username = os.environ.get(ADMIN_USERNAME_ENV)
    return bool(admin_username) and user.name == admin_username


def _to_me_out(user: User) -> schemas.MeOut:
    return schemas.MeOut(
        id=user.id,
        name=user.name,
        view_preference=user.view_preference,
        calendar_view_preference=user.calendar_view_preference,
        onboarded=user.onboarded,
        spice_level=user.spice_level,
        is_admin=_is_admin(user),
    )


def _to_book_out(book) -> schemas.BookOut:
    return schemas.BookOut(
        id=book.id,
        title=book.title,
        author=book.author,
        isbn=book.isbn,
        cover_url=book.cover_url,
        published_date=book.published_date,
        page_count=book.page_count,
        cover_color=book.cover_color,
    )


def _to_entry_out(entry) -> schemas.TBREntryOut:
    return schemas.TBREntryOut(
        id=entry.id,
        status=entry.status,
        added_at=entry.added_at,
        book=_to_book_out(entry.book),
        owned=entry.owned,
        has_paired_audiobook=entry.has_paired_audiobook,
        finished_at=entry.finished_at,
        started_at=entry.started_at,
        started_at_manual=entry.started_at_manual,
        rating=entry.rating,
        owns_physical=entry.owns_physical,
        physical_page_count=entry.physical_page_count,
    )


def _to_goal_out(goal) -> "schemas.GoalOut | None":
    if goal is None:
        return None
    return schemas.GoalOut(id=goal.id, timeframe=goal.timeframe, target_count=goal.target_count)


def _to_tile_out(tile: dict) -> schemas.StatTileOut:
    return schemas.StatTileOut(label=tile["label"], value=tile["value"], sub=tile.get("sub"))


def _find_entry_detail(db_connection, user_id: int, entry_id: int):
    """Looks up one entry within the user's own full entry list — list_tbr_entries_with_books is
    already scoped to WHERE user_id = ?, so finding a match here also proves ownership, no
    separate check needed."""
    return next(
        (e for e in list_tbr_entries_with_books(db_connection, user_id) if e.id == entry_id), None
    )


def _unified_progress_and_estimated_page(
    entry, session_lists: list[list[dict]], fallback_percents: list["float | None"]
):
    """Max of each linked edition's latest tracked progress, falling back to its synced percentage
    when session data is empty (Decision 5). `session_lists`/`fallback_percents` are parallel
    per-edition lists."""
    if entry.status != "reading":
        return None, None
    candidates = []
    for sessions, fallback in zip(session_lists, fallback_percents):
        progress = stat_tiles.latest_progress(sessions)
        candidates.append(progress if progress is not None else fallback)
    progress_percent = stat_tiles.unified_latest_progress(candidates)
    estimated_page = (
        round(progress_percent / 100 * entry.book.page_count)
        if progress_percent is not None and entry.book.page_count
        else None
    )
    return progress_percent, estimated_page


def _to_book_span_out(span) -> schemas.BookSpanOut:
    return schemas.BookSpanOut(
        entry_id=span.entry.id,
        book=schemas.CalendarBookOut(
            id=span.entry.book.id,
            title=span.entry.book.title,
            cover_url=span.entry.book.cover_url,
            cover_color=span.entry.book.cover_color,
        ),
        status=span.entry.status,
        start=span.start,
        end=span.end,
        lane=span.lane,
    )


def _to_day_cell_out(cell) -> schemas.DayCellOut:
    return schemas.DayCellOut(
        date=cell.date,
        in_month=cell.in_month,
        is_today=cell.is_today,
        is_future=cell.is_future,
        active_entry_ids=[span.entry.id for span in cell.active_spans],
        cover_entry_ids=[span.entry.id for span in cell.cover_spans],
        bar_entry_ids=[span.entry.id if span is not None else None for span in cell.bar_spans],
    )


def _to_calendar_out(calendar_context: dict, calendar_view: str) -> schemas.CalendarOut:
    return schemas.CalendarOut(
        year=calendar_context["calendar_year"],
        month=calendar_context["calendar_month"],
        month_label=calendar_context["calendar_month_label"],
        prev_month=calendar_context["calendar_prev"],
        next_month=calendar_context["calendar_next"],
        calendar_view=calendar_view,
        spans=[_to_book_span_out(s) for s in calendar_context["calendar_spans"]],
        grid=[[_to_day_cell_out(c) for c in row] for row in calendar_context["calendar_grid"]],
        tiles=[_to_tile_out(t) for t in calendar_context["calendar_tiles"]],
    )


# --- auth ---


@app.post("/api/login", response_model=schemas.MeOut)
def api_login(payload: schemas.LoginIn, db_connection: sqlite3.Connection = Depends(get_db)):
    # response_model is for OpenAPI's benefit - the raw JSONResponse below (needed to also set
    # the session cookie) bypasses FastAPI's automatic response_model serialization.
    try:
        access_token, refresh_token, expires_in = grimmory_auth.login(payload.username, payload.password)
    except GrimmoryLoginError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = get_or_create_user(db_connection, payload.username)
    # Locked so a concurrent get_valid_access_token/refresh() can't clobber this fresh login.
    with grimmory_auth.refresh_lock(user.id):
        set_grimmory_refresh_token(db_connection, user.id, refresh_token)
        grimmory_auth.log_token_write(user.id, refresh_token, "api_login")  # TEMPORARY diagnostic
        grimmory_auth.cache_access_token(user.id, access_token, expires_in)
    base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
    if base_url:
        try:
            library_check.sync_user_reading_status(db_connection, user.id, base_url, access_token)
        except LibraryCheckUnavailable as exc:
            grimmory_auth.evict_on_rejection(access_token, exc)
            logger.exception("Grimmory reading-status sync failed")

    response = JSONResponse(_to_me_out(user).model_dump(mode="json"))
    response.set_cookie(
        COOKIE_NAME,
        sign_session_cookie(user.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        samesite="lax",
        httponly=True,
    )
    return response


@app.post("/api/logout", status_code=204)
def api_logout():
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/api/me")
def api_me(user: Optional[User] = Depends(get_current_user)) -> "schemas.MeOut | None":
    return _to_me_out(user) if user is not None else None


# --- home / shelves ---


@app.get("/api/home", response_model=schemas.HomeOut)
def api_home(
    today: str = "",
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    shelves = _shelves_for_user(db_connection, user.id, _resolve_client_today(today).year)
    return schemas.HomeOut(
        shelves=[
            schemas.ShelfOut(
                status=shelf["status"],
                label=shelf["label"],
                entries=[_to_entry_out(e) for e in shelf["entries"]],
            )
            for shelf in shelves
        ]
    )


def _shelf_out(db_connection, user_id: int, status: str, year: int) -> schemas.ShelfOut:
    entries = _entries_for_shelf(_tbr_entries_for_user(db_connection, user_id), status, year)
    return schemas.ShelfOut(
        status=status, label=_shelf_label(status, year), entries=[_to_entry_out(e) for e in entries]
    )


@app.get("/api/shelf/{status}", response_model=schemas.ShelfOut)
def api_shelf(
    status: str,
    today: str = "",
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    if status not in SHELF_STATUSES:
        raise HTTPException(status_code=404, detail="Unknown shelf")
    return _shelf_out(db_connection, user.id, status, _resolve_client_today(today).year)


@app.post("/api/shelf/wanted/reorder", response_model=schemas.ShelfOut)
def api_reorder_wanted_shelf(
    payload: schemas.ReorderIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    set_wanted_order(db_connection, user.id, payload.entry_ids)
    # year is inert for the "wanted" shelf.
    return _shelf_out(db_connection, user.id, "wanted", datetime.now(timezone.utc).year)


@app.post("/api/onboarding", response_model=schemas.MeOut)
def api_onboarding(
    payload: schemas.OnboardingIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    if payload.target_count is not None and payload.target_count > 0:
        upsert_goal(db_connection, user.id, "year", payload.target_count)
    set_onboarded(db_connection, user.id)
    return _to_me_out(get_user(db_connection, user.id))


# --- book detail ---


@app.get("/api/book/{entry_id}", response_model=schemas.BookDetailOut)
def api_book_detail(
    entry_id: int,
    today: str = "",
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    resolved_today = _resolve_client_today(today)
    entry = _find_entry_detail(db_connection, user.id, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Not found")

    # Paired audiobook id, keyed off grimmory_book_id. Reads linked_editions rather than
    # get_audiobook_pairings's reverse dict, whose UNIQUE constraint rules out two audiobooks
    # ever colliding on the same ebook.
    audiobook_grimmory_id = None
    if entry.book.grimmory_book_id is not None:
        linked = get_linked_editions_for_ebook(db_connection, entry.book.grimmory_book_id)
        audiobook_edition = next((le for le in linked if le.format == "AUDIOBOOK"), None)
        audiobook_grimmory_id = audiobook_edition.edition_grimmory_id if audiobook_edition else None
    entry.has_paired_audiobook = audiobook_grimmory_id is not None

    sessions = []
    audiobook_sessions = []
    access_token = None
    base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
    if base_url and (entry.book.grimmory_book_id or audiobook_grimmory_id is not None):
        access_token = grimmory_auth.get_valid_access_token(db_connection, user)
    if access_token is not None:
        if entry.book.grimmory_book_id:
            try:
                sessions = library_check.get_or_fetch_reading_sessions(
                    db_connection, entry.id, entry.status, "EBOOK", base_url, access_token,
                    entry.book.grimmory_book_id,
                )
            except LibraryCheckUnavailable as exc:
                grimmory_auth.evict_on_rejection(access_token, exc)
        if audiobook_grimmory_id is not None:
            try:
                audiobook_sessions = library_check.get_or_fetch_reading_sessions(
                    db_connection, entry.id, entry.status, "AUDIOBOOK", base_url, access_token,
                    audiobook_grimmory_id,
                )
            except LibraryCheckUnavailable as exc:
                grimmory_auth.evict_on_rejection(access_token, exc)

    # Manually-logged physical sessions (Decisions 7-9), converted to the same Grimmory-shaped
    # dict so nothing downstream needs to know they didn't come from Grimmory.
    physical_sessions = (
        [
            stat_tiles.physical_session_to_grimmory_shape(s, entry.physical_page_count)
            for s in list_physical_reading_sessions(db_connection, entry.id)
        ]
        if entry.owns_physical
        else []
    )

    if (sessions or audiobook_sessions or physical_sessions) and not entry.started_at_manual:
        # Earliest across every linked edition, not just the ebook - a book started via a paired
        # audiobook or a physical stretch before the ebook was ever opened must derive started_at
        # from that earlier date (Decision 2).
        candidates = [
            d
            for d in (
                stat_tiles.first_meaningful_session_date(sessions),
                stat_tiles.first_meaningful_session_date(audiobook_sessions),
                stat_tiles.first_meaningful_session_date(physical_sessions),
            )
            if d is not None
        ]
        derived = min(candidates) if candidates else None
        if derived is not None and derived.isoformat() != entry.started_at:
            set_tbr_entry_started_at(db_connection, entry.id, derived.isoformat(), manual=False)
            entry.started_at = derived.isoformat()

    # Time-spent-by-medium tiles stay split (Decision 6); physical merges into Reading rather than
    # a third tab (Decision 8) - it's the same activity, just untracked by Grimmory.
    tiles = stat_tiles.build_book_tiles(
        entry, sessions + physical_sessions, resolved_today, is_audiobook=False
    )
    audiobook_tiles = stat_tiles.build_book_tiles(
        entry, audiobook_sessions, resolved_today, is_audiobook=True
    )

    # Progress is a per-source high-water mark, not merged (Decision 5); burndown/started_at do
    # flatten sources. audiobook_progress_percent is trusted only while actually paired.
    audiobook_fallback_percent = entry.audiobook_progress_percent if audiobook_grimmory_id is not None else None
    progress_percent, estimated_page = _unified_progress_and_estimated_page(
        entry,
        [sessions, physical_sessions, audiobook_sessions],
        [None, None, audiobook_fallback_percent],
    )
    burndown = stat_tiles.burndown_points(sessions + physical_sessions + audiobook_sessions)

    return schemas.BookDetailOut(
        entry=_to_entry_out(entry),
        tiles=[_to_tile_out(t) for t in tiles],
        audiobook_tiles=[_to_tile_out(t) for t in audiobook_tiles],
        burndown=[schemas.BurndownPointOut(date=d, remaining_percent=r) for d, r in burndown],
        burndown_day_span=stat_tiles.burndown_day_span(burndown),
        progress_percent=progress_percent,
        estimated_page=estimated_page,
    )


# --- stats / calendar ---

# Session-derived stat tiles (build_collection_tiles's "Avg pages read"/"Avg book length" plus
# every *_session_tiles tile, together forming the Stats page's single Overview/Averages/
# Highlights tab group) cost up to 2 Grimmory API calls per finished book with no caching
# upstream, so the grouped result is cached here per (user, year) for a short TTL rather than
# recomputed on every page view. Everything else in StatsOut (goal, finished_count) is a cheap
# local DB read and stays uncached, so editing a goal shows up immediately rather than waiting out
# the TTL. Plain in-process dict, not a DB table - deliberately kept lightweight (see
# conversation) rather than persisting raw session data, which would need its own invalidation/
# sync story.
_STATS_SESSION_CACHE_TTL_SECONDS = 900
_stats_session_cache: dict[tuple[int, int], tuple[float, schemas.StatTileGroupsOut]] = {}


def _cached_stats_tile_groups(
    db_connection, user: User, year: int, finished_this_year: list
) -> schemas.StatTileGroupsOut:
    cache_key = (user.id, year)
    cached = _stats_session_cache.get(cache_key)
    if cached is not None:
        cached_at, cached_groups = cached
        if time.monotonic() - cached_at < _STATS_SESSION_CACHE_TTL_SECONDS:
            return cached_groups

    sessions_by_source = _fetch_stats_page_sessions(db_connection, user, finished_this_year)
    # "Avg pages read"/"Avg book length" (build_collection_tiles) still want ebook+physical merged
    # per Decision 8; derived here rather than fetched a second time via _fetch_sessions_by_entry_id.
    reading_plus_physical_by_entry_id = {
        entry_id: sessions["ebook"] + sessions["physical"] for entry_id, sessions in sessions_by_source.items()
    }
    collection_tiles = stat_tiles.build_collection_tiles(
        finished_this_year, date(year, 1, 1), date(year, 12, 31), reading_plus_physical_by_entry_id
    )

    page_count_by_entry_id = {entry.id: entry.book.page_count for entry in finished_this_year}
    reading_sessions_with_page_counts = [
        (session, page_count_by_entry_id[entry_id])
        for entry_id, sessions in sessions_by_source.items()
        for session in sessions["ebook"]
    ]
    listening_sessions = [
        session for sessions in sessions_by_source.values() for session in sessions["audiobook"]
    ]
    physical_sessions = [
        session for sessions in sessions_by_source.values() for session in sessions["physical"]
    ]
    all_tiles = (
        collection_tiles
        + stat_tiles.reading_session_tiles(reading_sessions_with_page_counts)
        + stat_tiles.listening_session_tiles(listening_sessions)
        + stat_tiles.physical_session_tiles(physical_sessions)
    )
    grouped = stat_tiles.group_stat_tiles(all_tiles)
    tile_groups = schemas.StatTileGroupsOut(
        overview=[_to_tile_out(t) for t in grouped["overview"]],
        averages=[_to_tile_out(t) for t in grouped["averages"]],
        highlights=[_to_tile_out(t) for t in grouped["highlights"]],
    )

    _stats_session_cache[cache_key] = (time.monotonic(), tile_groups)
    return tile_groups


@app.get("/api/stats", response_model=schemas.StatsOut)
def api_stats(
    today: str = "",
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    year = _resolve_client_today(today).year
    goal = get_goal(db_connection, user.id, "year")
    finished_this_year = [
        entry
        for entry in list_tbr_entries_with_books(db_connection, user.id)
        if entry.status == "finished" and entry.finished_at and entry.finished_at.startswith(str(year))
    ]
    tile_groups = _cached_stats_tile_groups(db_connection, user, year, finished_this_year)

    return schemas.StatsOut(
        year=year,
        goal=_to_goal_out(goal),
        finished_count=len(finished_this_year),
        tile_groups=tile_groups,
    )


@app.get("/api/calendar", response_model=schemas.CalendarOut)
def api_calendar(
    month: str = "",
    today: str = "",
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    resolved_today = _resolve_client_today(today)
    cal_year, cal_month = _parse_calendar_month(month, resolved_today)
    calendar_context = _calendar_context(db_connection, user, cal_year, cal_month, resolved_today)
    return _to_calendar_out(calendar_context, user.calendar_view_preference)


# --- settings ---


@app.get("/api/settings", response_model=schemas.SettingsOut)
def api_settings(
    today: str = "",
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    goal = get_goal(db_connection, user.id, "year")
    return schemas.SettingsOut(
        goal=_to_goal_out(goal),
        goal_year=_resolve_client_today(today).year,
        grimmory_admin_configured=grimmory_auth.is_configured(db_connection),
        spice_labels=_spice_labels(),
        spice_level=user.spice_level,
        has_grimmory_session=bool(user.grimmory_refresh_token),
        want_to_read_shelf_id=user.want_to_read_shelf_id,
        sync_to_device_enabled=user.sync_to_device_enabled,
        sync_to_device_shelf_id=user.sync_to_device_shelf_id,
    )


@app.post("/api/settings/goal", response_model=schemas.GoalOut)
def api_settings_goal(
    payload: schemas.GoalIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    return _to_goal_out(upsert_goal(db_connection, user.id, "year", payload.target_count))


@app.post("/api/settings/sync", response_model=schemas.SyncResultOut)
def api_settings_sync(
    payload: schemas.SyncIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
    error = None
    access_token = None
    if not base_url:
        error = "Grimmory login is not configured"
    elif payload.password:
        try:
            access_token, refresh_token, expires_in = grimmory_auth.login(user.name, payload.password)
        except GrimmoryLoginError as exc:
            error = str(exc)
        else:
            # Locked for the same reason as /api/login - see grimmory_auth.refresh_lock.
            with grimmory_auth.refresh_lock(user.id):
                set_grimmory_refresh_token(db_connection, user.id, refresh_token)
                grimmory_auth.log_token_write(user.id, refresh_token, "api_settings_sync")  # TEMPORARY
                grimmory_auth.cache_access_token(user.id, access_token, expires_in)
    else:
        access_token = grimmory_auth.get_valid_access_token(db_connection, user)
        if access_token is None:
            error = "reconnect_needed"

    if access_token is not None:
        try:
            library_check.sync_user_reading_status(db_connection, user.id, base_url, access_token)
        except LibraryCheckUnavailable as exc:
            grimmory_auth.evict_on_rejection(access_token, exc)
            error = str(exc)

    return schemas.SyncResultOut(error=error)


@app.get("/api/settings/shelves", response_model=schemas.ShelfOptionsOut)
def api_settings_shelves(
    user: User = Depends(require_user), db_connection: sqlite3.Connection = Depends(get_db)
):
    # Own route rather than folded into GET /api/settings, so a slow Grimmory only affects this.
    base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
    if not base_url:
        return schemas.ShelfOptionsOut(shelves=[], error="Grimmory login is not configured")

    access_token = grimmory_auth.get_valid_access_token(db_connection, user)
    if access_token is None:
        return schemas.ShelfOptionsOut(shelves=[], error="reconnect_needed")

    try:
        own_id = grimmory_auth.get_own_grimmory_user_id(base_url, access_token)
        shelves = library_check.list_own_shelves(base_url, access_token, own_id)
    except LibraryCheckUnavailable as exc:
        grimmory_auth.evict_on_rejection(access_token, exc)
        return schemas.ShelfOptionsOut(shelves=[], error=str(exc))

    return schemas.ShelfOptionsOut(
        shelves=[
            schemas.ShelfOptionOut(id=shelf["id"], name=shelf["name"])
            for shelf in shelves
            if shelf.get("id") is not None and shelf.get("name") is not None
        ]
    )


@app.post("/api/settings/shelves", response_model=schemas.ShelfSyncSettingsOut)
def api_settings_shelves_update(
    payload: schemas.ShelfSyncSettingsIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    # Pure local write - Grimmory itself rejects assigning to a shelf the user doesn't own, so a
    # stale/foreign shelf id self-corrects loudly on the next sync.
    set_want_to_read_shelf_id(db_connection, user.id, payload.want_to_read_shelf_id)
    set_sync_to_device_enabled(db_connection, user.id, payload.sync_to_device_enabled)
    set_sync_to_device_shelf_id(db_connection, user.id, payload.sync_to_device_shelf_id)

    current = get_user(db_connection, user.id)
    return schemas.ShelfSyncSettingsOut(
        want_to_read_shelf_id=current.want_to_read_shelf_id,
        sync_to_device_enabled=current.sync_to_device_enabled,
        sync_to_device_shelf_id=current.sync_to_device_shelf_id,
    )


@app.post("/api/settings/spice", response_model=schemas.SpiceResultOut)
def api_settings_spice(
    payload: schemas.SpiceIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    level = max(0, min(len(grimmory_auth.RESTRICTION_TIERS) - 1, payload.level))

    error = None
    try:
        admin_session = grimmory_auth.get_admin_session(db_connection)
    except LibraryCheckUnavailable as exc:
        admin_session = None
        error = str(exc)

    if error is None and admin_session is None:
        error = "Ask your admin to set up Grimmory admin access first (/admin/settings)"
    elif error is None:
        admin_base_url, admin_token = admin_session
        try:
            grimmory_id = grimmory_auth.find_grimmory_user_id(admin_base_url, admin_token, user.name)
            if grimmory_id is None:
                error = "Couldn't find your Grimmory account from the admin session"
            else:
                grimmory_auth.sync_restriction_level(admin_base_url, admin_token, grimmory_id, level)
                set_spice_level(db_connection, user.id, level)
        except LibraryCheckUnavailable as exc:
            error = str(exc)

    current = get_user(db_connection, user.id)
    return schemas.SpiceResultOut(spice_level=current.spice_level, error=error)


# --- search ---


@app.get("/api/search/library", response_model=schemas.SearchOut)
def api_search_library(
    q: str = "",
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    query = q.strip()
    # Audiobooks are never a valid search-to-add result for a regular user - only the paired ebook
    # (if any) is listable/searchable in BooKnook. See find_catalog_match's own audiobook guard for
    # the parallel rule on the owned-check side.
    catalog_matches = [
        entry for entry in search_library_catalog(db_connection, query) if entry.format != "AUDIOBOOK"
    ]
    return schemas.SearchOut(query=query, results=_catalog_matches_to_search_results(catalog_matches))


@app.get("/api/search", response_model=schemas.SearchOut)
def api_search(
    q: str = "",
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    query = q.strip()
    search_settings = get_search_settings(db_connection)
    hardcover_key = search_settings.hardcover_api_key if search_settings else None

    error = False
    error_message = None
    results = []
    if query and hardcover_key:
        try:
            results = search_hardcover(query, hardcover_key)
        except HardcoverSearchError:
            error = True
            error_message = "Hardcover search failed — try Open Library below."
    elif query:
        results, error = _open_library_results(query)

    return schemas.SearchOut(
        query=query,
        results=[schemas.SearchResultOut(**vars(r)) for r in results],
        error=error,
        error_message=error_message,
        show_more=bool(query and hardcover_key),
    )


@app.get("/api/search/more", response_model=schemas.SearchOut)
def api_search_more(q: str = "", user: User = Depends(require_user)):
    query = q.strip()
    results, error = _open_library_results(query) if query else ([], False)
    return schemas.SearchOut(
        query=query, results=[schemas.SearchResultOut(**vars(r)) for r in results], error=error
    )


# --- tbr entries ---


@app.post("/api/tbr", response_model=schemas.TBREntryOut, status_code=201)
def api_add_to_tbr(
    payload: schemas.TBRCreateIn,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    book = create_book(
        db_connection,
        title=payload.title,
        author=payload.author or None,
        isbn=payload.isbn or None,
        cover_url=payload.cover_url or None,
        published_date=payload.published_date or None,
    )
    entry = add_tbr_entry(db_connection, user.id, book.id)
    # Fetch the real Grimmory cover in the background, overriding any search-result placeholder.
    if payload.grimmory_id:
        try:
            grimmory_id_int = int(payload.grimmory_id)
        except ValueError:
            grimmory_id_int = None
        if grimmory_id_int is not None:
            background_tasks.add_task(
                library_check.download_cover_for_book_now, book.id, grimmory_id_int
            )
    return _to_entry_out(_find_entry_detail(db_connection, user.id, entry.id))


@app.post("/api/tbr/{entry_id}/remove", status_code=204)
def api_remove_from_tbr(
    entry_id: int,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry and entry.user_id == user.id and entry.status == "wanted":
        # Unassign from Grimmory's Want to Read shelf too, or the next sync's additive shelf-pull
        # (library_check.sync_user_reading_status pass 3) recreates this entry from the shelf.
        base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
        book = get_book(db_connection, entry.book_id)
        if base_url and user.want_to_read_shelf_id is not None and book is not None and book.grimmory_book_id is not None:
            access_token = grimmory_auth.get_valid_access_token(db_connection, user)
            if access_token is not None:
                try:
                    library_check.assign_book_shelves(
                        base_url,
                        access_token,
                        {book.grimmory_book_id},
                        shelves_to_unassign={user.want_to_read_shelf_id},
                    )
                except LibraryCheckUnavailable as exc:
                    grimmory_auth.evict_on_rejection(access_token, exc)
        remove_tbr_entry(db_connection, entry_id)
    return Response(status_code=204)


@app.post("/api/tbr/{entry_id}/dates", response_model=schemas.TBREntryOut)
def api_set_tbr_dates(
    entry_id: int,
    payload: schemas.TBRDatesIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")

    if entry.status in ("reading", "finished"):
        started_at_value = payload.started_at.strip() or None
        set_tbr_entry_started_at(
            db_connection, entry_id, started_at_value, manual=bool(started_at_value)
        )

    finished_at_value = payload.finished_at.strip() or None
    if entry.status == "finished" and finished_at_value:
        finished_date = dates.parse_date(finished_at_value)
        if finished_date is not None:
            set_tbr_entry_finished_at(
                db_connection, entry_id, f"{finished_date.isoformat()}T00:00:00+00:00"
            )
            base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
            book = get_book(db_connection, entry.book_id)
            if base_url and book is not None and book.grimmory_book_id is not None:
                access_token = grimmory_auth.get_valid_access_token(db_connection, user)
                if access_token is not None:
                    try:
                        grimmory_auth.update_book_finished_date(
                            base_url, access_token, book.grimmory_book_id, finished_date
                        )
                    except LibraryCheckUnavailable as exc:
                        grimmory_auth.evict_on_rejection(access_token, exc)
    elif entry.status == "finished":
        set_tbr_entry_finished_at(db_connection, entry_id, None)

    return _to_entry_out(_find_entry_detail(db_connection, user.id, entry_id))


@app.post("/api/tbr/{entry_id}/physical", response_model=schemas.TBREntryOut)
def api_set_tbr_physical(
    entry_id: int,
    payload: schemas.TBRPhysicalIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    set_tbr_entry_owns_physical(db_connection, entry_id, payload.owns_physical)
    return _to_entry_out(_find_entry_detail(db_connection, user.id, entry_id))


@app.post("/api/tbr/{entry_id}/physical-page-count", response_model=schemas.TBREntryOut)
def api_set_tbr_physical_page_count(
    entry_id: int,
    payload: schemas.TBRPhysicalPageCountIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    if payload.physical_page_count is not None and payload.physical_page_count <= 0:
        raise HTTPException(status_code=422, detail="physical_page_count must be a positive integer")
    set_tbr_entry_physical_page_count(db_connection, entry_id, payload.physical_page_count)
    return _to_entry_out(_find_entry_detail(db_connection, user.id, entry_id))


def _to_physical_session_out(session) -> schemas.PhysicalReadingSessionOut:
    return schemas.PhysicalReadingSessionOut(
        id=session.id,
        start_time=session.start_time,
        end_time=session.end_time,
        start_page=session.start_page,
        end_page=session.end_page,
    )


def _validate_physical_session(payload: schemas.PhysicalReadingSessionIn) -> None:
    start = dates.parse_instant(payload.start_time)
    end = dates.parse_instant(payload.end_time)
    if start is None or end is None:
        raise HTTPException(status_code=422, detail="start_time/end_time must be valid timestamps")
    if end <= start:
        raise HTTPException(status_code=422, detail="end_time must be after start_time")
    if payload.start_page < 0 or payload.end_page <= payload.start_page:
        raise HTTPException(status_code=422, detail="end_page must be > start_page >= 0")


@app.get(
    "/api/tbr/{entry_id}/physical-sessions",
    response_model=list[schemas.PhysicalReadingSessionOut],
)
def api_list_physical_reading_sessions(
    entry_id: int,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    return [_to_physical_session_out(s) for s in list_physical_reading_sessions(db_connection, entry_id)]


@app.post(
    "/api/tbr/{entry_id}/physical-sessions",
    response_model=schemas.PhysicalReadingSessionOut,
    status_code=201,
)
def api_add_physical_reading_session(
    entry_id: int,
    payload: schemas.PhysicalReadingSessionIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    _validate_physical_session(payload)
    session = add_physical_reading_session(
        db_connection, entry_id, payload.start_time, payload.end_time, payload.start_page, payload.end_page
    )
    return _to_physical_session_out(session)


@app.post(
    "/api/tbr/{entry_id}/physical-sessions/{session_id}",
    response_model=schemas.PhysicalReadingSessionOut,
)
def api_update_physical_reading_session(
    entry_id: int,
    session_id: int,
    payload: schemas.PhysicalReadingSessionIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    session = get_physical_reading_session(db_connection, session_id)
    if session is None or session.entry_id != entry_id:
        raise HTTPException(status_code=404, detail="Not found")
    _validate_physical_session(payload)
    update_physical_reading_session(
        db_connection, session_id, payload.start_time, payload.end_time, payload.start_page, payload.end_page
    )
    return _to_physical_session_out(get_physical_reading_session(db_connection, session_id))


@app.post("/api/tbr/{entry_id}/physical-sessions/{session_id}/remove", status_code=204)
def api_remove_physical_reading_session(
    entry_id: int,
    session_id: int,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    session = get_physical_reading_session(db_connection, session_id)
    if session is not None and session.entry_id == entry_id:
        delete_physical_reading_session(db_connection, session_id)
    return Response(status_code=204)


# --- session log ---


# Function Name: _session_log_for_entry
# Description: Merges ebook + audiobook (from cached_reading_sessions - already kept warm by
#   library_check.get_or_fetch_reading_sessions on every book-detail/stats load, so this never
#   calls Grimmory itself) and physical sessions into one newest-first log. `pages` uses the same
#   estimation stat_tiles.build_collection_tiles's "Avg pages read" relies on
#   (stat_tiles.session_page_delta) - exact for physical, an estimate for ebook, always None for
#   audiobook (no page concept).
# Parameters:
# - db_connection: Database connection.
# - entry_id (int): Local TBR entry id.
# - page_count (Optional[int]): The book's own page count, for the ebook pages estimate.
# Returns: Session log rows (list[schemas.SessionLogEntryOut]), newest first.
def _session_log_for_entry(
    db_connection, entry_id: int, page_count: Optional[int]
) -> list[schemas.SessionLogEntryOut]:
    rows: list[schemas.SessionLogEntryOut] = []
    for book_type, source in (("EBOOK", "ebook"), ("AUDIOBOOK", "audiobook")):
        for s in list_cached_reading_sessions(db_connection, entry_id, book_type):
            pages = stat_tiles.session_page_delta(s, page_count) if source == "ebook" else None
            rows.append(
                schemas.SessionLogEntryOut(
                    source=source,
                    id=s["id"],
                    start_time=s["startTime"],
                    end_time=s.get("endTime"),
                    duration_seconds=s.get("durationSeconds"),
                    end_progress=s.get("endProgress"),
                    progress_delta=s.get("progressDelta"),
                    pages=round(pages) if pages is not None else None,
                )
            )
    for p in list_physical_reading_sessions(db_connection, entry_id):
        rows.append(
            schemas.SessionLogEntryOut(
                source="physical",
                id=p.id,
                start_time=p.start_time,
                end_time=p.end_time,
                start_page=p.start_page,
                end_page=p.end_page,
                pages=p.end_page - p.start_page,
            )
        )
    # Grimmory instants and physical sessions' own ISO instants (frontend's .toISOString()) sort
    # correctly as plain strings - no need to parse them first.
    rows.sort(key=lambda r: r.start_time, reverse=True)
    return rows


@app.get("/api/tbr/{entry_id}/sessions", response_model=list[schemas.SessionLogEntryOut])
def api_session_log(
    entry_id: int,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    book = get_book(db_connection, entry.book_id)
    return _session_log_for_entry(db_connection, entry_id, book.page_count if book else None)


@app.post("/api/tbr/{entry_id}/sessions/{source}/{session_id}/remove", status_code=204)
def api_remove_session_log_entry(
    entry_id: int,
    source: str,
    session_id: int,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    if source == "physical":
        session = get_physical_reading_session(db_connection, session_id)
        if session is not None and session.entry_id == entry_id:
            delete_physical_reading_session(db_connection, session_id)
    elif source in ("ebook", "audiobook"):
        # Soft-delete only - Grimmory has no session edit/delete API (confirmed empirically), so
        # this can only exclude the session locally, never fix it upstream. Never resurrected by a
        # future resync (models.add_cached_reading_sessions never touches an existing row).
        soft_delete_cached_reading_session(db_connection, entry_id, session_id, source.upper())
    else:
        raise HTTPException(status_code=422, detail="Invalid source")
    return Response(status_code=204)


@app.post("/api/tbr/{entry_id}/sessions/group", response_model=list[schemas.SessionLogEntryOut])
def api_group_duplicate_sessions(
    entry_id: int,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    # Restricted to "finished" entries only - a still-"reading" book can have a burst split across
    # multiple live fetches (get_or_fetch_reading_sessions keeps upserting new session ids as they
    # arrive), so merging mid-stream risks collapsing an incomplete stretch. "Finished" never
    # fetches again, so whatever's cached is permanently the final, complete picture.
    entry = get_tbr_entry(db_connection, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    if entry.status != "finished":
        raise HTTPException(status_code=400, detail="Only finished books can be grouped")
    group_duplicate_reading_sessions(db_connection, entry_id)
    book = get_book(db_connection, entry.book_id)
    return _session_log_for_entry(db_connection, entry_id, book.page_count if book else None)


# --- preferences ---


@app.post("/api/preferences/view", status_code=204)
def api_set_view_preference(
    payload: schemas.ViewPreferenceIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    if payload.view not in ("spine", "cover"):
        raise HTTPException(status_code=422, detail="Invalid view")
    set_view_preference(db_connection, user.id, payload.view)
    return Response(status_code=204)


@app.post("/api/preferences/calendar-view", status_code=204)
def api_set_calendar_view_preference(
    payload: schemas.CalendarViewPreferenceIn,
    user: User = Depends(require_user),
    db_connection: sqlite3.Connection = Depends(get_db),
):
    if payload.view not in ("grid", "list"):
        raise HTTPException(status_code=422, detail="Invalid view")
    set_calendar_view_preference(db_connection, user.id, payload.view)
    return Response(status_code=204)


# --- admin (no in-app auth — see module docstring above) ---


@app.get("/api/admin", response_model=schemas.AdminOut)
def api_admin(db_connection: sqlite3.Connection = Depends(get_db)):
    library_check_enabled = library_check.is_configured(db_connection)
    aggregate_entries = list_aggregate_tbr(db_connection)
    sync_state = None
    needed_entries = [_requested_row(e) for e in aggregate_entries]
    owned_entries = []
    audiobook_entries = []
    if library_check_enabled:
        catalog = get_library_catalog(db_connection)
        sync_state = get_library_sync_state(db_connection)

        # "In library" shows the whole catalog, not just requested-and-owned books.
        wanted_by_for_catalog: dict[int, list[str]] = {}
        needed_entries = []
        manual_owned_rows = []
        manual_audiobook_rows = []
        # Catalog entries already shown via a manual-match row below - skip in the plain pass.
        represented_catalog_ids: set[int] = set()
        for entry in aggregate_entries:
            match = library_check.resolve_catalog_match(entry.book, catalog)
            if match is None:
                needed_entries.append(_requested_row(entry))
            elif entry.book.manual_match_grimmory_id is not None:
                # Manually matched: render using the book's own stored title/author/cover.
                row = _requested_row(entry)
                row["grimmory_id"] = match.grimmory_id
                row["manually_matched"] = True
                if match.format == "AUDIOBOOK":
                    manual_audiobook_rows.append(row)
                else:
                    manual_owned_rows.append(row)
                represented_catalog_ids.add(id(match))
            else:
                wanted_by_for_catalog[id(match)] = entry.wanted_by

        catalog_rows = [
            _catalog_row(c, wanted_by_for_catalog.get(id(c), []))
            for c in catalog
            if id(c) not in represented_catalog_ids and c.format != "AUDIOBOOK"
        ]
        audiobook_catalog_rows = [
            _catalog_row(c, wanted_by_for_catalog.get(id(c), []))
            for c in catalog
            if id(c) not in represented_catalog_ids and c.format == "AUDIOBOOK"
        ]
        owned_entries = sorted(
            manual_owned_rows + catalog_rows, key=lambda row: (row["title"] or "").casefold()
        )
        audiobook_entries = sorted(
            manual_audiobook_rows + audiobook_catalog_rows,
            key=lambda row: (row["title"] or "").casefold(),
        )
        pairings = get_audiobook_pairings(db_connection)
        title_by_grimmory_id = {c.grimmory_id: c.title for c in catalog if c.grimmory_id is not None}
        for row in audiobook_entries:
            row["paired_ebook_title"] = title_by_grimmory_id.get(pairings.get(row["grimmory_id"]))
    return schemas.AdminOut(
        needed_entries=[schemas.AdminEntryOut(**row) for row in needed_entries],
        owned_entries=[schemas.AdminEntryOut(**row) for row in owned_entries],
        audiobook_entries=[schemas.AdminEntryOut(**row) for row in audiobook_entries],
        library_check_enabled=library_check_enabled,
        last_synced_at=sync_state.last_synced_at if sync_state else None,
        last_error=sync_state.last_error if sync_state else None,
    )


@app.post("/api/admin/library-sync", status_code=204)
async def api_admin_library_sync():
    with contextlib.suppress(LibraryCheckUnavailable):
        await asyncio.to_thread(library_check.sync_catalog_now)
    return Response(status_code=204)


@app.get("/api/admin/library-search", response_model=schemas.SearchOut)
def api_admin_library_search(
    q: str = "", exclude_audiobooks: bool = False, db_connection: sqlite3.Connection = Depends(get_db)
):
    # Ungated sibling of GET /api/search/library, for an admin not logged into the app itself.
    # exclude_audiobooks (used by the pairing picker) also excludes ebooks already paired.
    query = q.strip()
    catalog_matches = search_library_catalog(db_connection, query)
    if exclude_audiobooks:
        already_paired_ebook_ids = set(get_audiobook_pairings(db_connection).values())
        catalog_matches = [
            entry
            for entry in catalog_matches
            if entry.format != "AUDIOBOOK" and entry.grimmory_id not in already_paired_ebook_ids
        ]
    return schemas.SearchOut(query=query, results=_catalog_matches_to_search_results(catalog_matches))


@app.post("/api/admin/books/{book_id}/match", status_code=204)
def api_admin_match_book(
    book_id: int,
    payload: schemas.AdminMatchIn,
    db_connection: sqlite3.Connection = Depends(get_db),
):
    book = get_book(db_connection, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Not found")

    if payload.grimmory_id is None:
        # Unmatch: clear the pin and recompute grimmory_book_id from the fuzzy matcher (or None).
        catalog = get_library_catalog(db_connection)
        fallback = library_check.find_catalog_match(book.title, book.isbn, book.author, catalog)
        set_book_manual_match_and_grimmory_id(
            db_connection, book_id, None, fallback.grimmory_id if fallback else None
        )
        return Response(status_code=204)

    target = _catalog_by_grimmory_id(db_connection).get(payload.grimmory_id)
    if target is not None and target.format == "AUDIOBOOK":
        raise HTTPException(
            status_code=422,
            detail="Cannot match to an audiobook edition - use the audiobook pairing screen instead",
        )

    owner_id = library_check.find_owning_book_id(
        db_connection, payload.grimmory_id, exclude_book_id=book_id
    )
    if owner_id is not None:
        owner = get_book(db_connection, owner_id)
        detail = f'Already matched to "{owner.title}"' if owner else "Already matched to another book"
        raise HTTPException(status_code=409, detail=detail)

    set_book_manual_match_and_grimmory_id(db_connection, book_id, payload.grimmory_id, payload.grimmory_id)
    return Response(status_code=204)


@app.post("/api/admin/audiobooks/{audiobook_grimmory_id}/pair", status_code=204)
def api_admin_pair_audiobook(
    audiobook_grimmory_id: int,
    payload: schemas.AdminPairAudiobookIn,
    db_connection: sqlite3.Connection = Depends(get_db),
):
    catalog_by_id = _catalog_by_grimmory_id(db_connection)

    audiobook_entry = catalog_by_id.get(audiobook_grimmory_id)
    if audiobook_entry is None or audiobook_entry.format != "AUDIOBOOK":
        raise HTTPException(status_code=404, detail="Not an audiobook in the current catalog")

    if payload.ebook_grimmory_id is None:
        clear_audiobook_pairing(db_connection, audiobook_grimmory_id)
        # Dual-write during the DESIGN-multi-edition-refactor.md Phase 1-3 transition - see
        # linked_editions in app/models.py. Remove once Phase 3 retires audiobook_pairings.
        clear_linked_edition(db_connection, audiobook_grimmory_id)
        return Response(status_code=204)

    ebook_entry = catalog_by_id.get(payload.ebook_grimmory_id)
    if ebook_entry is None:
        raise HTTPException(status_code=404, detail="Ebook not found in the current catalog")
    if ebook_entry.format == "AUDIOBOOK":
        raise HTTPException(status_code=422, detail="Cannot pair to another audiobook")

    # linked_editions first - its UNIQUE(ebook_grimmory_id, format) can reject this pairing;
    # audiobook_pairings has no such constraint.
    try:
        set_linked_edition(db_connection, audiobook_grimmory_id, payload.ebook_grimmory_id, "AUDIOBOOK")
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=422, detail="This ebook already has a different audiobook linked")
    set_audiobook_pairing(db_connection, audiobook_grimmory_id, payload.ebook_grimmory_id)
    return Response(status_code=204)


@app.get("/api/admin/settings", response_model=schemas.AdminSettingsOut)
def api_admin_settings(db_connection: sqlite3.Connection = Depends(get_db)):
    settings = get_library_settings(db_connection)
    search_settings = get_search_settings(db_connection)
    admin_credentials = get_grimmory_admin_settings(db_connection)
    return schemas.AdminSettingsOut(
        library_settings=schemas.LibrarySettingsOut(
            base_url=settings.base_url,
            username=settings.username,
            password_set=bool(settings.password),
            sync_interval_minutes=settings.sync_interval_minutes,
        )
        if settings
        else None,
        grimmory_admin_settings=schemas.GrimmoryAdminSettingsOut(
            username=admin_credentials.username,
            password_set=bool(admin_credentials.password),
        )
        if admin_credentials
        else None,
        hardcover_api_key_set=bool(search_settings and search_settings.hardcover_api_key),
        default_sync_interval_minutes=library_check.DEFAULT_SYNC_INTERVAL_MINUTES,
    )


@app.post("/api/admin/settings", response_model=schemas.AdminSettingsOut)
def api_admin_settings_save(
    payload: schemas.LibrarySettingsIn,
    db_connection: sqlite3.Connection = Depends(get_db),
):
    existing = get_library_settings(db_connection)
    resolved_password = _keep_if_blank(payload.password, existing.password if existing else None)
    set_library_settings(
        db_connection,
        base_url=payload.base_url or None,
        username=payload.username or None,
        password=resolved_password,
        sync_interval_minutes=payload.sync_interval_minutes,
    )
    return api_admin_settings(db_connection)


@app.post("/api/admin/settings/hardcover", response_model=schemas.AdminSettingsOut)
def api_admin_settings_save_hardcover(
    payload: schemas.HardcoverSettingsIn,
    db_connection: sqlite3.Connection = Depends(get_db),
):
    existing = get_search_settings(db_connection)
    resolved_key = _keep_if_blank(payload.hardcover_api_key, existing.hardcover_api_key if existing else None)
    set_search_settings(db_connection, hardcover_api_key=resolved_key)
    return api_admin_settings(db_connection)


@app.post("/api/admin/settings/grimmory-admin", response_model=schemas.AdminSettingsOut)
def api_admin_settings_save_grimmory_admin(
    payload: schemas.GrimmoryAdminSettingsIn,
    db_connection: sqlite3.Connection = Depends(get_db),
):
    existing = get_grimmory_admin_settings(db_connection)
    resolved_password = _keep_if_blank(payload.password, existing.password if existing else None)
    set_grimmory_admin_settings(
        db_connection, username=payload.username or None, password=resolved_password
    )
    return api_admin_settings(db_connection)
