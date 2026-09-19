"""Pydantic response models for the JSON API (see the /api/* routes in app/main.py).

Mirrors the dataclasses in app/models.py and the ad hoc dicts stat_tiles/reading_calendar return,
giving them a stable typed shape for the frontend. Secrets (grimmory_refresh_token, stored
passwords/API keys) are never included — settings responses only ever expose whether one is set."""

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
    has_paired_audiobook: Optional[bool] = None
    finished_at: Optional[str] = None
    started_at: Optional[str] = None
    started_at_manual: bool = False
    rating: Optional[int] = None
    owns_physical: bool = False
    physical_page_count: Optional[int] = None
    predicted_month: Optional[str] = None


class PhysicalReadingSessionOut(BaseModel):
    id: int
    start_time: str
    end_time: str
    start_page: int
    end_page: int


class SessionLogEntryOut(BaseModel):
    """One row of the book detail page's merged, newest-first session log - ebook/audiobook rows
    come from cached_reading_sessions (id is Grimmory's own session id), physical rows from
    physical_reading_sessions (id is that table's own id). `source` disambiguates the id space for
    the delete action, since the two are otherwise unrelated integers. `pages` is exact for
    physical (end_page - start_page), an estimate from progress_delta * book.page_count for ebook
    (same method as stat_tiles.session_page_delta), and always None for audiobook - Grimmory
    never tracks audiobook position in pages."""
    source: str  # "ebook" | "audiobook" | "physical"
    id: int
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: Optional[int] = None
    end_progress: Optional[float] = None
    progress_delta: Optional[float] = None
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    pages: Optional[int] = None


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


class StatTileGroupsOut(BaseModel):
    overview: list[StatTileOut]
    averages: list[StatTileOut]
    highlights: list[StatTileOut]


class StatsOut(BaseModel):
    year: int
    goal: Optional[GoalOut] = None
    finished_count: int
    tile_groups: StatTileGroupsOut


class BurndownPointOut(BaseModel):
    date: date
    remaining_percent: int


class BookDetailOut(BaseModel):
    entry: TBREntryOut
    # Time-spent-by-medium tiles stay split (Decision 6); audiobook_tiles is empty if unpaired.
    tiles: list[StatTileOut]
    audiobook_tiles: list[StatTileOut] = []
    # Progress and burndown are unified across every linked edition instead (Decision 5).
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
    """active/cover/bar mirror reading_calendar.DayCell's active_spans/cover_spans/bar_spans,
    computed server-side and referenced here by entry_id instead of embedding BookSpan objects.
    bar_entry_ids preserves interior None gaps — never trimmed shorter than the highest occupied
    lane + 1."""

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
    # Whether a Grimmory refresh token is stored - never the token itself.
    has_grimmory_session: bool
    # Persisted shelf ids only - GET /api/settings/shelves does the live shelf-list fetch.
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
    # "reconnect_needed" | not-configured | a LibraryCheckUnavailable message - always 200, never raised.
    error: Optional[str] = None


class ShelfSyncSettingsOut(BaseModel):
    want_to_read_shelf_id: Optional[int] = None
    sync_to_device_enabled: bool = False
    sync_to_device_shelf_id: Optional[int] = None


class SpiceResultOut(BaseModel):
    spice_level: int
    error: Optional[str] = None


class AdminEntryOut(BaseModel):
    # Set for owned_entries only when the row came from a manual match (drives Unmatch).
    id: Optional[int] = None
    title: str
    author: Optional[str] = None
    cover_url: Optional[str] = None
    wanted_by: list[str]
    # Grimmory's own catalog id - unset for needed_entries (no match yet).
    grimmory_id: Optional[int] = None
    manually_matched: bool = False
    # Set only on an audiobook_entries row that's been paired to an ebook - see AdminPairAudiobookIn.
    paired_ebook_title: Optional[str] = None


class AdminOut(BaseModel):
    needed_entries: list[AdminEntryOut]
    owned_entries: list[AdminEntryOut]
    # In-library audiobooks, split out from owned_entries (see library_check.AUDIOBOOKS_ENABLED).
    audiobook_entries: list[AdminEntryOut]
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
# JSON bodies for the /api/* routes. A blank/omitted secret field (password, API key) means
# "leave it unchanged".


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


class AdminPairAudiobookIn(BaseModel):
    ebook_grimmory_id: Optional[int] = None


class AdminMatchIn(BaseModel):
    # A Grimmory catalog id to pin this book to, or None to clear an existing manual match.
    grimmory_id: Optional[int] = None


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


class TBRPhysicalIn(BaseModel):
    owns_physical: bool


class TBRPhysicalPageCountIn(BaseModel):
    # None clears it - unlike the secret-settings "blank means unchanged" convention elsewhere,
    # this field has no other way to signal "I know it and it's actually unset" vs "leave it alone",
    # so this endpoint always overwrites with whatever's sent, no leave-unchanged semantics.
    physical_page_count: Optional[int] = None


class PhysicalReadingSessionIn(BaseModel):
    start_time: str
    end_time: str
    start_page: int
    end_page: int


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
