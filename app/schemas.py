"""Pydantic response models for the JSON API (see the /api/* routes in app/main.py).

These mirror the plain dataclasses in app/models.py and the ad hoc dicts the stat_tiles/
reading_calendar helpers already return — the API layer's job is to give those a stable, typed
shape for the frontend, not to change what they contain. Secrets (grimmory_refresh_token, stored
passwords/API keys) are deliberately never included — settings responses only ever expose whether
a secret is set, matching what the Jinja2 admin_settings.html template already showed.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class MeOut(BaseModel):
    id: int
    name: str
    view_preference: str
    calendar_view_preference: str
    onboarded: bool
    spice_level: int
    is_admin: bool


class BookOut(BaseModel):
    id: int
    title: str
    author: Optional[str] = None
    isbn: Optional[str] = None
    cover_url: Optional[str] = None
    published_date: Optional[str] = None
    page_count: Optional[int] = None
    cover_color: Optional[str] = None


class TBREntryOut(BaseModel):
    id: int
    status: str
    added_at: str
    book: BookOut
    owned: Optional[bool] = None
    finished_at: Optional[str] = None
    started_at: Optional[str] = None
    started_at_manual: bool = False
    rating: Optional[int] = None


class ShelfOut(BaseModel):
    status: str
    label: str
    entries: list[TBREntryOut]


class HomeOut(BaseModel):
    shelves: list[ShelfOut]


class GoalOut(BaseModel):
    id: int
    timeframe: str
    target_count: int


class StatTileOut(BaseModel):
    label: str
    value: str
    sub: Optional[str] = None


class StatsOut(BaseModel):
    year: int
    goal: Optional[GoalOut] = None
    finished_count: int
    tiles: list[StatTileOut]


class BurndownPointOut(BaseModel):
    date: date
    remaining_percent: int


class BookDetailOut(BaseModel):
    entry: TBREntryOut
    tiles: list[StatTileOut]
    burndown: list[BurndownPointOut]
    burndown_day_span: int
    progress_percent: Optional[float] = None
    estimated_page: Optional[int] = None


class CalendarBookOut(BaseModel):
    id: int
    title: str
    cover_url: Optional[str] = None
    cover_color: Optional[str] = None


class BookSpanOut(BaseModel):
    """One book's active date range within the requested month — the calendar's unit of data.
    DayCellOut entries below reference these by entry_id rather than embedding a copy per day, so
    the frontend builds one lookup map instead of re-parsing a book's details on every cell it
    appears in."""

    entry_id: int
    book: CalendarBookOut
    status: str
    start: date
    end: date
    lane: int


class DayCellOut(BaseModel):
    """active/cover/bar mirror reading_calendar.DayCell's active_spans/cover_spans/bar_spans —
    same precedence (declutter, milestone ranking, lane-gap None-padding) already computed
    server-side, just referencing spans by entry_id instead of embedding BookSpan objects.
    bar_entry_ids preserves interior None gaps (an unoccupied lane below a higher occupied one);
    it is never trimmed to a shorter list than the highest occupied lane + 1."""

    date: date
    in_month: bool
    is_today: bool
    is_future: bool
    active_entry_ids: list[int]
    cover_entry_ids: list[int]
    bar_entry_ids: list[Optional[int]]


class CalendarOut(BaseModel):
    year: int
    month: int
    month_label: str
    prev_month: str
    next_month: str
    calendar_view: str
    spans: list[BookSpanOut]
    grid: list[list[DayCellOut]]
    tiles: list[StatTileOut]


class SearchResultOut(BaseModel):
    title: str
    author: Optional[str] = None
    isbn: Optional[str] = None
    cover_url: Optional[str] = None
    published_date: Optional[str] = None
    grimmory_id: Optional[int] = None


class SearchOut(BaseModel):
    query: str
    results: list[SearchResultOut]
    error: bool = False
    error_message: Optional[str] = None
    show_more: bool = False


class SettingsOut(BaseModel):
    goal: Optional[GoalOut] = None
    goal_year: int
    grimmory_admin_configured: bool
    spice_labels: list[str]
    spice_level: int
    # Whether the user has a stored Grimmory refresh token (see models.User.grimmory_refresh_token)
    # — never the token itself. Settings.html's Jinja2 equivalent checked `user.grimmory_refresh_token`
    # directly to decide whether /settings/sync needs a password field; this is that same check
    # exposed as a plain boolean instead of the secret.
    has_grimmory_session: bool
    # Persisted Grimmory shelf ids only — no live Grimmory call happens for this route (see
    # GET /api/settings/shelves for the live shelf-list fetch that powers the settings dropdowns).
    want_to_read_shelf_id: Optional[int] = None
    sync_to_device_enabled: bool = False
    sync_to_device_shelf_id: Optional[int] = None


class SyncResultOut(BaseModel):
    error: Optional[str] = None


class ShelfOptionOut(BaseModel):
    id: int
    name: str


class ShelfOptionsOut(BaseModel):
    shelves: list[ShelfOptionOut]
    # "reconnect_needed" | a not-configured message | a LibraryCheckUnavailable message — same
    # soft-error convention as SyncResultOut/SpiceResultOut (always 200, never raised).
    error: Optional[str] = None


class ShelfSyncSettingsOut(BaseModel):
    want_to_read_shelf_id: Optional[int] = None
    sync_to_device_enabled: bool = False
    sync_to_device_shelf_id: Optional[int] = None


class SpiceResultOut(BaseModel):
    spice_level: int
    error: Optional[str] = None


class AdminEntryOut(BaseModel):
    title: str
    author: Optional[str] = None
    cover_url: Optional[str] = None
    wanted_by: list[str]


class AdminOut(BaseModel):
    needed_entries: list[AdminEntryOut]
    owned_entries: list[AdminEntryOut]
    library_check_enabled: bool
    last_synced_at: Optional[str] = None
    last_error: Optional[str] = None


class LibrarySettingsOut(BaseModel):
    base_url: Optional[str] = None
    username: Optional[str] = None
    password_set: bool
    sync_interval_minutes: int


class GrimmoryAdminSettingsOut(BaseModel):
    username: Optional[str] = None
    password_set: bool


class AdminSettingsOut(BaseModel):
    library_settings: Optional[LibrarySettingsOut] = None
    grimmory_admin_settings: Optional[GrimmoryAdminSettingsOut] = None
    hardcover_api_key_set: bool
    default_sync_interval_minutes: int


# --- request bodies ---
# JSON bodies for the /api/* routes, mirroring the Form(...) fields their HTML-route counterparts
# take. A blank/omitted string on a "keep current secret unless replaced" field (password, API
# key) means the same thing here as an empty Form field does today: leave it unchanged.


class LoginIn(BaseModel):
    username: str
    password: str


class OnboardingIn(BaseModel):
    target_count: Optional[int] = None


class GoalIn(BaseModel):
    target_count: int


class SyncIn(BaseModel):
    password: str = ""


class SpiceIn(BaseModel):
    level: int


class ShelfSyncSettingsIn(BaseModel):
    want_to_read_shelf_id: Optional[int] = None
    sync_to_device_enabled: bool = False
    sync_to_device_shelf_id: Optional[int] = None


class TBRCreateIn(BaseModel):
    title: str
    author: str = ""
    isbn: str = ""
    cover_url: str = ""
    published_date: str = ""
    grimmory_id: str = ""


class TBRDatesIn(BaseModel):
    started_at: str = ""
    finished_at: str = ""


class ReorderIn(BaseModel):
    """Entry ids for the "wanted" shelf in the desired order (first = top of the shelf) — see
    models.py:set_wanted_order, which scopes the update to the caller's own wanted entries only."""

    entry_ids: list[int]


class ViewPreferenceIn(BaseModel):
    view: str


class CalendarViewPreferenceIn(BaseModel):
    view: str


class LibrarySettingsIn(BaseModel):
    base_url: str = ""
    username: str = ""
    password: str = ""
    sync_interval_minutes: int = 60


class HardcoverSettingsIn(BaseModel):
    hardcover_api_key: str = ""


class GrimmoryAdminSettingsIn(BaseModel):
    username: str = ""
    password: str = ""
