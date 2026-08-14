import httpx
import pytest

from app import library_check, models

# Captured before any test's autouse fixture (see stub_shelf_sync below) replaces these module
# attributes — the few places a test needs the *real* function instead of the default stub used
# everywhere else so unrelated tests don't need their own Grimmory shelf fakes.
_REAL_ENSURE_WANT_TO_READ_SHELF = library_check._ensure_want_to_read_shelf
_REAL_FETCH_SHELF_BOOKS = library_check.fetch_shelf_books
_REAL_ASSIGN_BOOK_SHELVES = library_check.assign_book_shelves


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, login_payload=None, login_status=200, books_payload=None, books_status=200):
        self.login_payload = login_payload if login_payload is not None else {"accessToken": "t"}
        self.login_status = login_status
        self.books_payload = books_payload if books_payload is not None else []
        self.books_status = books_status
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, path, json=None):
        return FakeResponse(self.login_payload, self.login_status)

    def get(self, path, params=None, headers=None):
        self.get_calls.append({"path": path, "params": params, "headers": headers})
        return FakeResponse(self.books_payload, self.books_status)


class FakeCoverResponse:
    def __init__(self, status_code=200, content=b"fake-image-bytes", content_type="image/png"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeCoverClient:
    """Just the /media/.../cover endpoint — separate from FakeClient (books list + login) since
    fetch_book_cover talks to a different path with a different response shape."""

    def __init__(self, response):
        self._response = response
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, path, headers=None):
        self.get_calls.append({"path": path, "headers": headers})
        return self._response


class RoutingFakeClient:
    """Routes to the books-list fake or the cover fake depending on the requested path — for
    tests exercising sync_user_reading_status end-to-end, which calls both endpoints."""

    def __init__(self, books_client, cover_response):
        self._books_client = books_client
        self._cover_response = cover_response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, path, params=None, headers=None):
        if path == library_check.BOOKS_PATH:
            return self._books_client.get(path, params=params, headers=headers)
        return self._cover_response

    def post(self, path, json=None):
        return self._books_client.post(path, json=json)


class ShelfFakeClient:
    """Generic path-routed fake for the shelf endpoints (list/create shelves, shelf books,
    assign/unassign) — responses keyed by path, recording every call for assertions."""

    def __init__(self, get_responses=None, post_responses=None):
        self._get_responses = get_responses or {}
        self._post_responses = post_responses or {}
        self.get_calls = []
        self.post_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, path, headers=None):
        self.get_calls.append({"path": path, "headers": headers})
        return FakeResponse(self._get_responses.get(path, []))

    def post(self, path, json=None, headers=None):
        self.post_calls.append({"path": path, "json": json, "headers": headers})
        return FakeResponse(self._post_responses.get(path, {}))


@pytest.fixture
def covers_tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(library_check, "covers_dir", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def conn():
    connection = models.get_connection(":memory:")
    models.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def stub_shelf_sync(monkeypatch):
    """sync_user_reading_status's Pass 3 (Want to Read) always runs, so every test that calls it
    directly would otherwise need its own Grimmory shelf fakes even when shelf sync isn't what
    it's testing. Stub the shelf functions to no-ops by default; the "shelf sync" tests below
    override these with their own fakes via their own monkeypatch calls (last write wins)."""
    monkeypatch.setattr(library_check, "_ensure_want_to_read_shelf", lambda *a, **k: 1)
    monkeypatch.setattr(library_check, "fetch_shelf_books", lambda *a, **k: [])
    monkeypatch.setattr(library_check, "assign_book_shelves", lambda *a, **k: None)


@pytest.fixture
def configured_settings(conn):
    models.set_library_settings(
        conn,
        base_url="https://grimmory.example.com",
        username="tbr-sync",
        password="hunter2",
        sync_interval_minutes=60,
    )


# --- is_configured ---


def test_is_configured_false_when_unset(conn):
    assert library_check.is_configured(conn) is False


def test_is_configured_true_when_all_set(conn, configured_settings):
    assert library_check.is_configured(conn) is True


# --- fetch_catalog ---


def test_fetch_catalog_raises_when_not_configured(conn):
    with pytest.raises(library_check.LibraryCheckUnavailable):
        library_check.fetch_catalog(conn)


def test_fetch_catalog_parses_books(conn, configured_settings, monkeypatch):
    books_payload = [
        {
            "id": 33,
            "metadata": {
                "title": "Dune",
                "isbn13": "9780441172719",
                "isbn10": "0441172717",
                "authors": ["Frank Herbert"],
            },
        },
        {"id": 34, "metadata": {"title": "No ISBN book"}},
        {"id": 35},
    ]
    fake_client = FakeClient(books_payload=books_payload)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    catalog = library_check.fetch_catalog(conn)

    assert len(catalog) == 3
    assert catalog[0].title == "Dune"
    assert catalog[0].isbn13 == "9780441172719"
    assert catalog[0].isbn10 == "0441172717"
    assert catalog[0].authors == ["Frank Herbert"]
    assert catalog[1].title == "No ISBN book"
    assert catalog[1].isbn13 is None
    assert catalog[1].authors == []
    assert catalog[2].title == ""
    assert fake_client.get_calls[0]["headers"] == {"Authorization": "Bearer t"}


def test_fetch_catalog_raises_on_login_failure(conn, configured_settings, monkeypatch):
    fake_client = FakeClient(login_status=401)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    with pytest.raises(library_check.LibraryCheckUnavailable):
        library_check.fetch_catalog(conn)


def test_fetch_catalog_backfills_missing_cover_for_matched_local_book(
    conn, configured_settings, covers_tmp_dir, monkeypatch
):
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    books_payload = [
        {
            "id": 42,
            "metadata": {"title": "Dune", "isbn13": "9780441172719", "authors": ["Frank Herbert"]},
        }
    ]
    books_client = FakeClient(books_payload=books_payload)
    cover_response = FakeCoverResponse(content=b"png-bytes", content_type="image/png")
    routing_client = RoutingFakeClient(books_client, cover_response)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: routing_client)

    library_check.fetch_catalog(conn)

    updated = models.get_book(conn, book.id)
    assert updated.cover_url == f"/covers/{book.id}.png"
    assert updated.grimmory_book_id == 42
    assert (covers_tmp_dir / f"{book.id}.png").read_bytes() == b"png-bytes"


def test_fetch_catalog_replaces_placeholder_cover_with_real_grimmory_cover(
    conn, configured_settings, covers_tmp_dir, monkeypatch
):
    # A cover_url that isn't under /covers/ is a search-result placeholder (e.g. an Open Library
    # thumbnail stored at add time, see POST /tbr) rather than an already-downloaded Grimmory
    # cover, so it should still get replaced once the book is matched to the catalog.
    book = models.create_book(
        conn, title="Dune", author="Frank Herbert", isbn="9780441172719", cover_url="https://covers.openlibrary.org/b/id/1-M.jpg"
    )
    books_payload = [
        {
            "id": 42,
            "metadata": {"title": "Dune", "isbn13": "9780441172719", "authors": ["Frank Herbert"]},
        }
    ]
    books_client = FakeClient(books_payload=books_payload)
    cover_response = FakeCoverResponse(content=b"png-bytes", content_type="image/png")
    routing_client = RoutingFakeClient(books_client, cover_response)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: routing_client)

    library_check.fetch_catalog(conn)

    updated = models.get_book(conn, book.id)
    assert updated.cover_url == f"/covers/{book.id}.png"
    assert updated.grimmory_book_id == 42


def test_fetch_catalog_skips_cover_download_when_local_cover_already_downloaded(
    conn, configured_settings, covers_tmp_dir, monkeypatch
):
    book = models.create_book(
        conn, title="Dune", author="Frank Herbert", isbn="9780441172719", cover_url="/covers/existing.jpg"
    )
    books_payload = [
        {
            "id": 42,
            "metadata": {"title": "Dune", "isbn13": "9780441172719", "authors": ["Frank Herbert"]},
        }
    ]
    books_client = FakeClient(books_payload=books_payload)
    routing_client = RoutingFakeClient(books_client, FakeCoverResponse())
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: routing_client)

    library_check.fetch_catalog(conn)

    updated = models.get_book(conn, book.id)
    assert updated.cover_url == "/covers/existing.jpg"  # unchanged - already a local Grimmory cover
    assert updated.grimmory_book_id == 42  # still set even though the cover download was skipped


# --- check_ownership ---


def _catalog_entry(title="Dune", isbn13="9780441172719", isbn10="0441172717", authors=None):
    return models.LibraryCatalogEntry(
        title=title, isbn13=isbn13, isbn10=isbn10, authors=authors or ["Frank Herbert"]
    )


def test_check_ownership_isbn_match_ignores_hyphens():
    catalog = [_catalog_entry()]
    assert library_check.check_ownership("Dune (different title)", "978-0441172719", None, catalog) is True


def test_check_ownership_fuzzy_title_author_match():
    catalog = [_catalog_entry(title="Dune")]
    assert library_check.check_ownership("Dune", None, "Frank Herbert", catalog) is True


def test_check_ownership_no_match():
    catalog = [_catalog_entry(title="Dune")]
    assert library_check.check_ownership("A Completely Different Book", None, "Some Author", catalog) is False


def test_check_ownership_title_only_requires_stricter_threshold():
    catalog = [_catalog_entry(title="Dune")]
    # Close but not exact title, no author on the TBR side at all.
    assert library_check.check_ownership("Dune Messiah", None, None, catalog) is False
    assert library_check.check_ownership("Dune", None, None, catalog) is True


# --- sync_catalog ---


def test_sync_catalog_success_updates_cache_and_state(conn, monkeypatch):
    entries = [_catalog_entry()]
    monkeypatch.setattr(library_check, "fetch_catalog", lambda conn: entries)

    library_check.sync_catalog(conn)

    cached = models.get_library_catalog(conn)
    assert len(cached) == 1
    assert cached[0].title == "Dune"

    state = models.get_library_sync_state(conn)
    assert state.last_synced_at is not None
    assert state.last_error is None


def test_sync_catalog_failure_keeps_old_catalog_and_records_error(conn, monkeypatch):
    models.replace_library_catalog(conn, [_catalog_entry()])

    def fail(conn):
        raise library_check.LibraryCheckUnavailable("boom")

    monkeypatch.setattr(library_check, "fetch_catalog", fail)

    with pytest.raises(library_check.LibraryCheckUnavailable):
        library_check.sync_catalog(conn)

    cached = models.get_library_catalog(conn)
    assert len(cached) == 1  # unchanged

    state = models.get_library_sync_state(conn)
    assert state.last_error == "boom"


# --- _run_sync_cycle ---


def test_run_sync_cycle_returns_poll_interval_when_unconfigured(monkeypatch):
    test_conn = models.get_connection(":memory:")
    models.init_db(test_conn)
    monkeypatch.setattr(library_check, "get_connection", lambda: test_conn)

    seconds = library_check._run_sync_cycle()

    assert seconds == library_check.POLL_INTERVAL_WHEN_UNCONFIGURED_SECONDS


# --- _sync_all_user_reading_status (background per-user sync) ---


def test_sync_all_user_reading_status_noop_without_base_url(conn, monkeypatch):
    monkeypatch.delenv("GRIMMORY_BASE_URL", raising=False)
    user = models.get_or_create_user(conn, "alice")
    models.set_grimmory_refresh_token(conn, user.id, "some-refresh")

    # Would raise if it tried to import/use grimmory_auth without a configured base URL.
    library_check._sync_all_user_reading_status(conn)


def test_sync_all_user_reading_status_skips_users_without_a_session(conn, monkeypatch):
    monkeypatch.setenv("GRIMMORY_BASE_URL", "https://grimmory.example.com")
    models.get_or_create_user(conn, "alice")  # no grimmory_refresh_token set

    from app import grimmory_auth

    calls = []
    monkeypatch.setattr(
        grimmory_auth, "get_valid_access_token", lambda conn, u: calls.append(u.id) or "token"
    )
    monkeypatch.setattr(library_check, "sync_user_reading_status", lambda *a, **k: None)

    library_check._sync_all_user_reading_status(conn)

    assert calls == []


def test_sync_all_user_reading_status_syncs_users_with_a_session(conn, monkeypatch):
    monkeypatch.setenv("GRIMMORY_BASE_URL", "https://grimmory.example.com")
    user = models.get_or_create_user(conn, "alice")
    models.set_grimmory_refresh_token(conn, user.id, "some-refresh")

    from app import grimmory_auth

    monkeypatch.setattr(grimmory_auth, "get_valid_access_token", lambda conn, u: "fresh-access")
    synced = []
    monkeypatch.setattr(
        library_check,
        "sync_user_reading_status",
        lambda conn, user_id, base_url, access_token: synced.append((user_id, access_token)),
    )

    library_check._sync_all_user_reading_status(conn)

    assert synced == [(user.id, "fresh-access")]


def test_sync_all_user_reading_status_one_user_failure_does_not_block_others(conn, monkeypatch):
    monkeypatch.setenv("GRIMMORY_BASE_URL", "https://grimmory.example.com")
    failing_user = models.get_or_create_user(conn, "alice")
    models.set_grimmory_refresh_token(conn, failing_user.id, "refresh-a")
    ok_user = models.get_or_create_user(conn, "bob")
    models.set_grimmory_refresh_token(conn, ok_user.id, "refresh-b")

    from app import grimmory_auth

    monkeypatch.setattr(grimmory_auth, "get_valid_access_token", lambda conn, u: "fresh-access")

    def fake_sync(conn, user_id, base_url, access_token):
        if user_id == failing_user.id:
            raise library_check.LibraryCheckUnavailable("boom")

    synced = []
    monkeypatch.setattr(
        library_check,
        "sync_user_reading_status",
        lambda conn, user_id, base_url, access_token: (
            fake_sync(conn, user_id, base_url, access_token) or synced.append(user_id)
        ),
    )

    library_check._sync_all_user_reading_status(conn)

    assert synced == [ok_user.id]


# --- sync_user_reading_status ---


def _grimmory_book(title="Dune", isbn13="9780441172719", authors=None, read_status="UNREAD", date_finished=None):
    return {
        "metadata": {"title": title, "isbn13": isbn13, "authors": authors or ["Frank Herbert"]},
        "readStatus": read_status,
        "dateFinished": date_finished,
    }


def _fake_books_client(books_payload, monkeypatch):
    fake_client = FakeClient(books_payload=books_payload)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)
    return fake_client


def test_sync_updates_matched_wanted_entry_to_reading(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    models.add_tbr_entry(conn, user.id, book.id)  # starts "wanted"
    _fake_books_client([_grimmory_book(read_status="READING")], monkeypatch)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entry = models.list_tbr_entries_with_books(conn, user.id)[0]
    assert entry.status == "reading"


def test_sync_updates_matched_entry_to_finished_with_grimmory_date(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    models.add_tbr_entry(conn, user.id, book.id)
    _fake_books_client(
        [_grimmory_book(read_status="READ", date_finished="2026-03-01T00:00:00Z")], monkeypatch
    )

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entry = models.list_tbr_entries_with_books(conn, user.id)[0]
    assert entry.status == "finished"
    assert entry.finished_at == "2026-03-01T00:00:00Z"


def test_sync_never_downgrades_finished_entry(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    entry = models.add_tbr_entry(conn, user.id, book.id, status="finished")
    models.set_tbr_entry_status(conn, entry.id, "finished", "2026-01-01T00:00:00Z")
    # Ambiguous/re-read status shouldn't pull an already-finished book back to reading.
    _fake_books_client([_grimmory_book(read_status="READING")], monkeypatch)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    updated = models.list_tbr_entries_with_books(conn, user.id)[0]
    assert updated.status == "finished"


def test_sync_removes_reading_entry_when_abandoned(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    models.add_tbr_entry(conn, user.id, book.id, status="reading")
    _fake_books_client([_grimmory_book(read_status="ABANDONED")], monkeypatch)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    assert models.list_tbr_entries_with_books(conn, user.id) == []


def test_sync_removes_reading_entry_when_wont_read(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    models.add_tbr_entry(conn, user.id, book.id, status="reading")
    _fake_books_client([_grimmory_book(read_status="WONT_READ")], monkeypatch)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    assert models.list_tbr_entries_with_books(conn, user.id) == []


def test_sync_leaves_wanted_entry_alone_when_abandoned(conn, monkeypatch):
    # Only removes an entry that's actually "reading" — an unstarted "wanted" book isn't affected
    # just because Grimmory has some unrelated abandoned status recorded for it.
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    models.add_tbr_entry(conn, user.id, book.id)  # "wanted"
    _fake_books_client([_grimmory_book(read_status="ABANDONED")], monkeypatch)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert len(entries) == 1
    assert entries[0].status == "wanted"


def test_sync_leaves_reading_entry_alone_on_paused(conn, monkeypatch):
    # PAUSED is genuinely ambiguous (might resume) — unlike ABANDONED/WONT_READ, stays untouched.
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    models.add_tbr_entry(conn, user.id, book.id, status="reading")
    _fake_books_client([_grimmory_book(read_status="PAUSED")], monkeypatch)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert len(entries) == 1
    assert entries[0].status == "reading"


def test_sync_abandoned_book_can_be_reimported_after_resuming(conn, monkeypatch):
    # Once removed, it's unmatched again — if picked back up and finished/reading again later,
    # the import pass (not the update pass) naturally re-adds it. Not a one-way trip.
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    models.add_tbr_entry(conn, user.id, book.id, status="reading")
    _fake_books_client([_grimmory_book(read_status="ABANDONED")], monkeypatch)
    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")
    assert models.list_tbr_entries_with_books(conn, user.id) == []

    _fake_books_client([_grimmory_book(read_status="READING")], monkeypatch)
    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert len(entries) == 1
    assert entries[0].status == "reading"


def test_sync_imports_unmatched_reading_book(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    # No pre-existing tbr_entries at all — this book was never search-added.
    _fake_books_client([_grimmory_book(title="Dune", read_status="READING")], monkeypatch)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert len(entries) == 1
    assert entries[0].status == "reading"
    assert entries[0].book.title == "Dune"
    assert entries[0].book.author == "Frank Herbert"
    assert entries[0].book.isbn == "9780441172719"


def test_sync_imports_unmatched_finished_book_with_fallback_date(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    _fake_books_client([_grimmory_book(title="Dune", read_status="READ", date_finished=None)], monkeypatch)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert entries[0].status == "finished"
    assert entries[0].finished_at is not None  # fell back to "now" since Grimmory gave no date


def test_sync_ignores_books_with_unmapped_read_status(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    _fake_books_client([_grimmory_book(title="Dune", read_status="UNREAD")], monkeypatch)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    assert models.list_tbr_entries_with_books(conn, user.id) == []


# --- fetch_book_cover / _maybe_download_cover ---


def test_fetch_book_cover_returns_bytes_and_content_type(monkeypatch):
    response = FakeCoverResponse(content=b"png-bytes", content_type="image/png")
    fake_client = FakeCoverClient(response)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    result = library_check.fetch_book_cover("https://grimmory.example.com", "token", 42)

    assert result == (b"png-bytes", "image/png")
    assert fake_client.get_calls[0]["path"] == "/api/v1/media/book/42/cover"
    assert fake_client.get_calls[0]["headers"] == {"Authorization": "Bearer token"}


def test_fetch_book_cover_returns_none_on_404(monkeypatch):
    fake_client = FakeCoverClient(FakeCoverResponse(status_code=404))
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    assert library_check.fetch_book_cover("https://grimmory.example.com", "token", 42) is None


def test_fetch_book_cover_returns_none_on_server_error(monkeypatch):
    fake_client = FakeCoverClient(FakeCoverResponse(status_code=500))
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    assert library_check.fetch_book_cover("https://grimmory.example.com", "token", 42) is None


def test_fetch_book_cover_returns_none_on_network_error(monkeypatch):
    class RaisingClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, path, headers=None):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: RaisingClient())

    assert library_check.fetch_book_cover("https://grimmory.example.com", "token", 42) is None


def test_maybe_download_cover_saves_file_and_sets_cover_url(conn, covers_tmp_dir, monkeypatch):
    book = models.create_book(conn, title="Dune", author="Frank Herbert")
    fake_client = FakeCoverClient(FakeCoverResponse(content=b"png-bytes", content_type="image/png"))
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    library_check._maybe_download_cover(conn, "https://grimmory.example.com", "token", book.id, 42)

    updated = models.get_book(conn, book.id)
    assert updated.cover_url == f"/covers/{book.id}.png"
    saved = covers_tmp_dir / f"{book.id}.png"
    assert saved.read_bytes() == b"png-bytes"


def test_maybe_download_cover_noop_when_no_grimmory_id(conn, covers_tmp_dir, monkeypatch):
    book = models.create_book(conn, title="Dune", author="Frank Herbert")

    def fail_if_called(*a, **k):
        raise AssertionError("should not make an HTTP call with no grimmory_book_id")

    monkeypatch.setattr(library_check.httpx, "Client", fail_if_called)

    library_check._maybe_download_cover(conn, "https://grimmory.example.com", "token", book.id, None)

    assert models.get_book(conn, book.id).cover_url is None


# --- download_cover_for_book ---


def test_download_cover_for_book_logs_in_and_downloads(conn, configured_settings, covers_tmp_dir, monkeypatch):
    book = models.create_book(conn, title="Dune", author="Frank Herbert")
    books_client = FakeClient()
    cover_response = FakeCoverResponse(content=b"png-bytes", content_type="image/png")
    routing_client = RoutingFakeClient(books_client, cover_response)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: routing_client)

    library_check.download_cover_for_book(conn, book.id, 42)

    updated = models.get_book(conn, book.id)
    assert updated.cover_url == f"/covers/{book.id}.png"
    assert (covers_tmp_dir / f"{book.id}.png").read_bytes() == b"png-bytes"


def test_download_cover_for_book_noop_when_not_configured(conn, covers_tmp_dir, monkeypatch):
    book = models.create_book(conn, title="Dune", author="Frank Herbert")

    def fail_if_called(*a, **k):
        raise AssertionError("should not make an HTTP call when unconfigured")

    monkeypatch.setattr(library_check.httpx, "Client", fail_if_called)

    library_check.download_cover_for_book(conn, book.id, 42)

    assert models.get_book(conn, book.id).cover_url is None


def test_download_cover_for_book_noop_on_login_failure(conn, configured_settings, covers_tmp_dir, monkeypatch):
    book = models.create_book(conn, title="Dune", author="Frank Herbert")
    fake_client = FakeClient(login_status=401)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    library_check.download_cover_for_book(conn, book.id, 42)

    assert models.get_book(conn, book.id).cover_url is None


def test_sync_downloads_cover_for_imported_book(conn, covers_tmp_dir, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    books_payload = [
        {
            "id": 42,
            "metadata": {"title": "Dune", "isbn13": "9780441172719", "authors": ["Frank Herbert"]},
            "readStatus": "READING",
        }
    ]
    books_client = FakeClient(books_payload=books_payload)
    cover_response = FakeCoverResponse(content=b"png-bytes", content_type="image/png")
    routing_client = RoutingFakeClient(books_client, cover_response)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: routing_client)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert len(entries) == 1
    assert entries[0].book.cover_url == f"/covers/{entries[0].book.id}.png"
    assert (covers_tmp_dir / f"{entries[0].book.id}.png").read_bytes() == b"png-bytes"


def test_sync_replaces_placeholder_cover_with_real_grimmory_cover(conn, covers_tmp_dir, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(
        conn, title="Dune", author="Frank Herbert", isbn="9780441172719", cover_url="https://covers.openlibrary.org/b/id/1-M.jpg"
    )
    models.add_tbr_entry(conn, user.id, book.id)
    books_payload = [
        {
            "id": 42,
            "metadata": {"title": "Dune", "isbn13": "9780441172719", "authors": ["Frank Herbert"]},
            "readStatus": "READING",
        }
    ]
    books_client = FakeClient(books_payload=books_payload)
    cover_response = FakeCoverResponse(content=b"png-bytes", content_type="image/png")
    routing_client = RoutingFakeClient(books_client, cover_response)
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: routing_client)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert entries[0].book.cover_url == f"/covers/{book.id}.png"


def test_sync_skips_cover_download_when_local_cover_already_downloaded(conn, covers_tmp_dir, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(
        conn, title="Dune", author="Frank Herbert", isbn="9780441172719", cover_url="/covers/existing.jpg"
    )
    models.add_tbr_entry(conn, user.id, book.id)
    books_payload = [
        {
            "id": 42,
            "metadata": {"title": "Dune", "isbn13": "9780441172719", "authors": ["Frank Herbert"]},
            "readStatus": "READING",
        }
    ]
    books_client = FakeClient(books_payload=books_payload)
    routing_client = RoutingFakeClient(books_client, FakeCoverResponse())
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: routing_client)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert entries[0].book.cover_url == "/covers/existing.jpg"  # unchanged - already a local Grimmory cover


# --- fetch_reading_sessions_for_book ---


class PaginatedFakeClient:
    """Fakes Grimmory's actual (nested) Page response shape — {"content": [...], "page":
    {"totalPages": N, ...}} — confirmed against a real response 2026-07-29 after the flat-shape
    assumption this fake originally used turned out to be wrong and let a real pagination bug
    (truncating a 118-session book to 100) pass unit tests undetected. Returns a different page of
    content depending on the requested `page` param."""

    def __init__(self, pages: list[list[dict]]):
        self._pages = pages
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, path, params=None, headers=None):
        self.get_calls.append({"path": path, "params": params, "headers": headers})
        page_num = params["page"]
        return FakeResponse(
            {
                "content": self._pages[page_num],
                "page": {
                    "size": len(self._pages[page_num]),
                    "number": page_num,
                    "totalElements": sum(len(p) for p in self._pages),
                    "totalPages": len(self._pages),
                },
            }
        )


def test_fetch_reading_sessions_for_book_single_page(monkeypatch):
    fake_client = PaginatedFakeClient([[{"id": 1}, {"id": 2}]])
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    sessions = library_check.fetch_reading_sessions_for_book(
        "https://grimmory.example.com", "token", 42
    )

    assert sessions == [{"id": 1}, {"id": 2}]
    assert len(fake_client.get_calls) == 1
    assert fake_client.get_calls[0]["params"] == {"page": 0, "size": 100}
    assert fake_client.get_calls[0]["headers"] == {"Authorization": "Bearer token"}


def test_fetch_reading_sessions_for_book_walks_all_pages(monkeypatch):
    fake_client = PaginatedFakeClient([[{"id": 1}], [{"id": 2}], [{"id": 3}]])
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    sessions = library_check.fetch_reading_sessions_for_book(
        "https://grimmory.example.com", "token", 42
    )

    assert sessions == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert len(fake_client.get_calls) == 3


def test_fetch_reading_sessions_for_book_empty(monkeypatch):
    fake_client = PaginatedFakeClient([[]])
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    sessions = library_check.fetch_reading_sessions_for_book(
        "https://grimmory.example.com", "token", 42
    )

    assert sessions == []


def test_fetch_reading_sessions_for_book_raises_on_http_failure(monkeypatch):
    class FailingClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path, params=None, headers=None):
            return FakeResponse({}, status_code=500)

    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: FailingClient())

    with pytest.raises(library_check.LibraryCheckUnavailable):
        library_check.fetch_reading_sessions_for_book("https://grimmory.example.com", "token", 42)


# --- list_own_shelves / get_or_create_shelf_by_name / fetch_shelf_books / assign_book_shelves ---

SHELF_BASE_URL = "https://grimmory.example.com"


def test_list_own_shelves_filters_to_own_user(monkeypatch):
    shelves = [
        {"id": 1, "name": "Want to Read", "userId": 7},
        {"id": 2, "name": "Public Faves", "userId": 99},
    ]
    fake_client = ShelfFakeClient(get_responses={library_check.SHELVES_PATH: shelves})
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    result = library_check.list_own_shelves(SHELF_BASE_URL, "token", own_grimmory_user_id=7)

    assert result == [{"id": 1, "name": "Want to Read", "userId": 7}]


def test_list_own_shelves_raises_on_http_failure(monkeypatch):
    class FailingClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path, headers=None):
            return FakeResponse({}, status_code=500)

    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: FailingClient())

    with pytest.raises(library_check.LibraryCheckUnavailable):
        library_check.list_own_shelves(SHELF_BASE_URL, "token", 7)


def test_get_or_create_shelf_by_name_returns_existing_match_without_posting(monkeypatch):
    shelves = [{"id": 1, "name": "Want to Read", "userId": 7}]
    fake_client = ShelfFakeClient(get_responses={library_check.SHELVES_PATH: shelves})
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    shelf_id = library_check.get_or_create_shelf_by_name(SHELF_BASE_URL, "token", 7, "Want to Read")

    assert shelf_id == 1
    assert fake_client.post_calls == []


def test_get_or_create_shelf_by_name_creates_when_missing(monkeypatch):
    fake_client = ShelfFakeClient(
        get_responses={library_check.SHELVES_PATH: []},
        post_responses={library_check.SHELVES_PATH: {"id": 55, "name": "Want to Read"}},
    )
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    shelf_id = library_check.get_or_create_shelf_by_name(SHELF_BASE_URL, "token", 7, "Want to Read")

    assert shelf_id == 55
    assert fake_client.post_calls[0]["json"] == {"name": "Want to Read", "publicShelf": False}


def test_get_or_create_shelf_by_name_adopts_shelf_created_by_a_concurrent_sync(monkeypatch):
    # Regression test: the manual /api/settings/sync trigger and the periodic background loop can
    # both reach this function for the same user around the same time (e.g. while the library
    # catalog cross-check isn't configured, the periodic loop runs every 60s) - whichever POSTs
    # second gets Grimmory's 409 SHELF_ALREADY_EXISTS. That must be treated as "someone else just
    # created it" and adopted, not surfaced as a sync failure.
    class RacyShelfClient:
        def __init__(self):
            self.get_call_count = 0
            self.post_calls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path, headers=None):
            self.get_call_count += 1
            # First GET (before the POST) sees nothing yet; the second GET (after losing the
            # create race) sees the shelf the other sync already created.
            shelves = [] if self.get_call_count == 1 else [{"id": 77, "name": "Want to Read", "userId": 7}]
            return FakeResponse(shelves)

        def post(self, path, json=None, headers=None):
            self.post_calls.append({"path": path, "json": json})
            return FakeResponse({}, status_code=409)

    fake_client = RacyShelfClient()
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    shelf_id = library_check.get_or_create_shelf_by_name(SHELF_BASE_URL, "token", 7, "Want to Read")

    assert shelf_id == 77
    assert len(fake_client.post_calls) == 1
    assert fake_client.get_call_count == 2


def test_fetch_shelf_books_returns_payload(monkeypatch):
    books = [{"id": 42, "metadata": {"title": "Dune"}}]
    fake_client = ShelfFakeClient(
        get_responses={library_check.SHELF_BOOKS_PATH.format(shelf_id=9): books}
    )
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    # fetch_shelf_books itself is what's under test here — the autouse stub_shelf_sync fixture
    # only stubs it for sync_user_reading_status's own callers, not tests exercising it directly.
    assert _REAL_FETCH_SHELF_BOOKS(SHELF_BASE_URL, "token", 9) == books


def test_assign_book_shelves_noop_when_no_book_ids(monkeypatch):
    fake_client = ShelfFakeClient()
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    _REAL_ASSIGN_BOOK_SHELVES(SHELF_BASE_URL, "token", set())

    assert fake_client.post_calls == []


def test_assign_book_shelves_posts_expected_body(monkeypatch):
    fake_client = ShelfFakeClient()
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: fake_client)

    _REAL_ASSIGN_BOOK_SHELVES(
        SHELF_BASE_URL, "token", {1, 2}, shelves_to_assign={10}, shelves_to_unassign={20}
    )

    call = fake_client.post_calls[0]
    assert call["path"] == library_check.BOOKS_SHELVES_PATH
    assert set(call["json"]["bookIds"]) == {1, 2}
    assert call["json"]["shelvesToAssign"] == [10]
    assert call["json"]["shelvesToUnassign"] == [20]


def test_assign_book_shelves_raises_on_http_failure(monkeypatch):
    class FailingClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, path, json=None, headers=None):
            return FakeResponse({}, status_code=500)

    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: FailingClient())

    with pytest.raises(library_check.LibraryCheckUnavailable):
        _REAL_ASSIGN_BOOK_SHELVES(SHELF_BASE_URL, "token", {1})


# --- sync_user_reading_status: shelf sync (Pass 3 Want to Read / Pass 4 Sync to Device) ---


def _record_assign_calls(monkeypatch, calls):
    def fake_assign(base_url, access_token, book_ids, shelves_to_assign=frozenset(), shelves_to_unassign=frozenset()):
        calls.append(
            {
                "book_ids": set(book_ids),
                "assign": set(shelves_to_assign),
                "unassign": set(shelves_to_unassign),
            }
        )

    monkeypatch.setattr(library_check, "assign_book_shelves", fake_assign)


def test_shelf_sync_lazy_creation_skipped_when_shelf_id_already_set(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    models.set_want_to_read_shelf_id(conn, user.id, 555)
    monkeypatch.setattr(
        library_check, "_ensure_want_to_read_shelf", _REAL_ENSURE_WANT_TO_READ_SHELF
    )
    _fake_books_client([], monkeypatch)
    monkeypatch.setattr(library_check, "fetch_shelf_books", lambda *a, **k: [])
    monkeypatch.setattr(library_check, "assign_book_shelves", lambda *a, **k: None)

    def fail_get_or_create(*a, **k):
        raise AssertionError("shelf id already resolved, must not be re-created")

    monkeypatch.setattr(library_check, "get_or_create_shelf_by_name", fail_get_or_create)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")


def test_shelf_sync_assigns_wanted_in_library_book_not_yet_on_shelf(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    models.set_book_grimmory_id(conn, book.id, 42)
    models.add_tbr_entry(conn, user.id, book.id)  # status "wanted"
    _fake_books_client([], monkeypatch)
    monkeypatch.setattr(library_check, "fetch_shelf_books", lambda *a, **k: [])
    assign_calls = []
    _record_assign_calls(monkeypatch, assign_calls)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    call = next(c for c in assign_calls if c["assign"] == {1})
    assert call["book_ids"] == {42}
    assert call["unassign"] == set()


def test_shelf_sync_unassigns_book_that_transitions_off_wanted_this_sync(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    # Local cover already set so Pass 1 doesn't attempt a cover download for this entry — that's
    # covered separately by the existing cover-download tests, not the point of this test.
    book = models.create_book(
        conn, title="Dune", author="Frank Herbert", isbn="9780441172719", cover_url="/covers/existing.jpg"
    )
    models.set_book_grimmory_id(conn, book.id, 42)
    models.add_tbr_entry(conn, user.id, book.id)  # status "wanted"
    # Real Grimmory book payloads always include "id" - matters here since Pass 1's
    # _sync_book_metadata always overwrites books.grimmory_book_id from this payload.
    _fake_books_client(
        [
            {
                "id": 42,
                "metadata": {"title": "Dune", "isbn13": "9780441172719", "authors": ["Frank Herbert"]},
                "readStatus": "READING",
            }
        ],
        monkeypatch,
    )
    # Simulate the book already sitting on the Want to Read shelf from a prior sync.
    monkeypatch.setattr(
        library_check, "fetch_shelf_books", lambda *a, **k: [{"id": 42, "metadata": {"title": "Dune"}}]
    )
    assign_calls = []
    _record_assign_calls(monkeypatch, assign_calls)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entry = models.list_tbr_entries_with_books(conn, user.id)[0]
    assert entry.status == "reading"
    call = next(c for c in assign_calls if c["unassign"] == {1})
    assert call["book_ids"] == {42}


def test_shelf_sync_pulls_in_unknown_shelf_book_without_duplicating_known_match(
    conn, covers_tmp_dir, monkeypatch
):
    user = models.get_or_create_user(conn, "alice")
    # Already-known entry, currently "reading" and present on the shelf too — pull-in must not
    # duplicate or downgrade it.
    known_book = models.create_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719")
    models.set_book_grimmory_id(conn, known_book.id, 42)
    known_entry = models.add_tbr_entry(conn, user.id, known_book.id)
    models.set_tbr_entry_status(conn, known_entry.id, "reading")

    # The pulled-in book (id 99) has no local cover yet, so Pass 3's pull-in triggers a real cover
    # download — route it through a proper fake cover response rather than the plain books-list
    # fake, matching the existing test_sync_downloads_cover_for_imported_book pattern.
    books_client = FakeClient(books_payload=[])
    routing_client = RoutingFakeClient(books_client, FakeCoverResponse())
    monkeypatch.setattr(library_check.httpx, "Client", lambda *a, **k: routing_client)
    shelf_books = [
        {"id": 42, "metadata": {"title": "Dune", "isbn13": "9780441172719", "authors": ["Frank Herbert"]}},
        {
            "id": 99,
            "metadata": {
                "title": "Project Hail Mary",
                "isbn13": "9780593135204",
                "authors": ["Andy Weir"],
            },
        },
    ]
    monkeypatch.setattr(library_check, "fetch_shelf_books", lambda *a, **k: shelf_books)
    monkeypatch.setattr(library_check, "assign_book_shelves", lambda *a, **k: None)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    entries = models.list_tbr_entries_with_books(conn, user.id)
    assert len(entries) == 2
    known = next(e for e in entries if e.book.grimmory_book_id == 42)
    assert known.status == "reading"  # not downgraded/duplicated
    pulled_in = next(e for e in entries if e.book.grimmory_book_id == 99)
    assert pulled_in.status == "wanted"
    assert pulled_in.book.title == "Project Hail Mary"


def test_shelf_sync_device_shelf_skipped_when_disabled(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")  # sync_to_device_enabled defaults to False
    _fake_books_client([], monkeypatch)
    monkeypatch.setattr(library_check, "fetch_shelf_books", lambda *a, **k: [])
    monkeypatch.setattr(library_check, "assign_book_shelves", lambda *a, **k: None)

    def fail_ensure_device_shelf(*a, **k):
        raise AssertionError("sync_to_device shelf should not be resolved when disabled")

    monkeypatch.setattr(library_check, "_ensure_sync_to_device_shelf", fail_ensure_device_shelf)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")


def test_shelf_sync_device_shelf_assigns_all_in_library_statuses_never_unassigns(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    models.set_sync_to_device_enabled(conn, user.id, True)

    wanted_book = models.create_book(conn, title="Dune", isbn="9780441172719")
    models.set_book_grimmory_id(conn, wanted_book.id, 1)
    models.add_tbr_entry(conn, user.id, wanted_book.id)  # wanted

    finished_book = models.create_book(conn, title="Hyperion", isbn="9780553283686")
    models.set_book_grimmory_id(conn, finished_book.id, 2)
    finished_entry = models.add_tbr_entry(conn, user.id, finished_book.id)
    models.set_tbr_entry_status(conn, finished_entry.id, "finished")

    _fake_books_client([], monkeypatch)
    monkeypatch.setattr(library_check, "fetch_shelf_books", lambda *a, **k: [])
    monkeypatch.setattr(library_check, "_ensure_sync_to_device_shelf", lambda *a, **k: 2)
    assign_calls = []
    _record_assign_calls(monkeypatch, assign_calls)

    library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")

    device_call = next(c for c in assign_calls if c["assign"] == {2})
    assert device_call["book_ids"] == {1, 2}
    assert device_call["unassign"] == set()


def test_shelf_sync_failure_propagates_uncaught(conn, monkeypatch):
    user = models.get_or_create_user(conn, "alice")
    _fake_books_client([], monkeypatch)

    def raise_unavailable(*a, **k):
        raise library_check.LibraryCheckUnavailable("shelf api down")

    monkeypatch.setattr(library_check, "fetch_shelf_books", raise_unavailable)

    with pytest.raises(library_check.LibraryCheckUnavailable):
        library_check.sync_user_reading_status(conn, user.id, "https://grimmory.example.com", "token")
