"""SQLite schema and CRUD layer for users, books, and tbr_entries."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tbr.db")

SCHEMA_SQL = """
-- name is the user's Grimmory username; rows are created lazily on first login (grimmory_auth.py),
-- not seeded. view_preference/calendar_view_preference are independent 'spine'|'cover' /
-- 'grid'|'list' toggles for the home screen and the Reading Calendar respectively.
-- onboarded tracks the first-login goal-setting prompt (/onboarding).
-- grimmory_refresh_token is this user's rotating Grimmory refresh token (grimmory_auth.py:
-- get_valid_access_token); cleared to NULL on a rejected refresh, so its presence means "believed
-- valid," not "guaranteed valid." spice_level (0-5) is the user's chili-pepper content-rating ceiling.
-- want_to_read_shelf_id / sync_to_device_shelf_id are Grimmory's own numeric shelf ids (not local
-- foreign keys) backing the "Want to Read" mirror and the opt-in "Sync to Device" KOReader shelf
-- (see library_check.py's sync_user_reading_status); NULL until resolved.
-- sync_to_device_enabled is the opt-in flag for that second shelf (default off); its shelf id is
-- kept even while disabled so re-enabling doesn't need to re-resolve it.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    view_preference TEXT NOT NULL DEFAULT 'spine',
    onboarded INTEGER NOT NULL DEFAULT 0,
    grimmory_refresh_token TEXT,
    spice_level INTEGER NOT NULL DEFAULT 0,
    calendar_view_preference TEXT NOT NULL DEFAULT 'grid',
    want_to_read_shelf_id INTEGER,
    sync_to_device_enabled INTEGER NOT NULL DEFAULT 0,
    sync_to_device_shelf_id INTEGER
);

-- page_count / grimmory_book_id / cover_color / format are always overwritten from Grimmory on
-- sync, never user-editable. grimmory_book_id is needed to fetch this book's reading sessions by
-- Grimmory's id (stat_tiles.py); NULL until matched (library_check.py:_sync_book_metadata).
-- cover_color is a sampled "#rrggbb" average used for Reading Calendar bars/swatches
-- (cover_color.py:ensure_cover_color); NULL until first computed, or forever if the cover can't be
-- decoded (callers fall back to a palette). format is Grimmory's primaryFile.bookType
-- ("EPUB"/"AUDIOBOOK"/...), used to label session tiles by media type.
-- manual_match_grimmory_id is an admin override (POST /api/admin/books/{id}/match) that takes
-- priority over the fuzzy matcher in library_check.resolve_catalog_match; stores
-- library_catalog.grimmory_id (stable across catalog rebuilds), never library_catalog.id (that
-- table is rebuilt from scratch on every sync). NULL means no override.
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    isbn TEXT UNIQUE,
    cover_url TEXT,
    published_date TEXT,
    page_count INTEGER,
    grimmory_book_id INTEGER,
    cover_color TEXT,
    manual_match_grimmory_id INTEGER,
    format TEXT
);

-- status is 'wanted'|'reading'|'finished', driven by the Grimmory sync
-- (library_check.sync_user_reading_status) or set directly when adding a book onto a shelf.
-- finished_at comes from Grimmory's dateFinished (falls back to sync time), buckets the "Finished
-- in {year}" shelf, and is user-editable (POST /tbr/{id}/dates) — edits are best-effort pushed
-- back to Grimmory (grimmory_auth.py:update_book_finished_date).
-- started_at is a local-only YYYY-MM-DD date (Grimmory has no such field): auto-set on first
-- 'reading', refined from the first real session once one exists, and directly user-editable.
-- started_at_manual marks a real user edit so it's never auto-overwritten, distinguishing it from
-- an auto-guess. rating mirrors Grimmory's personalRating; always overwritten, not user-editable.
-- sort_order is the user's manual ordering, meaningful only for status='wanted' (reading/finished
-- order by added_at/finished_at instead); NULL until backfilled, never NULL afterward for a live
-- wanted entry. audiobook_progress_percent mirrors Grimmory's Book.audiobookProgress.percentage —
-- a fallback used when session data is empty, since Grimmory never populates session progress
-- deltas for audiobooks. owns_physical is per-user (never sourced from Grimmory's own physical
-- tag, which is catalog-wide across every account — confirmed empirically), set via
-- POST /tbr/{id}/physical. physical_page_count is this printing's own page count, separate from
-- books.page_count; editing it reshapes past physical sessions' computed percentages, an accepted
-- tradeoff (stat_tiles.physical_session_to_grimmory_shape).
CREATE TABLE IF NOT EXISTS tbr_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    book_id INTEGER NOT NULL REFERENCES books(id),
    status TEXT NOT NULL DEFAULT 'wanted',
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    started_at TEXT,
    started_at_manual INTEGER NOT NULL DEFAULT 0,
    rating INTEGER,
    sort_order INTEGER,
    audiobook_progress_percent REAL,
    owns_physical INTEGER NOT NULL DEFAULT 0,
    physical_page_count INTEGER,
    UNIQUE(user_id, book_id)
);

-- Manually-logged physical reading sessions (DESIGN-multi-edition-refactor.md Decisions 7-9),
-- keyed by start_page/end_page rather than a percentage. Converted to a Grimmory-shaped session
-- dict at read time using tbr_entries.physical_page_count (stat_tiles.py:
-- physical_session_to_grimmory_shape), so an edit to either never needs a separate recompute step.
-- Entry-id-keyed (per-user), unlike linked_editions below — physical ownership isn't a catalog fact.
CREATE TABLE IF NOT EXISTS physical_reading_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES tbr_entries(id) ON DELETE CASCADE,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    start_page INTEGER NOT NULL,
    end_page INTEGER NOT NULL
);

-- Local cache of the Grimmory catalog, refreshed by app.library_check.
-- format mirrors books.format — lets the admin "In library" list split off audiobooks without a
-- per-book lookup.
CREATE TABLE IF NOT EXISTS library_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    isbn13 TEXT,
    isbn10 TEXT,
    authors TEXT,
    published_date TEXT,
    grimmory_id INTEGER,
    format TEXT
);

-- Manual admin-asserted pairing between an audiobook and its ebook counterpart, both left as
-- separate Grimmory books (see AUDIOBOOKS_ENABLED in library_check.py). Bookkeeping for the admin
-- UI only — reading status/stats still come only from the ebook's own Grimmory data. Keyed by
-- Grimmory's own catalog ids (stable across catalog rebuilds), never library_catalog.id.
-- DEPRECATED as of DESIGN-multi-edition-refactor.md Phase 1, superseded by linked_editions below.
-- Kept dual-written to (main.py:api_admin_pair_audiobook) only for the Phase 1-3 transition window.
CREATE TABLE IF NOT EXISTS audiobook_pairings (
    audiobook_grimmory_id INTEGER PRIMARY KEY,
    ebook_grimmory_id INTEGER NOT NULL
);

-- Generalized replacement for audiobook_pairings — one row per non-ebook "linked edition" of a
-- book (audiobook today, physical planned). Catalog-id-keyed like audiobook_pairings was, not
-- local book_id-keyed, since an admin can pair an edition before any user has shelved the book.
-- edition_grimmory_id is this edition's own catalog id (PRIMARY KEY, one ebook per edition).
-- ebook_grimmory_id is the anchor it's linked to; format distinguishes the edition kind
-- ('AUDIOBOOK' today). UNIQUE(ebook_grimmory_id, format) caps it at one linked edition of a given
-- format per ebook. Physical ownership does NOT live here — see tbr_entries.owns_physical instead,
-- since it's a per-user fact, not a catalog one.
CREATE TABLE IF NOT EXISTS linked_editions (
    edition_grimmory_id INTEGER PRIMARY KEY,
    ebook_grimmory_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    UNIQUE(ebook_grimmory_id, format)
);

-- Single-row table tracking the last catalog sync attempt.
CREATE TABLE IF NOT EXISTS library_sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_synced_at TEXT,
    last_error TEXT
);

-- Single-row table holding the Grimmory connection settings, editable from /admin/settings.
CREATE TABLE IF NOT EXISTS library_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    base_url TEXT,
    username TEXT,
    password TEXT,
    sync_interval_minutes INTEGER NOT NULL DEFAULT 60
);

-- Single-row table holding book-search provider settings, editable from /admin/settings.
-- Separate from library_settings — this is about metadata search when adding a book, not the
-- library cross-check sync.
CREATE TABLE IF NOT EXISTS search_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    hardcover_api_key TEXT
);

-- Single-row table holding a second, *admin-privileged* Grimmory account, editable from
-- /admin/settings — separate from library_settings' dedicated read-only sync account. Used by
-- app/grimmory_auth.py for the handful of actions that need Grimmory admin rights on a user's
-- behalf (managing content restrictions for the spice scale) — never persisted as a token, only
-- this username/password, and a fresh admin login happens on every call (see
-- app/grimmory_auth.py).
CREATE TABLE IF NOT EXISTS grimmory_admin_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT,
    password TEXT
);

-- One row per (user, timeframe). v1 only ever creates 'year' rows (see SPEC.md > Goals — month/
-- week are a deferred follow-up); target_count is edited in place, since progress is computed
-- live from tbr_entries.finished_at rather than stored on the goal.
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    timeframe TEXT NOT NULL DEFAULT 'year',
    target_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, timeframe)
);

-- Local mirror of a book's Grimmory reading sessions (app.library_check.
-- get_or_fetch_reading_sessions), so a "finished" entry never needs to re-fetch/re-page its whole
-- session history on every book/stats page load. book_type is 'EBOOK'|'AUDIOBOOK' - which of the
-- entry's linked editions this session belongs to, since both share one entry_id (paired
-- audiobooks push status onto their ebook's entry, never their own). grimmory_session_id is
-- Grimmory's own session id, used to dedupe on import - never re-inserted once seen, so a
-- resync can't silently resurrect a tombstoned row. deleted_at is a soft-delete: Grimmory has no
-- API to edit/delete a session (confirmed empirically - bad sessions have needed direct DB
-- surgery on Grimmory's own database in the past), so BooKnook can only exclude a bad session
-- locally, never fix it upstream; NULL means active. Sessions are otherwise immutable once
-- imported - Grimmory has no edit endpoint either, so existing rows are never updated, only added.
CREATE TABLE IF NOT EXISTS cached_reading_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES tbr_entries(id) ON DELETE CASCADE,
    grimmory_session_id INTEGER NOT NULL,
    book_type TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration_seconds INTEGER,
    end_progress REAL,
    progress_delta REAL,
    deleted_at TEXT,
    UNIQUE(entry_id, grimmory_session_id, book_type)
);
"""

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = os.environ.get("TBR_DB_PATH", DEFAULT_DB_PATH)
    # check_same_thread=False: FastAPI's generator dependencies (see get_db below) can run their
    # setup and teardown on different threadpool worker threads for the same request.
    db_connection = sqlite3.connect(db_path, check_same_thread=False)
    db_connection.row_factory = sqlite3.Row
    db_connection.execute("PRAGMA foreign_keys = ON")
    return db_connection


def _add_column_if_missing(conn: sqlite3.Connection, alter_sql: str) -> None:
    try:
        conn.execute(alter_sql)
    except sqlite3.OperationalError:
        pass  # column already exists


def init_db(db_connection: sqlite3.Connection) -> None:
    db_connection.executescript(SCHEMA_SQL)
    # Lightweight in-place migration for databases created before these columns existed —
    # SCHEMA_SQL's CREATE TABLE IF NOT EXISTS won't add them to an already-existing table. Add a
    # new call here (never edit an old one) when adding a column to an existing table.
    _add_column_if_missing(db_connection, "ALTER TABLE users ADD COLUMN view_preference TEXT NOT NULL DEFAULT 'spine'")
    _add_column_if_missing(db_connection, "ALTER TABLE books ADD COLUMN published_date TEXT")
    _add_column_if_missing(db_connection, "ALTER TABLE library_catalog ADD COLUMN published_date TEXT")
    _add_column_if_missing(db_connection, "ALTER TABLE users ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(db_connection, "ALTER TABLE tbr_entries ADD COLUMN finished_at TEXT")
    _add_column_if_missing(db_connection, "ALTER TABLE tbr_entries ADD COLUMN started_at TEXT")
    _add_column_if_missing(db_connection, "ALTER TABLE books ADD COLUMN page_count INTEGER")
    _add_column_if_missing(db_connection, "ALTER TABLE tbr_entries ADD COLUMN rating INTEGER")
    _add_column_if_missing(db_connection, "ALTER TABLE users ADD COLUMN grimmory_refresh_token TEXT")
    _add_column_if_missing(db_connection, "ALTER TABLE users ADD COLUMN spice_level INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(db_connection, "ALTER TABLE books ADD COLUMN grimmory_book_id INTEGER")
    _add_column_if_missing(
        db_connection, "ALTER TABLE tbr_entries ADD COLUMN started_at_manual INTEGER NOT NULL DEFAULT 0"
    )
    _add_column_if_missing(
        db_connection, "ALTER TABLE users ADD COLUMN calendar_view_preference TEXT NOT NULL DEFAULT 'grid'"
    )
    _add_column_if_missing(db_connection, "ALTER TABLE books ADD COLUMN cover_color TEXT")
    _add_column_if_missing(db_connection, "ALTER TABLE library_catalog ADD COLUMN grimmory_id INTEGER")
    _add_column_if_missing(db_connection, "ALTER TABLE users ADD COLUMN want_to_read_shelf_id INTEGER")
    _add_column_if_missing(
        db_connection, "ALTER TABLE users ADD COLUMN sync_to_device_enabled INTEGER NOT NULL DEFAULT 0"
    )
    _add_column_if_missing(db_connection, "ALTER TABLE users ADD COLUMN sync_to_device_shelf_id INTEGER")
    # KOReader self-service sync removed 2026-07-29. A DB that already dropped these columns
    # raises "no such column" here, not OperationalError, so that's caught too.
    for column in ("koreader_username", "koreader_sync_enabled"):
        try:
            db_connection.execute(f"ALTER TABLE users DROP COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # already dropped, or never existed on a fresh database
    _add_column_if_missing(db_connection, "ALTER TABLE tbr_entries ADD COLUMN sort_order INTEGER")
    _add_column_if_missing(db_connection, "ALTER TABLE books ADD COLUMN manual_match_grimmory_id INTEGER")
    _add_column_if_missing(db_connection, "ALTER TABLE tbr_entries ADD COLUMN audiobook_progress_percent REAL")
    _add_column_if_missing(db_connection, "ALTER TABLE books ADD COLUMN format TEXT")
    _add_column_if_missing(db_connection, "ALTER TABLE library_catalog ADD COLUMN format TEXT")
    _add_column_if_missing(
        db_connection, "ALTER TABLE tbr_entries ADD COLUMN owns_physical INTEGER NOT NULL DEFAULT 0"
    )
    _add_column_if_missing(db_connection, "ALTER TABLE tbr_entries ADD COLUMN physical_page_count INTEGER")
    # One-time backfill of linked_editions from audiobook_pairings (DESIGN-multi-edition-refactor.md
    # Phase 1) - INSERT OR IGNORE so re-running init_db never duplicates a row already backfilled or
    # since written directly to linked_editions by the Phase 1 dual-write.
    db_connection.execute(
        """
        INSERT OR IGNORE INTO linked_editions (edition_grimmory_id, ebook_grimmory_id, format)
        SELECT audiobook_grimmory_id, ebook_grimmory_id, 'AUDIOBOOK' FROM audiobook_pairings
        """
    )
    # A legacy row that collided with the UNIQUE(ebook_grimmory_id, format) constraint above (an
    # ebook with two audiobooks already paired to it) got silently dropped from linked_editions -
    # remove it here too so both tables agree on the one pairing that survived.
    db_connection.execute(
        """
        DELETE FROM audiobook_pairings
        WHERE audiobook_grimmory_id NOT IN (
            SELECT edition_grimmory_id FROM linked_editions WHERE format = 'AUDIOBOOK'
        )
        """
    )
    # One-time backfill for wanted entries that predate sort_order, preserving today's added_at
    # order. Only touches NULL rows, so it's a no-op after the first init_db() call.
    db_connection.execute(
        """
        UPDATE tbr_entries
        SET sort_order = ranked.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY added_at DESC) - 1 AS rn
            FROM tbr_entries
            WHERE status = 'wanted' AND sort_order IS NULL
        ) AS ranked
        WHERE tbr_entries.id = ranked.id
        """
    )
    db_connection.commit()


@dataclass
class User:
    id: int
    name: str
    view_preference: str = "spine"
    onboarded: bool = False
    grimmory_refresh_token: Optional[str] = None
    spice_level: int = 0
    calendar_view_preference: str = "grid"
    want_to_read_shelf_id: Optional[int] = None
    sync_to_device_enabled: bool = False
    sync_to_device_shelf_id: Optional[int] = None


@dataclass
class Book:
    id: int
    title: str
    author: Optional[str]
    isbn: Optional[str]
    cover_url: Optional[str]
    published_date: Optional[str] = None
    page_count: Optional[int] = None
    grimmory_book_id: Optional[int] = None
    cover_color: Optional[str] = None
    manual_match_grimmory_id: Optional[int] = None
    format: Optional[str] = None


@dataclass
class TBREntry:
    id: int
    user_id: int
    book_id: int
    status: str
    added_at: str
    finished_at: Optional[str] = None
    started_at: Optional[str] = None
    started_at_manual: bool = False
    rating: Optional[int] = None
    sort_order: Optional[int] = None
    audiobook_progress_percent: Optional[float] = None
    owns_physical: bool = False
    physical_page_count: Optional[int] = None


@dataclass
class TBREntryDetail:
    id: int
    status: str
    added_at: str
    book: Book
    owned: Optional[bool] = None
    # Drives the "Audiobook available" badge (app.models.audiobook_pairings). None when `owned`
    # is also None (library-check unconfigured).
    has_paired_audiobook: Optional[bool] = None
    finished_at: Optional[str] = None
    started_at: Optional[str] = None
    started_at_manual: bool = False
    rating: Optional[int] = None
    sort_order: Optional[int] = None
    audiobook_progress_percent: Optional[float] = None
    owns_physical: bool = False
    physical_page_count: Optional[int] = None


@dataclass
class PhysicalReadingSession:
    id: int
    entry_id: int
    start_time: str
    end_time: str
    start_page: int
    end_page: int


@dataclass
class AggregateTBREntry:
    book: Book
    wanted_by: list[str]
    owned: Optional[bool] = None


@dataclass
class LibraryCatalogEntry:
    title: str
    isbn13: Optional[str]
    isbn10: Optional[str]
    authors: list[str] = field(default_factory=list)
    published_date: Optional[str] = None
    grimmory_id: Optional[int] = None
    format: Optional[str] = None


@dataclass
class LinkedEdition:
    edition_grimmory_id: int
    ebook_grimmory_id: int
    format: str


@dataclass
class LibrarySyncState:
    last_synced_at: Optional[str]
    last_error: Optional[str]


@dataclass
class LibrarySettings:
    base_url: Optional[str]
    username: Optional[str]
    password: Optional[str]
    sync_interval_minutes: int


@dataclass
class SearchSettings:
    hardcover_api_key: Optional[str]


@dataclass
class GrimmoryAdminSettings:
    username: Optional[str]
    password: Optional[str]


@dataclass
class Goal:
    id: int
    user_id: int
    timeframe: str
    target_count: int
    created_at: str


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        name=row["name"],
        view_preference=row["view_preference"],
        onboarded=bool(row["onboarded"]),
        grimmory_refresh_token=row["grimmory_refresh_token"],
        spice_level=row["spice_level"],
        calendar_view_preference=row["calendar_view_preference"],
        want_to_read_shelf_id=row["want_to_read_shelf_id"],
        sync_to_device_enabled=bool(row["sync_to_device_enabled"]),
        sync_to_device_shelf_id=row["sync_to_device_shelf_id"],
    )


def _row_to_book(row: sqlite3.Row, id_column: str = "id") -> Book:
    # id_column supports a joined query that had to alias books.id, e.g. "AS book_id".
    return Book(
        id=row[id_column],
        title=row["title"],
        author=row["author"],
        isbn=row["isbn"],
        cover_url=row["cover_url"],
        published_date=row["published_date"],
        page_count=row["page_count"],
        grimmory_book_id=row["grimmory_book_id"],
        cover_color=row["cover_color"],
        manual_match_grimmory_id=row["manual_match_grimmory_id"],
        format=row["format"],
    )


def _row_to_tbr_entry(row: sqlite3.Row) -> TBREntry:
    return TBREntry(
        id=row["id"],
        user_id=row["user_id"],
        book_id=row["book_id"],
        status=row["status"],
        added_at=row["added_at"],
        finished_at=row["finished_at"],
        started_at=row["started_at"],
        started_at_manual=bool(row["started_at_manual"]),
        rating=row["rating"],
        sort_order=row["sort_order"],
        audiobook_progress_percent=row["audiobook_progress_percent"],
        owns_physical=bool(row["owns_physical"]),
        physical_page_count=row["physical_page_count"],
    )


# --- users ---

def list_users(db_connection: sqlite3.Connection) -> list[User]:
    rows = db_connection.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [_row_to_user(r) for r in rows]


def get_user(db_connection: sqlite3.Connection, user_id: int) -> Optional[User]:
    row = db_connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_name(db_connection: sqlite3.Connection, name: str) -> Optional[User]:
    row = db_connection.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    return _row_to_user(row) if row else None


def create_user(db_connection: sqlite3.Connection, name: str) -> User:
    cur = db_connection.execute("INSERT INTO users (name) VALUES (?)", (name,))
    db_connection.commit()
    return get_user(db_connection, cur.lastrowid)


def get_or_create_user(db_connection: sqlite3.Connection, name: str) -> User:
    """Looks up a user by their Grimmory username, creating a local record on first login."""
    existing = get_user_by_name(db_connection, name)
    if existing:
        return existing
    return create_user(db_connection, name)


def set_view_preference(db_connection: sqlite3.Connection, user_id: int, view: str) -> None:
    db_connection.execute("UPDATE users SET view_preference = ? WHERE id = ?", (view, user_id))
    db_connection.commit()


def set_calendar_view_preference(db_connection: sqlite3.Connection, user_id: int, view: str) -> None:
    db_connection.execute(
        "UPDATE users SET calendar_view_preference = ? WHERE id = ?", (view, user_id)
    )
    db_connection.commit()


def set_onboarded(db_connection: sqlite3.Connection, user_id: int) -> None:
    db_connection.execute("UPDATE users SET onboarded = 1 WHERE id = ?", (user_id,))
    db_connection.commit()


def set_grimmory_refresh_token(
    db_connection: sqlite3.Connection, user_id: int, refresh_token: Optional[str]
) -> None:
    """Set to a new value on every successful login/refresh (Grimmory rotates refresh tokens on
    each use — see app/grimmory_auth.py), or cleared to None once a refresh attempt is rejected,
    so its presence means "believed valid," not "guaranteed valid until proven otherwise"."""
    db_connection.execute(
        "UPDATE users SET grimmory_refresh_token = ? WHERE id = ?", (refresh_token, user_id)
    )
    db_connection.commit()


def set_spice_level(db_connection: sqlite3.Connection, user_id: int, spice_level: int) -> None:
    db_connection.execute("UPDATE users SET spice_level = ? WHERE id = ?", (spice_level, user_id))
    db_connection.commit()


def set_want_to_read_shelf_id(
    db_connection: sqlite3.Connection, user_id: int, shelf_id: Optional[int]
) -> None:
    db_connection.execute(
        "UPDATE users SET want_to_read_shelf_id = ? WHERE id = ?", (shelf_id, user_id)
    )
    db_connection.commit()


def set_sync_to_device_enabled(db_connection: sqlite3.Connection, user_id: int, enabled: bool) -> None:
    db_connection.execute(
        "UPDATE users SET sync_to_device_enabled = ? WHERE id = ?", (int(enabled), user_id)
    )
    db_connection.commit()


def set_sync_to_device_shelf_id(
    db_connection: sqlite3.Connection, user_id: int, shelf_id: Optional[int]
) -> None:
    db_connection.execute(
        "UPDATE users SET sync_to_device_shelf_id = ? WHERE id = ?", (shelf_id, user_id)
    )
    db_connection.commit()


# --- books ---

def get_book(db_connection: sqlite3.Connection, book_id: int) -> Optional[Book]:
    row = db_connection.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return _row_to_book(row) if row else None


def get_book_by_isbn(db_connection: sqlite3.Connection, isbn: str) -> Optional[Book]:
    row = db_connection.execute("SELECT * FROM books WHERE isbn = ?", (isbn,)).fetchone()
    return _row_to_book(row) if row else None


def list_books(db_connection: sqlite3.Connection) -> list[Book]:
    rows = db_connection.execute("SELECT * FROM books ORDER BY id").fetchall()
    return [_row_to_book(r) for r in rows]


def create_book(
    db_connection: sqlite3.Connection,
    title: str,
    author: Optional[str] = None,
    isbn: Optional[str] = None,
    cover_url: Optional[str] = None,
    published_date: Optional[str] = None,
) -> Book:
    """Creates a book, or returns the existing one if the ISBN already exists."""
    if isbn:
        existing = get_book_by_isbn(db_connection, isbn)
        if existing:
            return existing
    cur = db_connection.execute(
        "INSERT INTO books (title, author, isbn, cover_url, published_date) VALUES (?, ?, ?, ?, ?)",
        (title, author, isbn, cover_url, published_date),
    )
    db_connection.commit()
    return get_book(db_connection, cur.lastrowid)


# --- tbr_entries ---

def get_tbr_entry(db_connection: sqlite3.Connection, entry_id: int) -> Optional[TBREntry]:
    row = db_connection.execute("SELECT * FROM tbr_entries WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_tbr_entry(row) if row else None


def list_tbr_entries_for_user(db_connection: sqlite3.Connection, user_id: int) -> list[TBREntry]:
    rows = db_connection.execute(
        "SELECT * FROM tbr_entries WHERE user_id = ? ORDER BY added_at DESC", (user_id,)
    ).fetchall()
    return [_row_to_tbr_entry(r) for r in rows]


def list_all_tbr_entries(db_connection: sqlite3.Connection) -> list[TBREntry]:
    rows = db_connection.execute("SELECT * FROM tbr_entries ORDER BY added_at DESC").fetchall()
    return [_row_to_tbr_entry(r) for r in rows]


def list_tbr_entries_with_books(db_connection: sqlite3.Connection, user_id: int) -> list[TBREntryDetail]:
    rows = db_connection.execute(
        """
        SELECT tbr_entries.id AS entry_id, tbr_entries.status, tbr_entries.added_at,
               tbr_entries.finished_at, tbr_entries.started_at, tbr_entries.started_at_manual,
               tbr_entries.rating, tbr_entries.sort_order, tbr_entries.audiobook_progress_percent,
               tbr_entries.owns_physical, tbr_entries.physical_page_count,
               books.id AS book_id, books.title, books.author, books.isbn, books.cover_url,
               books.published_date, books.page_count, books.grimmory_book_id, books.cover_color,
               books.manual_match_grimmory_id, books.format
        FROM tbr_entries
        JOIN books ON books.id = tbr_entries.book_id
        WHERE tbr_entries.user_id = ?
        ORDER BY tbr_entries.added_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [
        TBREntryDetail(
            id=row["entry_id"],
            status=row["status"],
            added_at=row["added_at"],
            finished_at=row["finished_at"],
            started_at=row["started_at"],
            started_at_manual=bool(row["started_at_manual"]),
            rating=row["rating"],
            sort_order=row["sort_order"],
            audiobook_progress_percent=row["audiobook_progress_percent"],
            owns_physical=bool(row["owns_physical"]),
            physical_page_count=row["physical_page_count"],
            book=_row_to_book(row, id_column="book_id"),
        )
        for row in rows
    ]


def list_aggregate_tbr(db_connection: sqlite3.Connection) -> list[AggregateTBREntry]:
    """All TBR entries across every user, deduplicated by book, with who wants each."""
    rows = db_connection.execute(
        """
        SELECT books.id AS book_id, books.title, books.author, books.isbn, books.cover_url,
               books.published_date, books.page_count, books.grimmory_book_id, books.cover_color,
               books.manual_match_grimmory_id, books.format, users.name AS user_name
        FROM tbr_entries
        JOIN books ON books.id = tbr_entries.book_id
        JOIN users ON users.id = tbr_entries.user_id
        ORDER BY books.title COLLATE NOCASE, users.name COLLATE NOCASE
        """
    ).fetchall()

    entries: dict[int, AggregateTBREntry] = {}
    for row in rows:
        book_id = row["book_id"]
        if book_id not in entries:
            entries[book_id] = AggregateTBREntry(book=_row_to_book(row, id_column="book_id"), wanted_by=[])
        entries[book_id].wanted_by.append(row["user_name"])
    return list(entries.values())


def add_tbr_entry(db_connection: sqlite3.Connection, user_id: int, book_id: int, status: str = "wanted") -> TBREntry:
    # New wanted entries go to the bottom of the manually-ordered shelf (highest sort_order last).
    sort_order = None
    if status == "wanted":
        row = db_connection.execute(
            "SELECT MAX(sort_order) AS max_order FROM tbr_entries WHERE user_id = ? AND status = 'wanted'",
            (user_id,),
        ).fetchone()
        sort_order = (row["max_order"] + 1) if row["max_order"] is not None else 0

    cur = db_connection.execute(
        "INSERT OR IGNORE INTO tbr_entries (user_id, book_id, status, sort_order) VALUES (?, ?, ?, ?)",
        (user_id, book_id, status, sort_order),
    )
    db_connection.commit()
    if cur.lastrowid and cur.rowcount:
        return get_tbr_entry(db_connection, cur.lastrowid)
    row = db_connection.execute(
        "SELECT * FROM tbr_entries WHERE user_id = ? AND book_id = ?", (user_id, book_id)
    ).fetchone()
    return _row_to_tbr_entry(row)


def remove_tbr_entry(db_connection: sqlite3.Connection, entry_id: int) -> None:
    db_connection.execute("DELETE FROM tbr_entries WHERE id = ?", (entry_id,))
    db_connection.commit()


def set_tbr_entry_status(
    db_connection: sqlite3.Connection, entry_id: int, status: str, finished_at: Optional[str] = None
) -> None:
    db_connection.execute(
        "UPDATE tbr_entries SET status = ?, finished_at = ? WHERE id = ?",
        (status, finished_at, entry_id),
    )
    db_connection.commit()


def set_tbr_entry_started_at(
    db_connection: sqlite3.Connection, entry_id: int, started_at: Optional[str], manual: bool = False
) -> None:
    db_connection.execute(
        "UPDATE tbr_entries SET started_at = ?, started_at_manual = ? WHERE id = ?",
        (started_at, int(manual), entry_id),
    )
    db_connection.commit()


def set_tbr_entry_finished_at(
    db_connection: sqlite3.Connection, entry_id: int, finished_at: Optional[str]
) -> None:
    db_connection.execute(
        "UPDATE tbr_entries SET finished_at = ? WHERE id = ?", (finished_at, entry_id)
    )
    db_connection.commit()


def set_wanted_order(db_connection: sqlite3.Connection, user_id: int, entry_ids: list[int]) -> None:
    """Sets sort_order for each id in entry_ids to its index in that list (0 = first). Scoped to
    `WHERE user_id = ? AND status = 'wanted'` so a request can't reorder another user's entries or
    silently repurpose this to reorder a reading/finished entry, which ignores sort_order entirely
    (see SCHEMA_SQL's comment on the column)."""
    db_connection.executemany(
        "UPDATE tbr_entries SET sort_order = ? WHERE id = ? AND user_id = ? AND status = 'wanted'",
        [(index, entry_id, user_id) for index, entry_id in enumerate(entry_ids)],
    )
    db_connection.commit()


def set_book_cover_url(db_connection: sqlite3.Connection, book_id: int, cover_url: str) -> None:
    db_connection.execute("UPDATE books SET cover_url = ? WHERE id = ?", (cover_url, book_id))
    db_connection.commit()


def set_book_cover_color(db_connection: sqlite3.Connection, book_id: int, cover_color: str) -> None:
    db_connection.execute("UPDATE books SET cover_color = ? WHERE id = ?", (cover_color, book_id))
    db_connection.commit()


def covers_dir() -> str:
    db_path = os.environ.get("TBR_DB_PATH", DEFAULT_DB_PATH)
    path = os.path.join(os.path.dirname(db_path), "covers")
    os.makedirs(path, exist_ok=True)
    return path


def set_book_page_count(db_connection: sqlite3.Connection, book_id: int, page_count: Optional[int]) -> None:
    db_connection.execute("UPDATE books SET page_count = ? WHERE id = ?", (page_count, book_id))
    db_connection.commit()


def set_book_format(db_connection: sqlite3.Connection, book_id: int, book_format: Optional[str]) -> None:
    db_connection.execute("UPDATE books SET format = ? WHERE id = ?", (book_format, book_id))
    db_connection.commit()


def set_book_grimmory_id(
    db_connection: sqlite3.Connection, book_id: int, grimmory_book_id: Optional[int]
) -> None:
    db_connection.execute(
        "UPDATE books SET grimmory_book_id = ? WHERE id = ?", (grimmory_book_id, book_id)
    )
    db_connection.commit()


def set_book_manual_match_grimmory_id(
    db_connection: sqlite3.Connection, book_id: int, grimmory_book_id: Optional[int]
) -> None:
    db_connection.execute(
        "UPDATE books SET manual_match_grimmory_id = ? WHERE id = ?", (grimmory_book_id, book_id)
    )
    db_connection.commit()


def set_book_manual_match_and_grimmory_id(
    db_connection: sqlite3.Connection,
    book_id: int,
    manual_match_grimmory_id: Optional[int],
    grimmory_book_id: Optional[int],
) -> None:
    """Sets both columns in a single UPDATE + commit — used by POST /api/admin/books/{id}/match
    (match and unmatch) so the pin and its immediate effect on grimmory_book_id can never be left
    inconsistent by a crash between two separate writes."""
    db_connection.execute(
        "UPDATE books SET manual_match_grimmory_id = ?, grimmory_book_id = ? WHERE id = ?",
        (manual_match_grimmory_id, grimmory_book_id, book_id),
    )
    db_connection.commit()


def set_tbr_entry_rating(db_connection: sqlite3.Connection, entry_id: int, rating: Optional[int]) -> None:
    db_connection.execute("UPDATE tbr_entries SET rating = ? WHERE id = ?", (rating, entry_id))
    db_connection.commit()


def set_tbr_entry_audiobook_progress_percent(
    db_connection: sqlite3.Connection, entry_id: int, percent: Optional[float]
) -> None:
    db_connection.execute(
        "UPDATE tbr_entries SET audiobook_progress_percent = ? WHERE id = ?", (percent, entry_id)
    )
    db_connection.commit()


def set_tbr_entry_owns_physical(db_connection: sqlite3.Connection, entry_id: int, owns_physical: bool) -> None:
    db_connection.execute(
        "UPDATE tbr_entries SET owns_physical = ? WHERE id = ?", (int(owns_physical), entry_id)
    )
    db_connection.commit()


def set_tbr_entry_physical_page_count(
    db_connection: sqlite3.Connection, entry_id: int, physical_page_count: Optional[int]
) -> None:
    db_connection.execute(
        "UPDATE tbr_entries SET physical_page_count = ? WHERE id = ?", (physical_page_count, entry_id)
    )
    db_connection.commit()


# --- physical_reading_sessions ---


def _row_to_physical_reading_session(row: sqlite3.Row) -> PhysicalReadingSession:
    return PhysicalReadingSession(
        id=row["id"],
        entry_id=row["entry_id"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        start_page=row["start_page"],
        end_page=row["end_page"],
    )


def add_physical_reading_session(
    db_connection: sqlite3.Connection,
    entry_id: int,
    start_time: str,
    end_time: str,
    start_page: int,
    end_page: int,
) -> PhysicalReadingSession:
    cur = db_connection.execute(
        """
        INSERT INTO physical_reading_sessions (entry_id, start_time, end_time, start_page, end_page)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, start_time, end_time, start_page, end_page),
    )
    db_connection.commit()
    return get_physical_reading_session(db_connection, cur.lastrowid)


def get_physical_reading_session(
    db_connection: sqlite3.Connection, session_id: int
) -> Optional[PhysicalReadingSession]:
    row = db_connection.execute(
        "SELECT * FROM physical_reading_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return _row_to_physical_reading_session(row) if row else None


def list_physical_reading_sessions(
    db_connection: sqlite3.Connection, entry_id: int
) -> list[PhysicalReadingSession]:
    rows = db_connection.execute(
        "SELECT * FROM physical_reading_sessions WHERE entry_id = ? ORDER BY start_time",
        (entry_id,),
    ).fetchall()
    return [_row_to_physical_reading_session(r) for r in rows]


def update_physical_reading_session(
    db_connection: sqlite3.Connection,
    session_id: int,
    start_time: str,
    end_time: str,
    start_page: int,
    end_page: int,
) -> None:
    db_connection.execute(
        """
        UPDATE physical_reading_sessions
        SET start_time = ?, end_time = ?, start_page = ?, end_page = ?
        WHERE id = ?
        """,
        (start_time, end_time, start_page, end_page, session_id),
    )
    db_connection.commit()


def delete_physical_reading_session(db_connection: sqlite3.Connection, session_id: int) -> None:
    db_connection.execute("DELETE FROM physical_reading_sessions WHERE id = ?", (session_id,))
    db_connection.commit()


# --- cached_reading_sessions ---


def _row_to_cached_session_dict(row: sqlite3.Row) -> dict:
    # Grimmory-shaped, matching library_check.fetch_reading_sessions_for_book's raw dicts, so
    # stat_tiles.py's session functions need no changes to consume either source.
    return {
        "id": row["grimmory_session_id"],
        "bookType": row["book_type"],
        "startTime": row["start_time"],
        "endTime": row["end_time"],
        "durationSeconds": row["duration_seconds"],
        "endProgress": row["end_progress"],
        "progressDelta": row["progress_delta"],
    }


# Function Name: add_cached_reading_sessions
# Description: Bulk-imports raw Grimmory session dicts, ignoring any already cached (by
#   grimmory_session_id) so a tombstoned or already-seen row is never re-inserted.
# Parameters:
# - db_connection: Database connection.
# - entry_id (int): Local TBR entry these sessions belong to.
# - book_type (str): "EBOOK" or "AUDIOBOOK" - which of the entry's editions these came from.
# - sessions (list[dict]): Raw Grimmory session dicts (library_check.fetch_reading_sessions_for_book).
# Returns: None
def add_cached_reading_sessions(
    db_connection: sqlite3.Connection, entry_id: int, book_type: str, sessions: list[dict]
) -> None:
    rows = [
        (
            entry_id,
            session["id"],
            book_type,
            session.get("startTime"),
            session.get("endTime"),
            session.get("durationSeconds"),
            session.get("endProgress"),
            session.get("progressDelta"),
        )
        for session in sessions
        if session.get("id") is not None and session.get("startTime")
    ]
    if not rows:
        return
    db_connection.executemany(
        """
        INSERT OR IGNORE INTO cached_reading_sessions
            (entry_id, grimmory_session_id, book_type, start_time, end_time, duration_seconds,
             end_progress, progress_delta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    db_connection.commit()


# Function Name: list_cached_reading_sessions
# Description: Active (non-tombstoned) cached sessions for one entry/edition, Grimmory-shaped.
# Parameters:
# - db_connection: Database connection.
# - entry_id (int): Local TBR entry id.
# - book_type (str): "EBOOK" or "AUDIOBOOK".
# Returns: Grimmory-shaped session dicts (list[dict]), oldest first.
def list_cached_reading_sessions(
    db_connection: sqlite3.Connection, entry_id: int, book_type: str
) -> list[dict]:
    rows = db_connection.execute(
        """
        SELECT * FROM cached_reading_sessions
        WHERE entry_id = ? AND book_type = ? AND deleted_at IS NULL
        ORDER BY start_time
        """,
        (entry_id, book_type),
    ).fetchall()
    return [_row_to_cached_session_dict(row) for row in rows]


# Function Name: soft_delete_cached_reading_session
# Description: Tombstones one cached session so it's excluded from every read and never
#   resurrected by a future import, without erasing the row (deleted_at timestamp for investigation).
# Parameters:
# - db_connection: Database connection.
# - entry_id (int): Local TBR entry id.
# - grimmory_session_id (int): Grimmory's own session id.
# - book_type (str): "EBOOK" or "AUDIOBOOK".
# Returns: None
def soft_delete_cached_reading_session(
    db_connection: sqlite3.Connection, entry_id: int, grimmory_session_id: int, book_type: str
) -> None:
    db_connection.execute(
        """
        UPDATE cached_reading_sessions SET deleted_at = datetime('now')
        WHERE entry_id = ? AND grimmory_session_id = ? AND book_type = ?
        """,
        (entry_id, grimmory_session_id, book_type),
    )
    db_connection.commit()


# Function Name: group_duplicate_reading_sessions
# Description: Merges sessions that share an identical start_time within the same book_type -
#   a confirmed Grimmory bug (see library_check.py's AUDIOBOOKS_ENABLED comment) that fragments
#   one real listening/reading stretch into many rows, all stamped with the stretch's original
#   start instead of their own. Each burst collapses into its latest-ending row (duration_seconds
#   and progress_delta summed across the group - preserves total time exactly, only reduces row
#   count), the rest hard-deleted. Manual/explicit only, never run automatically as part of
#   import - see app/main.py's api_group_duplicate_sessions for why this is restricted to
#   "finished" entries only.
# Parameters:
# - db_connection: Database connection.
# - entry_id (int): Local TBR entry id.
# Returns: Number of rows removed (int)
def group_duplicate_reading_sessions(db_connection: sqlite3.Connection, entry_id: int) -> int:
    rows = db_connection.execute(
        """
        SELECT id, book_type, start_time, end_time, duration_seconds, progress_delta
        FROM cached_reading_sessions
        WHERE entry_id = ? AND deleted_at IS NULL
        """,
        (entry_id,),
    ).fetchall()

    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault((row["book_type"], row["start_time"]), []).append(row)

    removed_ids: list[int] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        survivor = max(group, key=lambda r: r["end_time"] or "")
        total_duration = sum(r["duration_seconds"] or 0 for r in group)
        total_delta = sum(r["progress_delta"] or 0 for r in group) or None
        db_connection.execute(
            "UPDATE cached_reading_sessions SET duration_seconds = ?, progress_delta = ? WHERE id = ?",
            (total_duration, total_delta, survivor["id"]),
        )
        removed_ids += [r["id"] for r in group if r["id"] != survivor["id"]]

    if removed_ids:
        db_connection.executemany(
            "DELETE FROM cached_reading_sessions WHERE id = ?", [(i,) for i in removed_ids]
        )
    db_connection.commit()
    return len(removed_ids)


# --- library_catalog / library_sync_state ---

def _row_to_library_catalog_entry(row: sqlite3.Row) -> LibraryCatalogEntry:
    return LibraryCatalogEntry(
        title=row["title"],
        isbn13=row["isbn13"],
        isbn10=row["isbn10"],
        authors=json.loads(row["authors"]) if row["authors"] else [],
        published_date=row["published_date"],
        grimmory_id=row["grimmory_id"],
        format=row["format"],
    )


def replace_library_catalog(db_connection: sqlite3.Connection, entries: list[LibraryCatalogEntry]) -> None:
    db_connection.execute("DELETE FROM library_catalog")
    db_connection.executemany(
        """
        INSERT INTO library_catalog (title, isbn13, isbn10, authors, published_date, grimmory_id, format)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (e.title, e.isbn13, e.isbn10, json.dumps(e.authors), e.published_date, e.grimmory_id, e.format)
            for e in entries
        ],
    )
    db_connection.commit()


def get_library_catalog(db_connection: sqlite3.Connection) -> list[LibraryCatalogEntry]:
    rows = db_connection.execute("SELECT * FROM library_catalog").fetchall()
    return [_row_to_library_catalog_entry(r) for r in rows]


def search_library_catalog(
    db_connection: sqlite3.Connection, query: str, limit: int = 20
) -> list[LibraryCatalogEntry]:

    query = query.strip()
    if not query:
        return []
    like = f"%{query}%"
    rows = db_connection.execute(
        """
        SELECT * FROM library_catalog
        WHERE title LIKE ? COLLATE NOCASE OR authors LIKE ? COLLATE NOCASE
        ORDER BY title COLLATE NOCASE
        LIMIT ?
        """,
        (like, like, limit),
    ).fetchall()
    return [_row_to_library_catalog_entry(r) for r in rows]


def set_audiobook_pairing(
    db_connection: sqlite3.Connection, audiobook_grimmory_id: int, ebook_grimmory_id: int
) -> None:
    db_connection.execute(
        """
        INSERT INTO audiobook_pairings (audiobook_grimmory_id, ebook_grimmory_id) VALUES (?, ?)
        ON CONFLICT(audiobook_grimmory_id) DO UPDATE SET ebook_grimmory_id = excluded.ebook_grimmory_id
        """,
        (audiobook_grimmory_id, ebook_grimmory_id),
    )
    db_connection.commit()


def clear_audiobook_pairing(db_connection: sqlite3.Connection, audiobook_grimmory_id: int) -> None:
    db_connection.execute(
        "DELETE FROM audiobook_pairings WHERE audiobook_grimmory_id = ?", (audiobook_grimmory_id,)
    )
    db_connection.commit()


def get_audiobook_pairings(db_connection: sqlite3.Connection) -> dict[int, int]:
    rows = db_connection.execute(
        "SELECT audiobook_grimmory_id, ebook_grimmory_id FROM audiobook_pairings"
    ).fetchall()
    return {row["audiobook_grimmory_id"]: row["ebook_grimmory_id"] for row in rows}


# --- linked_editions (Phase 1 generalization of audiobook_pairings - see
# DESIGN-multi-edition-refactor.md) ---


def set_linked_edition(
    db_connection: sqlite3.Connection, edition_grimmory_id: int, ebook_grimmory_id: int, format: str
) -> None:
    db_connection.execute(
        """
        INSERT INTO linked_editions (edition_grimmory_id, ebook_grimmory_id, format) VALUES (?, ?, ?)
        ON CONFLICT(edition_grimmory_id) DO UPDATE SET ebook_grimmory_id = excluded.ebook_grimmory_id,
                                                        format = excluded.format
        """,
        (edition_grimmory_id, ebook_grimmory_id, format),
    )
    db_connection.commit()


def clear_linked_edition(db_connection: sqlite3.Connection, edition_grimmory_id: int) -> None:
    db_connection.execute(
        "DELETE FROM linked_editions WHERE edition_grimmory_id = ?", (edition_grimmory_id,)
    )
    db_connection.commit()


def get_linked_editions(db_connection: sqlite3.Connection) -> list[LinkedEdition]:
    rows = db_connection.execute(
        "SELECT edition_grimmory_id, ebook_grimmory_id, format FROM linked_editions"
    ).fetchall()
    return [
        LinkedEdition(
            edition_grimmory_id=row["edition_grimmory_id"],
            ebook_grimmory_id=row["ebook_grimmory_id"],
            format=row["format"],
        )
        for row in rows
    ]


def get_linked_editions_for_ebook(
    db_connection: sqlite3.Connection, ebook_grimmory_id: int
) -> list[LinkedEdition]:
    rows = db_connection.execute(
        "SELECT edition_grimmory_id, ebook_grimmory_id, format FROM linked_editions WHERE ebook_grimmory_id = ?",
        (ebook_grimmory_id,),
    ).fetchall()
    return [
        LinkedEdition(
            edition_grimmory_id=row["edition_grimmory_id"],
            ebook_grimmory_id=row["ebook_grimmory_id"],
            format=row["format"],
        )
        for row in rows
    ]


# These four tables (library_sync_state, library_settings, search_settings,
# grimmory_admin_settings) are each a single always-id=1 config row - _get_singleton_row/
# _upsert_singleton share that shape so each get_*/set_* pair below is just column names.

def _get_singleton_row(conn: sqlite3.Connection, table: str, columns: str = "*") -> Optional[sqlite3.Row]:
    return conn.execute(f"SELECT {columns} FROM {table} WHERE id = 1").fetchone()


def _upsert_singleton(conn: sqlite3.Connection, table: str, values: dict) -> None:
    columns = list(values.keys())
    assignments = ", ".join(f"{col} = excluded.{col}" for col in columns)
    conn.execute(
        f"INSERT INTO {table} (id, {', '.join(columns)}) "
        f"VALUES (1, {', '.join('?' for _ in columns)}) "
        f"ON CONFLICT(id) DO UPDATE SET {assignments}",
        tuple(values.values()),
    )
    conn.commit()


def get_library_sync_state(db_connection: sqlite3.Connection) -> Optional[LibrarySyncState]:
    row = _get_singleton_row(db_connection, "library_sync_state", "last_synced_at, last_error")
    if row is None:
        return None
    return LibrarySyncState(last_synced_at=row["last_synced_at"], last_error=row["last_error"])


def set_library_sync_state(
    db_connection: sqlite3.Connection,
    last_synced_at: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    _upsert_singleton(
        db_connection,
        "library_sync_state",
        {"last_synced_at": last_synced_at, "last_error": last_error},
    )


# --- library_settings ---

def get_library_settings(db_connection: sqlite3.Connection) -> Optional[LibrarySettings]:
    row = _get_singleton_row(db_connection, "library_settings")
    if row is None:
        return None
    return LibrarySettings(
        base_url=row["base_url"],
        username=row["username"],
        password=row["password"],
        sync_interval_minutes=row["sync_interval_minutes"],
    )


def set_library_settings(
    db_connection: sqlite3.Connection,
    base_url: Optional[str],
    username: Optional[str],
    password: Optional[str],
    sync_interval_minutes: int,
) -> None:
    _upsert_singleton(
        db_connection,
        "library_settings",
        {
            "base_url": base_url,
            "username": username,
            "password": password,
            "sync_interval_minutes": sync_interval_minutes,
        },
    )


# --- search_settings ---

def get_search_settings(db_connection: sqlite3.Connection) -> Optional[SearchSettings]:
    row = _get_singleton_row(db_connection, "search_settings")
    if row is None:
        return None
    return SearchSettings(hardcover_api_key=row["hardcover_api_key"])


def set_search_settings(db_connection: sqlite3.Connection, hardcover_api_key: Optional[str]) -> None:
    _upsert_singleton(db_connection, "search_settings", {"hardcover_api_key": hardcover_api_key})


# --- grimmory_admin_settings ---

def get_grimmory_admin_settings(db_connection: sqlite3.Connection) -> Optional[GrimmoryAdminSettings]:
    row = _get_singleton_row(db_connection, "grimmory_admin_settings")
    if row is None:
        return None
    return GrimmoryAdminSettings(username=row["username"], password=row["password"])


def set_grimmory_admin_settings(
    db_connection: sqlite3.Connection, username: Optional[str], password: Optional[str]
) -> None:
    _upsert_singleton(
        db_connection, "grimmory_admin_settings", {"username": username, "password": password}
    )


# --- goals ---

def _row_to_goal(row: sqlite3.Row) -> Goal:
    return Goal(
        id=row["id"],
        user_id=row["user_id"],
        timeframe=row["timeframe"],
        target_count=row["target_count"],
        created_at=row["created_at"],
    )


def get_goal(db_connection: sqlite3.Connection, user_id: int, timeframe: str = "year") -> Optional[Goal]:
    row = db_connection.execute(
        "SELECT * FROM goals WHERE user_id = ? AND timeframe = ?", (user_id, timeframe)
    ).fetchone()
    return _row_to_goal(row) if row else None


def upsert_goal(
    db_connection: sqlite3.Connection, user_id: int, timeframe: str, target_count: int
) -> Goal:
    """Creates the goal, or updates its target in place — editing never resets progress, since
    progress is computed live from tbr_entries.finished_at rather than stored on the goal."""
    db_connection.execute(
        """
        INSERT INTO goals (user_id, timeframe, target_count) VALUES (?, ?, ?)
        ON CONFLICT(user_id, timeframe) DO UPDATE SET target_count = excluded.target_count
        """,
        (user_id, timeframe, target_count),
    )
    db_connection.commit()
    return get_goal(db_connection, user_id, timeframe)


if __name__ == "__main__":
    _db_connection = get_connection()
    init_db(_db_connection)
    _db_connection.close()
    print("Database initialized.")
