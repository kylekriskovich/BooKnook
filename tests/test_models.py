import pytest

from app import models


@pytest.fixture
def conn():
    connection = models.get_connection(":memory:")
    models.init_db(connection)
    yield connection
    connection.close()


def test_create_user(conn):
    user = models.create_user(conn, "Alice")
    assert user.id is not None
    assert models.get_user(conn, user.id) == user


def test_get_or_create_user_creates_on_first_login(conn):
    user = models.get_or_create_user(conn, "Alice")
    assert user.name == "Alice"
    assert models.list_users(conn) == [user]


def test_get_or_create_user_returns_existing_on_repeat_login(conn):
    first = models.get_or_create_user(conn, "Alice")
    second = models.get_or_create_user(conn, "Alice")
    assert first.id == second.id
    assert len(models.list_users(conn)) == 1


def test_new_user_defaults_to_spine_view(conn):
    user = models.create_user(conn, "Alice")
    assert user.view_preference == "spine"


def test_set_view_preference(conn):
    user = models.create_user(conn, "Alice")
    models.set_view_preference(conn, user.id, "cover")
    assert models.get_user(conn, user.id).view_preference == "cover"


def test_new_user_defaults_shelf_sync_fields(conn):
    user = models.create_user(conn, "Alice")
    assert user.want_to_read_shelf_id is None
    assert user.sync_to_device_enabled is False
    assert user.sync_to_device_shelf_id is None


def test_set_want_to_read_shelf_id(conn):
    user = models.create_user(conn, "Alice")
    models.set_want_to_read_shelf_id(conn, user.id, 42)
    assert models.get_user(conn, user.id).want_to_read_shelf_id == 42
    models.set_want_to_read_shelf_id(conn, user.id, None)
    assert models.get_user(conn, user.id).want_to_read_shelf_id is None


def test_set_sync_to_device_enabled(conn):
    user = models.create_user(conn, "Alice")
    models.set_sync_to_device_enabled(conn, user.id, True)
    assert models.get_user(conn, user.id).sync_to_device_enabled is True
    models.set_sync_to_device_enabled(conn, user.id, False)
    assert models.get_user(conn, user.id).sync_to_device_enabled is False


def test_set_sync_to_device_shelf_id(conn):
    user = models.create_user(conn, "Alice")
    models.set_sync_to_device_shelf_id(conn, user.id, 99)
    assert models.get_user(conn, user.id).sync_to_device_shelf_id == 99
    models.set_sync_to_device_shelf_id(conn, user.id, None)
    assert models.get_user(conn, user.id).sync_to_device_shelf_id is None


def test_create_book_reuses_existing_isbn(conn):
    book1 = models.create_book(conn, "Dune", author="Frank Herbert", isbn="9780441172719")
    book2 = models.create_book(conn, "Dune", author="Frank Herbert", isbn="9780441172719")
    assert book1.id == book2.id
    assert len(models.list_books(conn)) == 1


def test_create_book_without_isbn_creates_new_each_time(conn):
    book1 = models.create_book(conn, "Untitled Draft")
    book2 = models.create_book(conn, "Untitled Draft")
    assert book1.id != book2.id


def test_set_book_cover_color_round_trips_via_list_tbr_entries_with_books(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    models.add_tbr_entry(conn, user.id, book.id)
    assert models.get_book(conn, book.id).cover_color is None

    models.set_book_cover_color(conn, book.id, "#a1b2c3")

    assert models.get_book(conn, book.id).cover_color == "#a1b2c3"
    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert entries[0].book.cover_color == "#a1b2c3"


def test_add_and_list_tbr_entry(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    assert entry.status == "wanted"
    entries = models.list_tbr_entries_for_user(conn, user.id)
    assert len(entries) == 1
    assert entries[0].id == entry.id


def test_add_tbr_entry_is_idempotent(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    entry1 = models.add_tbr_entry(conn, user.id, book.id)
    entry2 = models.add_tbr_entry(conn, user.id, book.id)
    assert entry1.id == entry2.id
    assert len(models.list_tbr_entries_for_user(conn, user.id)) == 1


def test_set_book_format(conn):
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    assert book.format is None

    models.set_book_format(conn, book.id, "AUDIOBOOK")

    assert models.get_book(conn, book.id).format == "AUDIOBOOK"


def test_set_tbr_entry_audiobook_progress_percent(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    assert entry.audiobook_progress_percent is None

    models.set_tbr_entry_audiobook_progress_percent(conn, entry.id, 42.5)

    updated = models.get_tbr_entry(conn, entry.id)
    assert updated.audiobook_progress_percent == 42.5
    detail = models.list_tbr_entries_with_books(conn, user.id)[0]
    assert detail.audiobook_progress_percent == 42.5


def test_remove_tbr_entry(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    models.remove_tbr_entry(conn, entry.id)
    assert models.list_tbr_entries_for_user(conn, user.id) == []


def test_remove_tbr_entry_with_logged_physical_session_does_not_violate_foreign_key(conn):
    # physical_reading_sessions.entry_id must cascade - PRAGMA foreign_keys is on (get_connection)
    # and remove_tbr_entry does a bare DELETE, so a missing ON DELETE CASCADE raises IntegrityError.
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    models.add_physical_reading_session(conn, entry.id, "2026-08-21T10:00:00Z", "2026-08-21T11:00:00Z", 0, 140)

    models.remove_tbr_entry(conn, entry.id)

    assert models.list_tbr_entries_for_user(conn, user.id) == []
    assert models.list_physical_reading_sessions(conn, entry.id) == []


# --- wanted-shelf manual ordering ---


def test_add_tbr_entry_first_wanted_entry_gets_sort_order_zero(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    assert entry.sort_order == 0


def test_add_tbr_entry_new_wanted_entries_go_to_the_bottom(conn):
    user = models.create_user(conn, "Alice")
    dune = models.create_book(conn, "Dune")
    hobbit = models.create_book(conn, "The Hobbit")
    first = models.add_tbr_entry(conn, user.id, dune.id)
    second = models.add_tbr_entry(conn, user.id, hobbit.id)
    assert second.sort_order > first.sort_order


def test_add_tbr_entry_leaves_sort_order_none_for_non_wanted_status(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune")
    entry = models.add_tbr_entry(conn, user.id, book.id, status="reading")
    assert entry.sort_order is None


def test_init_db_backfills_sort_order_for_legacy_wanted_entries(conn):
    # Simulates rows written before sort_order existed (NULL), inserted directly to bypass
    # add_tbr_entry's own sort_order handling — same scenario a pre-upgrade database is in.
    user = models.create_user(conn, "Alice")
    older = models.create_book(conn, "Older Book")
    newer = models.create_book(conn, "Newer Book")
    conn.execute(
        "INSERT INTO tbr_entries (user_id, book_id, status, added_at, sort_order) "
        "VALUES (?, ?, 'wanted', '2026-01-01T00:00:00', NULL)",
        (user.id, older.id),
    )
    conn.execute(
        "INSERT INTO tbr_entries (user_id, book_id, status, added_at, sort_order) "
        "VALUES (?, ?, 'wanted', '2026-01-02T00:00:00', NULL)",
        (user.id, newer.id),
    )
    conn.commit()

    models.init_db(conn)  # re-run the (idempotent) backfill

    entries = {e.book_id: e for e in models.list_tbr_entries_for_user(conn, user.id)}
    assert entries[newer.id].sort_order < entries[older.id].sort_order  # newest-added sorts first

    # Idempotent: running it again doesn't change already-backfilled values.
    before = {e.book_id: e.sort_order for e in models.list_tbr_entries_for_user(conn, user.id)}
    models.init_db(conn)
    after = {e.book_id: e.sort_order for e in models.list_tbr_entries_for_user(conn, user.id)}
    assert before == after


def test_set_wanted_order_reorders_by_index(conn):
    user = models.create_user(conn, "Alice")
    a = models.add_tbr_entry(conn, user.id, models.create_book(conn, "A").id)
    b = models.add_tbr_entry(conn, user.id, models.create_book(conn, "B").id)
    c = models.add_tbr_entry(conn, user.id, models.create_book(conn, "C").id)

    models.set_wanted_order(conn, user.id, [b.id, c.id, a.id])

    entries = {e.id: e.sort_order for e in models.list_tbr_entries_for_user(conn, user.id)}
    assert entries[b.id] < entries[c.id] < entries[a.id]


def test_set_wanted_order_does_not_touch_another_users_entries(conn):
    alice = models.create_user(conn, "Alice")
    bob = models.create_user(conn, "Bob")
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    bobs_entry = models.add_tbr_entry(conn, bob.id, book.id)
    original_sort_order = bobs_entry.sort_order

    # Alice tries to reorder using Bob's entry id — scoped update must silently no-op it.
    models.set_wanted_order(conn, alice.id, [bobs_entry.id])

    refreshed = models.get_tbr_entry(conn, bobs_entry.id)
    assert refreshed.sort_order == original_sort_order


def test_set_wanted_order_does_not_touch_non_wanted_entries(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune")
    entry = models.add_tbr_entry(conn, user.id, book.id, status="reading")

    models.set_wanted_order(conn, user.id, [entry.id])

    assert models.get_tbr_entry(conn, entry.id).sort_order is None


def test_list_all_tbr_entries_across_users(conn):
    alice = models.create_user(conn, "Alice")
    bob = models.create_user(conn, "Bob")
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    models.add_tbr_entry(conn, alice.id, book.id)
    models.add_tbr_entry(conn, bob.id, book.id)
    assert len(models.list_all_tbr_entries(conn)) == 2


def test_list_aggregate_tbr_dedupes_by_book_and_lists_wanters(conn):
    alice = models.create_user(conn, "Alice")
    bob = models.create_user(conn, "Bob")
    dune = models.create_book(conn, "Dune", isbn="9780441172719")
    hobbit = models.create_book(conn, "The Hobbit", isbn="9780547928227")
    models.add_tbr_entry(conn, alice.id, dune.id)
    models.add_tbr_entry(conn, bob.id, dune.id)
    models.add_tbr_entry(conn, alice.id, hobbit.id)

    aggregate = models.list_aggregate_tbr(conn)

    assert len(aggregate) == 2
    dune_entry = next(e for e in aggregate if e.book.id == dune.id)
    hobbit_entry = next(e for e in aggregate if e.book.id == hobbit.id)
    assert sorted(dune_entry.wanted_by) == ["Alice", "Bob"]
    assert hobbit_entry.wanted_by == ["Alice"]


def test_list_aggregate_tbr_empty(conn):
    assert models.list_aggregate_tbr(conn) == []


def test_get_library_settings_unset_returns_none(conn):
    assert models.get_library_settings(conn) is None


def test_set_and_get_library_settings_round_trips(conn):
    models.set_library_settings(
        conn,
        base_url="https://grimmory.example.com",
        username="tbr-sync",
        password="hunter2",
        sync_interval_minutes=30,
    )
    settings = models.get_library_settings(conn)
    assert settings.base_url == "https://grimmory.example.com"
    assert settings.username == "tbr-sync"
    assert settings.password == "hunter2"
    assert settings.sync_interval_minutes == 30


def test_set_library_settings_upserts(conn):
    models.set_library_settings(
        conn, base_url="https://a.example.com", username="a", password="a", sync_interval_minutes=60
    )
    models.set_library_settings(
        conn, base_url="https://b.example.com", username="b", password="b", sync_interval_minutes=15
    )
    settings = models.get_library_settings(conn)
    assert settings.base_url == "https://b.example.com"
    assert settings.username == "b"
    assert settings.sync_interval_minutes == 15


# --- search_library_catalog ---


def test_search_library_catalog_matches_title_case_insensitively(conn):
    models.replace_library_catalog(
        conn, [models.LibraryCatalogEntry(title="Dune", isbn13=None, isbn10=None, authors=["Frank Herbert"])]
    )
    assert [e.title for e in models.search_library_catalog(conn, "dune")] == ["Dune"]


def test_search_library_catalog_matches_author(conn):
    models.replace_library_catalog(
        conn, [models.LibraryCatalogEntry(title="Dune", isbn13=None, isbn10=None, authors=["Frank Herbert"])]
    )
    assert [e.title for e in models.search_library_catalog(conn, "Herbert")] == ["Dune"]


def test_search_library_catalog_no_match(conn):
    models.replace_library_catalog(
        conn, [models.LibraryCatalogEntry(title="Dune", isbn13=None, isbn10=None, authors=["Frank Herbert"])]
    )
    assert models.search_library_catalog(conn, "Discworld") == []


def test_search_library_catalog_blank_query_returns_nothing(conn):
    models.replace_library_catalog(
        conn, [models.LibraryCatalogEntry(title="Dune", isbn13=None, isbn10=None, authors=["Frank Herbert"])]
    )
    assert models.search_library_catalog(conn, "   ") == []


def test_search_library_catalog_sorted_by_title(conn):
    models.replace_library_catalog(
        conn,
        [
            models.LibraryCatalogEntry(title="Zeta Book", isbn13=None, isbn10=None, authors=[]),
            models.LibraryCatalogEntry(title="Alpha Book", isbn13=None, isbn10=None, authors=[]),
        ],
    )
    assert [e.title for e in models.search_library_catalog(conn, "book")] == ["Alpha Book", "Zeta Book"]


def test_search_library_catalog_round_trips_grimmory_id(conn):
    models.replace_library_catalog(
        conn,
        [models.LibraryCatalogEntry(title="Dune", isbn13=None, isbn10=None, authors=[], grimmory_id=42)],
    )
    assert [e.grimmory_id for e in models.search_library_catalog(conn, "dune")] == [42]


def test_get_library_catalog_round_trips_format(conn):
    models.replace_library_catalog(
        conn,
        [models.LibraryCatalogEntry(title="Dune", isbn13=None, isbn10=None, authors=[], format="AUDIOBOOK")],
    )
    assert [e.format for e in models.get_library_catalog(conn)] == ["AUDIOBOOK"]


# --- audiobook_pairings ---


def test_set_audiobook_pairing_round_trips(conn):
    models.set_audiobook_pairing(conn, audiobook_grimmory_id=2, ebook_grimmory_id=1)
    assert models.get_audiobook_pairings(conn) == {2: 1}


def test_set_audiobook_pairing_overwrites_existing_pairing_for_same_audiobook(conn):
    models.set_audiobook_pairing(conn, audiobook_grimmory_id=2, ebook_grimmory_id=1)
    models.set_audiobook_pairing(conn, audiobook_grimmory_id=2, ebook_grimmory_id=99)
    assert models.get_audiobook_pairings(conn) == {2: 99}


def test_clear_audiobook_pairing_removes_it(conn):
    models.set_audiobook_pairing(conn, audiobook_grimmory_id=2, ebook_grimmory_id=1)
    models.clear_audiobook_pairing(conn, audiobook_grimmory_id=2)
    assert models.get_audiobook_pairings(conn) == {}


# --- cached_reading_sessions ---


def _entry(conn):
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert")
    return models.add_tbr_entry(conn, user.id, book.id)


def test_add_and_list_cached_reading_sessions_round_trips(conn):
    entry = _entry(conn)
    session = {
        "id": 101,
        "startTime": "2026-01-01T10:00:00Z",
        "endTime": "2026-01-01T10:30:00Z",
        "durationSeconds": 1800,
        "endProgress": 25.0,
        "progressDelta": 5.0,
    }
    models.add_cached_reading_sessions(conn, entry.id, "EBOOK", [session])

    cached = models.list_cached_reading_sessions(conn, entry.id, "EBOOK")
    assert len(cached) == 1
    assert cached[0]["startTime"] == "2026-01-01T10:00:00Z"
    assert cached[0]["durationSeconds"] == 1800
    assert cached[0]["bookType"] == "EBOOK"


def test_add_cached_reading_sessions_ignores_duplicates_by_grimmory_session_id(conn):
    entry = _entry(conn)
    session = {"id": 101, "startTime": "2026-01-01T10:00:00Z"}
    models.add_cached_reading_sessions(conn, entry.id, "EBOOK", [session])
    models.add_cached_reading_sessions(conn, entry.id, "EBOOK", [session])

    assert len(models.list_cached_reading_sessions(conn, entry.id, "EBOOK")) == 1


def test_add_cached_reading_sessions_skips_sessions_without_id_or_start_time(conn):
    entry = _entry(conn)
    models.add_cached_reading_sessions(
        conn, entry.id, "EBOOK", [{"startTime": "2026-01-01T10:00:00Z"}, {"id": 5}, {}]
    )
    assert models.list_cached_reading_sessions(conn, entry.id, "EBOOK") == []


def test_cached_reading_sessions_separate_by_book_type(conn):
    entry = _entry(conn)
    models.add_cached_reading_sessions(conn, entry.id, "EBOOK", [{"id": 1, "startTime": "2026-01-01T00:00:00Z"}])
    models.add_cached_reading_sessions(conn, entry.id, "AUDIOBOOK", [{"id": 1, "startTime": "2026-01-02T00:00:00Z"}])

    assert len(models.list_cached_reading_sessions(conn, entry.id, "EBOOK")) == 1
    assert len(models.list_cached_reading_sessions(conn, entry.id, "AUDIOBOOK")) == 1


def test_soft_delete_cached_reading_session_excludes_it_from_list(conn):
    entry = _entry(conn)
    models.add_cached_reading_sessions(conn, entry.id, "EBOOK", [{"id": 101, "startTime": "2026-01-01T00:00:00Z"}])

    models.soft_delete_cached_reading_session(conn, entry.id, 101, "EBOOK")

    assert models.list_cached_reading_sessions(conn, entry.id, "EBOOK") == []


def test_soft_deleted_session_is_never_resurrected_by_a_later_import(conn):
    entry = _entry(conn)
    session = {"id": 101, "startTime": "2026-01-01T00:00:00Z"}
    models.add_cached_reading_sessions(conn, entry.id, "EBOOK", [session])
    models.soft_delete_cached_reading_session(conn, entry.id, 101, "EBOOK")

    # A future resync re-fetches the same session from Grimmory - INSERT OR IGNORE must not
    # revive the tombstoned row.
    models.add_cached_reading_sessions(conn, entry.id, "EBOOK", [session])

    assert models.list_cached_reading_sessions(conn, entry.id, "EBOOK") == []


# --- group_duplicate_reading_sessions ---


def test_group_duplicate_reading_sessions_merges_a_burst(conn):
    entry = _entry(conn)
    # The observed Grimmory bug: every row in a burst shares the stretch's original start_time,
    # with end_time creeping forward and duration_seconds representing each real ~5-min chunk.
    models.add_cached_reading_sessions(
        conn,
        entry.id,
        "AUDIOBOOK",
        [
            {"id": 1, "startTime": "2026-08-26T06:12:25Z", "endTime": "2026-08-26T06:17:25Z", "durationSeconds": 300},
            {"id": 2, "startTime": "2026-08-26T06:12:25Z", "endTime": "2026-08-26T06:22:25Z", "durationSeconds": 300},
            {"id": 3, "startTime": "2026-08-26T06:12:25Z", "endTime": "2026-08-26T06:27:25Z", "durationSeconds": 259},
        ],
    )

    removed = models.group_duplicate_reading_sessions(conn, entry.id)

    assert removed == 2
    cached = models.list_cached_reading_sessions(conn, entry.id, "AUDIOBOOK")
    assert len(cached) == 1
    assert cached[0]["id"] == 3  # the latest-ending row survives
    assert cached[0]["startTime"] == "2026-08-26T06:12:25Z"
    assert cached[0]["endTime"] == "2026-08-26T06:27:25Z"
    assert cached[0]["durationSeconds"] == 859  # summed, not overwritten - preserves total time


def test_group_duplicate_reading_sessions_leaves_unique_sessions_untouched(conn):
    entry = _entry(conn)
    models.add_cached_reading_sessions(
        conn,
        entry.id,
        "EBOOK",
        [
            {"id": 1, "startTime": "2026-01-01T00:00:00Z", "durationSeconds": 100},
            {"id": 2, "startTime": "2026-01-02T00:00:00Z", "durationSeconds": 200},
        ],
    )

    removed = models.group_duplicate_reading_sessions(conn, entry.id)

    assert removed == 0
    assert len(models.list_cached_reading_sessions(conn, entry.id, "EBOOK")) == 2


def test_group_duplicate_reading_sessions_never_merges_across_book_types(conn):
    entry = _entry(conn)
    # Same start_time, but one ebook one audiobook - must not merge across the two.
    models.add_cached_reading_sessions(
        conn, entry.id, "EBOOK", [{"id": 1, "startTime": "2026-01-01T00:00:00Z", "durationSeconds": 100}]
    )
    models.add_cached_reading_sessions(
        conn, entry.id, "AUDIOBOOK", [{"id": 1, "startTime": "2026-01-01T00:00:00Z", "durationSeconds": 200}]
    )

    removed = models.group_duplicate_reading_sessions(conn, entry.id)

    assert removed == 0
    assert len(models.list_cached_reading_sessions(conn, entry.id, "EBOOK")) == 1
    assert len(models.list_cached_reading_sessions(conn, entry.id, "AUDIOBOOK")) == 1


def test_group_duplicate_reading_sessions_ignores_tombstoned_sessions(conn):
    entry = _entry(conn)
    models.add_cached_reading_sessions(
        conn,
        entry.id,
        "AUDIOBOOK",
        [
            {"id": 1, "startTime": "2026-01-01T00:00:00Z", "endTime": "2026-01-01T00:05:00Z", "durationSeconds": 300},
            {"id": 2, "startTime": "2026-01-01T00:00:00Z", "endTime": "2026-01-01T00:10:00Z", "durationSeconds": 300},
        ],
    )
    models.soft_delete_cached_reading_session(conn, entry.id, 2, "AUDIOBOOK")

    removed = models.group_duplicate_reading_sessions(conn, entry.id)

    # Only one active row remains (the other is tombstoned) - nothing to merge with.
    assert removed == 0
    assert len(models.list_cached_reading_sessions(conn, entry.id, "AUDIOBOOK")) == 1


def test_cached_reading_sessions_cascade_delete_with_entry(conn):
    entry = _entry(conn)
    models.add_cached_reading_sessions(conn, entry.id, "EBOOK", [{"id": 101, "startTime": "2026-01-01T00:00:00Z"}])

    conn.execute("DELETE FROM tbr_entries WHERE id = ?", (entry.id,))
    conn.commit()

    rows = conn.execute("SELECT * FROM cached_reading_sessions WHERE entry_id = ?", (entry.id,)).fetchall()
    assert rows == []
