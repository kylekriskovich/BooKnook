"""SQLite schema and CRUD layer for users, books, and tbr_entries."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tbr.db")

SCHEMA_SQL = """
-- name holds the user's Grimmory username. Rows are created lazily on first successful login
-- (see app/grimmory_auth.py) rather than seeded, since there's no fixed user list anymore.
-- view_preference is which home-screen layout ('spine' | 'cover') the user last chose.
-- onboarded tracks whether the user has been through the first-login goal-setting prompt
-- (see app/main.py's /onboarding) — reused rather than a one-time "just created" flag so
-- existing users also get prompted once after the goals feature ships.
-- grimmory_refresh_token is the user's own rotating Grimmory refresh token (see
-- app/grimmory_auth.py:get_valid_access_token) — persisted so most Grimmory-backed actions
-- (reading-status sync, spice level) don't need to re-prompt for the Grimmory password every
-- time; cleared back to NULL whenever a refresh attempt is rejected, so its presence means
-- "believed valid," not "guaranteed valid." spice_level (0-5) is this user's chosen chili-pepper
-- content-rating ceiling (see app/grimmory_auth.py:sync_restriction_level).
-- calendar_view_preference ('grid' | 'list') is which layout the Reading Calendar section on
-- /stats last showed — same shape/purpose as view_preference above, just a second, independent
-- view toggle for a different part of the app.
-- want_to_read_shelf_id is the Grimmory shelf id (Grimmory's own numeric id, not a local foreign
-- key — same relationship as books.grimmory_book_id) backing this user's always-on "Want to Read"
-- shelf mirror (see app/library_check.py's shelf-sync passes in sync_user_reading_status). NULL
-- until resolved — either the user's own choice from the Settings dropdown, or lazily
-- get-or-created by name on this user's first sync after the feature shipped.
-- sync_to_device_enabled is the user's opt-in for the "Sync to Device" shelf, which feeds the
-- external grimmory.koplugin KOReader plugin — default off, and unlike want_to_read_shelf_id
-- nothing ever flows from this shelf back into BooKnook.
-- sync_to_device_shelf_id mirrors want_to_read_shelf_id's shape/lifecycle but for the opt-in
-- shelf; meaningless while sync_to_device_enabled is 0, but deliberately left in place (not
-- cleared) if the user disables and later re-enables, so re-enabling doesn't need to re-resolve
-- or re-create the shelf.
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

-- page_count is Grimmory's own catalog metadata (BookMetadata.pageCount) — plain book metadata,
-- not session-derived (see reading-app-stats-backlog.md), captured from the same sync response
-- already used for reading status. Always overwritten from Grimmory on sync, same as
-- title/author/isbn — not user-editable, unlike tbr_entries.started_at.
-- grimmory_book_id is Grimmory's own numeric book id, captured the same way as page_count — needed
-- to fetch this book's reading sessions on demand (app/stat_tiles.py, GET /book/{entry_id}), which
-- is keyed by Grimmory's id, not ours. NULL until the book is matched by a sync (see
-- library_check.py:_sync_book_metadata) — a book added via search but never yet Grimmory-matched
-- has no reading sessions to fetch anyway.
-- cover_color is a hex "#rrggbb" average color sampled from the cover image the first time it's
-- needed (see app/cover_color.py:ensure_cover_color, called from the Reading Calendar) — used to
-- color that book's calendar bars/swatch so they match its actual cover instead of an id-cycled
-- palette. NULL until first computed, and forever afterward if the book has no cover_url or the
-- image can't be decoded (callers fall back to the palette in that case).
-- manual_match_grimmory_id is an admin-asserted override, distinct from grimmory_book_id's
-- fuzzy-match provenance above — set via POST /api/admin/books/{id}/match when an admin manually
-- links a Need-to-Acquire book to a library_catalog row that library_check.find_catalog_match
-- failed to associate on its own. Stores Grimmory's own numeric book id (library_catalog.grimmory_id),
-- never library_catalog.id — that table is fully ephemeral, rebuilt from scratch on every catalog
-- sync (see replace_library_catalog), so its row ids don't survive across syncs. NULL means no
-- manual override; when set, library_check.resolve_catalog_match treats it as this book's catalog
-- match, taking priority over the fuzzy matcher. This is a second, higher-priority source for
-- grimmory_book_id above, not a loosening of that column's "not user-editable" contract —
-- grimmory_book_id itself remains only ever machine-derived, just from two candidate sources now.
-- Cleared back to NULL by the unmatch action.
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
    manual_match_grimmory_id INTEGER
);

-- status is 'wanted' | 'reading' | 'finished' — driven automatically by the Grimmory reading-
-- status sync at login (app/library_check.py:sync_user_reading_status), or set directly when a
-- user adds a book straight onto the Reading/Finished shelf. finished_at is set from Grimmory's
-- dateFinished when available (falls back to the sync timestamp), and buckets the "Finished in
-- {year}" shelf — also user-editable (POST /tbr/{id}/dates, see app/stat_tiles.py), which
-- best-effort pushes the edit back to Grimmory's own dateFinished (self-scoped, silently skipped
-- on failure — see app/grimmory_auth.py:update_book_finished_date). started_at is a plain
-- YYYY-MM-DD date, not a full timestamp — Grimmory has no session-independent "date started"
-- field anywhere (see reading-app-stats-backlog.md), so unlike finished_at it can only ever be a
-- local-only value, never written back. Best-effort auto-set the first time an entry reaches
-- 'reading' (library_check.py:_apply_status), later improved on each book-detail page view from
-- the actual first reading-session date once one exists (app/stat_tiles.py:
-- first_meaningful_session_date), and always directly user-editable (POST /tbr/{id}/dates).
-- started_at_manual distinguishes a real user edit (never auto-overwritten) from an auto-guess
-- (safe for either auto-set path above to improve) — both of the auto sources otherwise just
-- leave a plain non-null value indistinguishable from a manual one. rating mirrors Grimmory's own
-- personalRating (1-5, nullable) — like page_count, this is plain metadata already present in the
-- reading-status sync response, always overwritten from Grimmory, not user-editable in this app.
-- sort_order is a user-chosen manual ordering, meaningful only for status='wanted' (see
-- set_wanted_order) — lower sorts earlier. NULL until backfilled/set (see init_db's backfill and
-- add_tbr_entry), never NULL for a live wanted entry afterward. reading/finished ignore this
-- entirely (they order by added_at/finished_at instead — see app/main.py:_entries_for_shelf).
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
    UNIQUE(user_id, book_id)
);

-- Local cache of the Grimmory catalog, refreshed by app.library_check.
CREATE TABLE IF NOT EXISTS library_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    isbn13 TEXT,
    isbn10 TEXT,
    authors TEXT,
    published_date TEXT,
    grimmory_id INTEGER
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
"""

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = os.environ.get("TBR_DB_PATH", DEFAULT_DB_PATH)
    # check_same_thread=False: FastAPI resolves each sync dependency
    db_connection = sqlite3.connect(db_path, check_same_thread=False)
    db_connection.row_factory = sqlite3.Row
    db_connection.execute("PRAGMA foreign_keys = ON")
    return db_connection


def init_db(db_connection: sqlite3.Connection) -> None:
    db_connection.executescript(SCHEMA_SQL)
    # Lightweight in-place migration for databases created before view_preference existed —
    # SCHEMA_SQL's CREATE TABLE IF NOT EXISTS won't add it to an already-existing users table.
    try:
        db_connection.execute("ALTER TABLE users ADD COLUMN view_preference TEXT NOT NULL DEFAULT 'spine'")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE books ADD COLUMN published_date TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE library_catalog ADD COLUMN published_date TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE tbr_entries ADD COLUMN finished_at TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE tbr_entries ADD COLUMN started_at TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE books ADD COLUMN page_count INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE tbr_entries ADD COLUMN rating INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE users ADD COLUMN grimmory_refresh_token TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE users ADD COLUMN spice_level INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE books ADD COLUMN grimmory_book_id INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute(
            "ALTER TABLE tbr_entries ADD COLUMN started_at_manual INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute(
            "ALTER TABLE users ADD COLUMN calendar_view_preference TEXT NOT NULL DEFAULT 'grid'"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE books ADD COLUMN cover_color TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE library_catalog ADD COLUMN grimmory_id INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE users ADD COLUMN want_to_read_shelf_id INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute(
            "ALTER TABLE users ADD COLUMN sync_to_device_enabled INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE users ADD COLUMN sync_to_device_shelf_id INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    # KOReader self-service sync removed 2026-07-29 — dropped in favor of already-hiding
    # null/zero stats everywhere else, rather than needing a second signal to gate them on.
    # DROP COLUMN needs SQLite 3.35+ (bundled with Python 3.12's sqlite3 module, well past that);
    # a database that already had these columns dropped raises "no such column" here, not
    # OperationalError, so that's caught too.
    for column in ("koreader_username", "koreader_sync_enabled"):
        try:
            db_connection.execute(f"ALTER TABLE users DROP COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # already dropped, or never existed on a fresh database
    try:
        db_connection.execute("ALTER TABLE tbr_entries ADD COLUMN sort_order INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db_connection.execute("ALTER TABLE books ADD COLUMN manual_match_grimmory_id INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    # One-time backfill for wanted entries that predate sort_order — numbers each user's own
    # wanted shelf by their current added_at DESC order (preserving today's effective order across
    # the upgrade instead of scrambling it), 0 = first. Only touches sort_order IS NULL rows, so
    # this is a no-op on every init_db() call after the first — new entries always get a
    # non-null sort_order from add_tbr_entry itself, never relying on this running again.
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


@dataclass
class TBREntryDetail:
    id: int
    status: str
    added_at: str
    book: Book
    owned: Optional[bool] = None
    finished_at: Optional[str] = None
    started_at: Optional[str] = None
    started_at_manual: bool = False
    rating: Optional[int] = None
    sort_order: Optional[int] = None


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


def _row_to_book(row: sqlite3.Row) -> Book:
    return Book(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        isbn=row["isbn"],
        cover_url=row["cover_url"],
        published_date=row["published_date"],
        page_count=row["page_count"],
        grimmory_book_id=row["grimmory_book_id"],
        cover_color=row["cover_color"],
        manual_match_grimmory_id=row["manual_match_grimmory_id"],
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
               tbr_entries.rating, tbr_entries.sort_order,
               books.id AS book_id, books.title, books.author, books.isbn, books.cover_url,
               books.published_date, books.page_count, books.grimmory_book_id, books.cover_color
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
            book=Book(
                id=row["book_id"],
                title=row["title"],
                author=row["author"],
                isbn=row["isbn"],
                cover_url=row["cover_url"],
                published_date=row["published_date"],
                page_count=row["page_count"],
                grimmory_book_id=row["grimmory_book_id"],
                cover_color=row["cover_color"],
            ),
        )
        for row in rows
    ]


def list_aggregate_tbr(db_connection: sqlite3.Connection) -> list[AggregateTBREntry]:
    """All TBR entries across every user, deduplicated by book, with who wants each."""
    rows = db_connection.execute(
        """
        SELECT books.id AS book_id, books.title, books.author, books.isbn, books.cover_url,
               books.grimmory_book_id, books.manual_match_grimmory_id, users.name AS user_name
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
            entries[book_id] = AggregateTBREntry(
                book=Book(
                    id=book_id,
                    title=row["title"],
                    author=row["author"],
                    isbn=row["isbn"],
                    cover_url=row["cover_url"],
                    grimmory_book_id=row["grimmory_book_id"],
                    manual_match_grimmory_id=row["manual_match_grimmory_id"],
                ),
                wanted_by=[],
            )
        entries[book_id].wanted_by.append(row["user_name"])
    return list(entries.values())


def add_tbr_entry(db_connection: sqlite3.Connection, user_id: int, book_id: int, status: str = "wanted") -> TBREntry:
    # New wanted entries go to the *top* of the manually-ordered shelf (lowest sort_order sorts
    # first — see set_wanted_order), matching the pre-ordering default of newest-added-first until
    # the user drags it elsewhere. Left NULL for reading/finished, which never read sort_order.
    sort_order = None
    if status == "wanted":
        row = db_connection.execute(
            "SELECT MIN(sort_order) AS min_order FROM tbr_entries WHERE user_id = ? AND status = 'wanted'",
            (user_id,),
        ).fetchone()
        sort_order = (row["min_order"] - 1) if row["min_order"] is not None else 0

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


# --- library_catalog / library_sync_state ---

def _row_to_library_catalog_entry(row: sqlite3.Row) -> LibraryCatalogEntry:
    return LibraryCatalogEntry(
        title=row["title"],
        isbn13=row["isbn13"],
        isbn10=row["isbn10"],
        authors=json.loads(row["authors"]) if row["authors"] else [],
        published_date=row["published_date"],
        grimmory_id=row["grimmory_id"],
    )


def replace_library_catalog(db_connection: sqlite3.Connection, entries: list[LibraryCatalogEntry]) -> None:
    db_connection.execute("DELETE FROM library_catalog")
    db_connection.executemany(
        """
        INSERT INTO library_catalog (title, isbn13, isbn10, authors, published_date, grimmory_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (e.title, e.isbn13, e.isbn10, json.dumps(e.authors), e.published_date, e.grimmory_id)
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


def get_library_sync_state(db_connection: sqlite3.Connection) -> Optional[LibrarySyncState]:
    row = db_connection.execute(
        "SELECT last_synced_at, last_error FROM library_sync_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return LibrarySyncState(last_synced_at=row["last_synced_at"], last_error=row["last_error"])


def set_library_sync_state(
    db_connection: sqlite3.Connection,
    last_synced_at: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    db_connection.execute(
        """
        INSERT INTO library_sync_state (id, last_synced_at, last_error) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET last_synced_at = excluded.last_synced_at,
                                       last_error = excluded.last_error
        """,
        (last_synced_at, last_error),
    )
    db_connection.commit()


# --- library_settings ---

def get_library_settings(db_connection: sqlite3.Connection) -> Optional[LibrarySettings]:
    row = db_connection.execute("SELECT * FROM library_settings WHERE id = 1").fetchone()
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
    db_connection.execute(
        """
        INSERT INTO library_settings (id, base_url, username, password, sync_interval_minutes)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET base_url = excluded.base_url,
                                       username = excluded.username,
                                       password = excluded.password,
                                       sync_interval_minutes = excluded.sync_interval_minutes
        """,
        (base_url, username, password, sync_interval_minutes),
    )
    db_connection.commit()


# --- search_settings ---

def get_search_settings(db_connection: sqlite3.Connection) -> Optional[SearchSettings]:
    row = db_connection.execute("SELECT * FROM search_settings WHERE id = 1").fetchone()
    if row is None:
        return None
    return SearchSettings(hardcover_api_key=row["hardcover_api_key"])


def set_search_settings(db_connection: sqlite3.Connection, hardcover_api_key: Optional[str]) -> None:
    db_connection.execute(
        """
        INSERT INTO search_settings (id, hardcover_api_key) VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET hardcover_api_key = excluded.hardcover_api_key
        """,
        (hardcover_api_key,),
    )
    db_connection.commit()


# --- grimmory_admin_settings ---

def get_grimmory_admin_settings(db_connection: sqlite3.Connection) -> Optional[GrimmoryAdminSettings]:
    row = db_connection.execute("SELECT * FROM grimmory_admin_settings WHERE id = 1").fetchone()
    if row is None:
        return None
    return GrimmoryAdminSettings(username=row["username"], password=row["password"])


def set_grimmory_admin_settings(
    db_connection: sqlite3.Connection, username: Optional[str], password: Optional[str]
) -> None:
    db_connection.execute(
        """
        INSERT INTO grimmory_admin_settings (id, username, password) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET username = excluded.username, password = excluded.password
        """,
        (username, password),
    )
    db_connection.commit()


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
