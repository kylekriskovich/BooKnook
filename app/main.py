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
    add_tbr_entry,
    create_book,
    get_book,
    get_connection,
    get_goal,
    get_grimmory_admin_settings,
    get_library_catalog,
    get_library_settings,
    get_library_sync_state,
    get_or_create_user,
    get_search_settings,
    get_tbr_entry,
    get_user,
    init_db,
    list_aggregate_tbr,
    list_tbr_entries_with_books,
    remove_tbr_entry,
    search_library_catalog,
    set_book_manual_match_and_grimmory_id,
    set_calendar_view_preference,
    set_grimmory_admin_settings,
    set_grimmory_refresh_token,
    set_library_settings,
    set_onboarded,
    set_search_settings,
    set_spice_level,
    set_sync_to_device_enabled,
    set_sync_to_device_shelf_id,
    set_tbr_entry_finished_at,
    set_tbr_entry_started_at,
    set_view_preference,
    set_wanted_order,
    set_want_to_read_shelf_id,
    upsert_goal,
)

# Configured here rather than left to whatever default uvicorn/the WSGI/ASGI server happens to
# set up, so app.* loggers (notably app.grimmory_api - see app/grimmory_http.py) reliably show
# every Grimmory request/response instead of only warnings/errors via Python's bare last-resort
# stderr handler. TBR_LOG_LEVEL lets an operator turn this down without a code change if the
# per-request INFO lines get too noisy in production.
logging.basicConfig(
    level=os.environ.get("TBR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

APP_DIR = os.path.dirname(__file__)
# frontend-dist/ is the SvelteKit adapter-static build output — COPY'd there by the Dockerfile's
# frontend-build stage. Absent in local backend-only dev (`uvicorn app.main:app --reload` without
# ever running `npm run build`); see frontend/README.md, which runs its own Vite dev server
# against this backend instead of hitting the routes below.
FRONTEND_DIST = os.path.join(os.path.dirname(APP_DIR), "frontend-dist")
COOKIE_NAME = "tbr_user_id"
SECRET_KEY_ENV = "TBR_SECRET_KEY"
ADMIN_USERNAME_ENV = "TBR_ADMIN_USERNAME"

# 30 days — matches Grimmory's own refresh-token lifetime (see app/grimmory_auth.py), so a TBR
# session lasts about as long as Grimmory would keep the user's own stored refresh token valid.
# Reading-status sync no longer depends on this cookie's lifetime for freshness — it now runs in
# the background on Grimmory's own account, independent of login frequency (see
# library_check.run_periodic_sync and SPEC.md > Reading-status auto-detection).
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def _secret_key() -> bytes:
    key = os.environ.get(SECRET_KEY_ENV)
    if not key:
        raise RuntimeError(f"{SECRET_KEY_ENV} must be set (used to sign session cookies)")
    return key.encode()


def sign_session_cookie(user_id: int, issued_at: "int | None" = None) -> str:
    """Cookie holds "<user_id>.<issued_at>.<hmac>". issued_at is covered by the signature (not
    just appended after it) so it can't be tampered with to extend a session, and lets
    _verify_session_cookie enforce SESSION_MAX_AGE_SECONDS server-side rather than relying solely
    on the browser honoring the cookie's max_age."""
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
    """Serves a real file from the SvelteKit build (the content-hashed workbox-*.js,
    manifest.webmanifest, robots.txt, ...) when `full_path` matches one; otherwise falls back to
    index.html so the client-side router can resolve the route itself — deep links like
    /book/42, or a hard refresh on /calendar, have no server-side route of their own. Pairs with
    frontend/vite.config.ts's adapter({ fallback: 'index.html' }), which assumes exactly this.
    Registered from inside lifespan() (see below), not as a module-level decorator — see the
    comment there for why that ordering matters.
    """
    if full_path.startswith("api/") or full_path.startswith("covers/"):
        raise HTTPException(status_code=404, detail="Not found")

    if full_path:
        candidate = os.path.abspath(os.path.join(FRONTEND_DIST, full_path))
        # Path-traversal guard (e.g. "/../../etc/passwd") — only ever serve a real file that
        # resolves to somewhere inside the build output.
        if candidate.startswith(os.path.abspath(FRONTEND_DIST) + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)

    response = FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
    # index.html is the one URL that never changes across deploys and must always point at
    # whichever content-hashed JS/CSS bundle is current — no-cache forces revalidation on every
    # load. Everything else under frontend-dist/ is content-hashed and safe to let the browser
    # cache indefinitely, unlike this file.
    response.headers["Cache-Control"] = "no-cache"
    return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_connection = get_connection()
    try:
        init_db(db_connection)
    finally:
        db_connection.close()

    # Mounted here rather than at module import time so it picks up whatever TBR_DB_PATH is
    # actually configured at real startup (covers_dir() creates the directory relative to it) —
    # importing this module alone must not create directories as a side effect (e.g. under
    # pytest, before a test fixture gets a chance to set TBR_DB_PATH to a tmp path).
    app.mount("/covers", StaticFiles(directory=library_check.covers_dir()), name="covers")

    # Also registered here rather than as module-level decorators/mounts, and specifically *after*
    # /covers above: Starlette matches routes in registration order, and spa_fallback's
    # /{full_path:path} matches literally everything (including /covers/*) — decorator-based
    # routes register at import time, which would put spa_fallback *before* /covers in the list
    # and let it shadow every cover image request. Skipped entirely if frontend-dist/ doesn't
    # exist (local backend-only dev — see FRONTEND_DIST's comment above).
    if os.path.isdir(FRONTEND_DIST):
        app.mount("/_app", StaticFiles(directory=os.path.join(FRONTEND_DIST, "_app")), name="frontend-app")
        icons_dir = os.path.join(FRONTEND_DIST, "icons")
        if os.path.isdir(icons_dir):
            app.mount("/icons", StaticFiles(directory=icons_dir), name="frontend-icons")
        app.add_api_route("/{full_path:path}", spa_fallback, methods=["GET"], include_in_schema=False)

    # Always runs — the loop itself checks library_settings each cycle, so settings saved via
    # /admin/settings take effect without an app restart.
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
    """FastAPI dependency yielding one connection per request, closed once the request finishes.
    Cached by FastAPI across every `Depends(get_db)` in a single request (including indirectly via
    get_current_user/require_user below), so a request only ever opens one connection no matter
    how many routes/dependencies ask for it."""
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
    """TBR entries with books, enriched with the library "owned" flag and, where the library
    match has one, its publishedDate — when the Grimmory cross-check is configured."""
    entries = list_tbr_entries_with_books(db_connection, user_id)
    if library_check.is_configured(db_connection):
        catalog = get_library_catalog(db_connection)
        for entry in entries:
            match = library_check.find_catalog_match(
                entry.book.title, entry.book.isbn, entry.book.author, catalog
            )
            entry.owned = match is not None
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
    """Entries for one shelf — for "finished", also restricted to finished_at falling within
    the given year, matching the "Finished in {year}" label (status alone isn't enough; a
    'finished' entry from a prior year shouldn't show up here), and sorted most-recently-finished
    first rather than the default added_at-DESC ordering. "wanted" sorts by the user's own manual
    order instead (see models.py:set_wanted_order) — sort_order is never None for a live wanted
    entry once init_db's backfill has run, so this doesn't need a None-safe fallback."""
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
    """Parses a client-supplied "YYYY-MM-DD" local date, falling back to the current UTC date if
    missing/malformed. Every route that needs "today" for something the user actually sees
    (calendar highlighting/month default, book-detail's Estimated Completion/Pages-per-day,
    /api/stats' current-year default) takes this as a query param, and the frontend always sends
    the browser's own local date (see e.g. frontend/src/lib/utils/dates.ts) rather than leaving it
    to the server's UTC clock - for anyone whose local timezone is ahead of UTC (e.g. UTC+8),
    "today" per the server doesn't roll over to the viewer's actual calendar day until well into
    their morning (at UTC+8, not until 8am local), which would otherwise show yesterday's
    calendar, a stale year-boundary stats page, and completion estimates off by a day. A bad/
    stale/missing query param (older cached frontend build, direct API call) falls back to the
    previous UTC-only behavior rather than erroring."""
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return dates.today_utc()


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


def _calendar_context(db_connection, user_id: int, year: int, month: int, today: date) -> dict:
    """Shared aggregation behind GET /api/calendar."""
    entries = list_tbr_entries_with_books(db_connection, user_id)
    spans = reading_calendar.month_spans(entries, year, month, today)
    for span in spans:
        cover_color.ensure_cover_color(db_connection, span.entry.book)
    days = reading_calendar.days_active(spans, year, month)
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)

    # Calendar-specific tiles (session-approximate, computed from month_spans/days_active — not
    # derivable from a plain entry collection) first, then the same session-independent collection
    # tiles /api/stats uses, scoped to books finished in this month instead of this year — the
    # whole point of stat_tiles.build_collection_tiles taking a plain entry list is that a
    # different timeframe is just a different filter here, not new tile logic.
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
    calendar_tiles += stat_tiles.build_collection_tiles(finished_this_month, month_start, month_end)

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
# Consumed by the SvelteKit frontend in frontend/ (built to frontend-dist/ and served by
# spa_fallback above in production) — see the root CLAUDE.md for the overall architecture.
#
# /admin* routes have no in-app auth, gated at the reverse proxy instead (NPM Access List / Basic
# Auth on /admin — see SPEC.md's access-control section). That's intentional, not an oversight.


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
        finished_at=entry.finished_at,
        started_at=entry.started_at,
        started_at_manual=entry.started_at_manual,
        rating=entry.rating,
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
    # response_model is declared for OpenAPI's benefit (see frontend/src/lib/api/schema.d.ts) —
    # returning a raw JSONResponse below (needed to also set the session cookie) bypasses
    # FastAPI's automatic response_model serialization/filtering, but the documented schema still
    # comes from this declaration regardless of what the handler actually returns at runtime.
    try:
        access_token, refresh_token = grimmory_auth.login(payload.username, payload.password)
    except GrimmoryLoginError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = get_or_create_user(db_connection, payload.username)
    set_grimmory_refresh_token(db_connection, user.id, refresh_token)
    base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
    if base_url:
        try:
            library_check.sync_user_reading_status(db_connection, user.id, base_url, access_token)
        except LibraryCheckUnavailable:
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
    # year is inert for the "wanted" shelf (_shelf_label/_entries_for_shelf only branch on it for
    # "finished") - no client-supplied today needed here unlike the other _shelf_out callers.
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

    sessions = []
    base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
    if base_url and entry.book.grimmory_book_id:
        access_token = grimmory_auth.get_valid_access_token(db_connection, user)
        if access_token is not None:
            with contextlib.suppress(LibraryCheckUnavailable):
                sessions = library_check.fetch_reading_sessions_for_book(
                    base_url, access_token, entry.book.grimmory_book_id
                )

    if sessions and not entry.started_at_manual:
        derived = stat_tiles.first_meaningful_session_date(sessions)
        if derived is not None and derived.isoformat() != entry.started_at:
            set_tbr_entry_started_at(db_connection, entry.id, derived.isoformat(), manual=False)
            entry.started_at = derived.isoformat()

    tiles = stat_tiles.build_book_tiles(entry, sessions, resolved_today)
    burndown = stat_tiles.burndown_points(sessions)
    progress_percent = stat_tiles.latest_progress(sessions) if entry.status == "reading" else None
    if progress_percent is None and entry.status == "reading":
        # Session-derived progress is never available for audiobooks (Grimmory's own audiobook
        # player doesn't log endProgress on listening sessions - see
        # tbr_entries.audiobook_progress_percent in app/models.py) - fall back to the
        # independently-synced value from Grimmory's progress-update endpoint instead of just
        # showing no progress at all.
        progress_percent = entry.audiobook_progress_percent
    estimated_page = (
        round(progress_percent / 100 * entry.book.page_count)
        if progress_percent is not None and entry.book.page_count
        else None
    )

    return schemas.BookDetailOut(
        entry=_to_entry_out(entry),
        tiles=[_to_tile_out(t) for t in tiles],
        burndown=[schemas.BurndownPointOut(date=d, remaining_percent=r) for d, r in burndown],
        burndown_day_span=stat_tiles.burndown_day_span(burndown),
        progress_percent=progress_percent,
        estimated_page=estimated_page,
    )


# --- stats / calendar ---


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
    tiles = stat_tiles.build_collection_tiles(finished_this_year, date(year, 1, 1), date(year, 12, 31))
    return schemas.StatsOut(
        year=year,
        goal=_to_goal_out(goal),
        finished_count=len(finished_this_year),
        tiles=[_to_tile_out(t) for t in tiles],
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
    calendar_context = _calendar_context(db_connection, user.id, cal_year, cal_month, resolved_today)
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
            access_token, refresh_token = grimmory_auth.login(user.name, payload.password)
        except GrimmoryLoginError as exc:
            error = str(exc)
        else:
            set_grimmory_refresh_token(db_connection, user.id, refresh_token)
    else:
        access_token = grimmory_auth.get_valid_access_token(db_connection, user)
        if access_token is None:
            error = "reconnect_needed"

    if access_token is not None:
        try:
            library_check.sync_user_reading_status(db_connection, user.id, base_url, access_token)
        except LibraryCheckUnavailable as exc:
            error = str(exc)

    return schemas.SyncResultOut(error=error)


@app.get("/api/settings/shelves", response_model=schemas.ShelfOptionsOut)
def api_settings_shelves(
    user: User = Depends(require_user), db_connection: sqlite3.Connection = Depends(get_db)
):
    # Live Grimmory shelf list for the Settings-page dropdowns — deliberately its own route rather
    # than folded into GET /api/settings, so a slow/unreachable Grimmory only affects this section
    # instead of the whole settings page load.
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
    # Pure local write, no live Grimmory validation call here — if a stale/foreign shelf id ever
    # got persisted, Grimmory's own POST /api/v1/books/shelves already rejects assigning to a
    # shelf the user doesn't own, so the next sync fails loud (self-correcting once fixed) rather
    # than needing a second live round trip on every save.
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
    catalog_matches = search_library_catalog(db_connection, query)
    results = [
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
    return schemas.SearchOut(query=query, results=results)


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
        try:
            results = search_books(query)
        except httpx.HTTPError:
            error = True

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
    error = False
    results = []
    if query:
        try:
            results = search_books(query)
        except httpx.HTTPError:
            error = True
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
    # Fetch the real Grimmory cover in the background whenever we know the grimmory_id, even if
    # payload.cover_url already carried a search-result placeholder (e.g. an Open Library
    # thumbnail) - the real cover should win over that placeholder rather than waiting for the
    # next periodic sync to notice and replace it (see library_check._has_local_cover).
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
        try:
            finished_date = date.fromisoformat(finished_at_value)
        except ValueError:
            finished_date = None
        if finished_date is not None:
            set_tbr_entry_finished_at(
                db_connection, entry_id, f"{finished_date.isoformat()}T00:00:00+00:00"
            )
            base_url = os.environ.get(grimmory_auth.GRIMMORY_BASE_URL_ENV)
            book = get_book(db_connection, entry.book_id)
            if base_url and book is not None and book.grimmory_book_id is not None:
                access_token = grimmory_auth.get_valid_access_token(db_connection, user)
                if access_token is not None:
                    with contextlib.suppress(LibraryCheckUnavailable):
                        grimmory_auth.update_book_finished_date(
                            base_url, access_token, book.grimmory_book_id, finished_date
                        )
    elif entry.status == "finished":
        set_tbr_entry_finished_at(db_connection, entry_id, None)

    return _to_entry_out(_find_entry_detail(db_connection, user.id, entry_id))


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
    if library_check_enabled:
        catalog = get_library_catalog(db_connection)
        sync_state = get_library_sync_state(db_connection)

        # "In library" shows the whole catalog (not just requested-and-owned books), so
        # browsing it doesn't depend on someone having added a book to their TBR first —
        # carrying over wanted_by wherever a catalog entry does happen to also be requested.
        wanted_by_for_catalog: dict[int, list[str]] = {}
        needed_entries = []
        manual_owned_rows = []
        # id(match) of catalog entries already represented via a manual-match row below, so the
        # plain catalog pass doesn't also show them (each catalog entry appears exactly once).
        represented_catalog_ids: set[int] = set()
        for entry in aggregate_entries:
            match = library_check.resolve_catalog_match(entry.book, catalog)
            if match is None:
                needed_entries.append(_requested_row(entry))
            elif entry.book.manual_match_grimmory_id is not None:
                # Manually matched: render using the book's own stored title/author/cover — this
                # is admin-asserted certainty, not re-derived from a possibly differently-titled
                # catalog row (see _catalog_row for the auto-match path, which does use the
                # catalog row's own fields).
                row = _requested_row(entry)
                row["grimmory_id"] = match.grimmory_id
                row["manually_matched"] = True
                manual_owned_rows.append(row)
                represented_catalog_ids.add(id(match))
            else:
                wanted_by_for_catalog[id(match)] = entry.wanted_by

        catalog_rows = [
            _catalog_row(c, wanted_by_for_catalog.get(id(c), []))
            for c in catalog
            if id(c) not in represented_catalog_ids
        ]
        owned_entries = sorted(
            manual_owned_rows + catalog_rows, key=lambda row: (row["title"] or "").casefold()
        )
    return schemas.AdminOut(
        needed_entries=[schemas.AdminEntryOut(**row) for row in needed_entries],
        owned_entries=[schemas.AdminEntryOut(**row) for row in owned_entries],
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
def api_admin_library_search(q: str = "", db_connection: sqlite3.Connection = Depends(get_db)):
    # Ungated sibling of GET /api/search/library (which requires a household-user login) — the
    # match picker needs to work for an admin who isn't also logged into the app itself, matching
    # every other /api/admin/* route's reverse-proxy-gated-not-in-app-gated posture.
    query = q.strip()
    catalog_matches = search_library_catalog(db_connection, query)
    results = [
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
    return schemas.SearchOut(query=query, results=results)


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
        # Unmatch: clear the pin, then recompute grimmory_book_id from whatever the fuzzy matcher
        # says right now (or None) — never leaves a stale manually-forced id behind. Both columns
        # are written in one UPDATE (see set_book_manual_match_and_grimmory_id) so a crash between
        # them can't leave the pin cleared but the old forced id still in place, or vice versa.
        catalog = get_library_catalog(db_connection)
        fallback = library_check.find_catalog_match(book.title, book.isbn, book.author, catalog)
        set_book_manual_match_and_grimmory_id(
            db_connection, book_id, None, fallback.grimmory_id if fallback else None
        )
        return Response(status_code=204)

    owner_id = library_check.find_owning_book_id(
        db_connection, payload.grimmory_id, exclude_book_id=book_id
    )
    if owner_id is not None:
        owner = get_book(db_connection, owner_id)
        detail = f'Already matched to "{owner.title}"' if owner else "Already matched to another book"
        raise HTTPException(status_code=409, detail=detail)

    set_book_manual_match_and_grimmory_id(db_connection, book_id, payload.grimmory_id, payload.grimmory_id)
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
    resolved_password = payload.password or (existing.password if existing else None)
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
    resolved_key = payload.hardcover_api_key or (existing.hardcover_api_key if existing else None)
    set_search_settings(db_connection, hardcover_api_key=resolved_key)
    return api_admin_settings(db_connection)


@app.post("/api/admin/settings/grimmory-admin", response_model=schemas.AdminSettingsOut)
def api_admin_settings_save_grimmory_admin(
    payload: schemas.GrimmoryAdminSettingsIn,
    db_connection: sqlite3.Connection = Depends(get_db),
):
    existing = get_grimmory_admin_settings(db_connection)
    resolved_password = payload.password or (existing.password if existing else None)
    set_grimmory_admin_settings(
        db_connection, username=payload.username or None, password=resolved_password
    )
    return api_admin_settings(db_connection)
