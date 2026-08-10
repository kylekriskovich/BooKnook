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


def test_remove_tbr_entry(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    models.remove_tbr_entry(conn, entry.id)
    assert models.list_tbr_entries_for_user(conn, user.id) == []


# --- wanted-shelf manual ordering ---


def test_add_tbr_entry_first_wanted_entry_gets_sort_order_zero(conn):
    user = models.create_user(conn, "Alice")
    book = models.create_book(conn, "Dune")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    assert entry.sort_order == 0


def test_add_tbr_entry_new_wanted_entries_go_to_the_top(conn):
    user = models.create_user(conn, "Alice")
    dune = models.create_book(conn, "Dune")
    hobbit = models.create_book(conn, "The Hobbit")
    first = models.add_tbr_entry(conn, user.id, dune.id)
    second = models.add_tbr_entry(conn, user.id, hobbit.id)
    assert second.sort_order < first.sort_order


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
