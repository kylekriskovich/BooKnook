"""Tests for the /api/* JSON routes — see CLAUDE.md for the overall architecture.

`client` fixture: per-test SQLite via TBR_DB_PATH, Grimmory calls monkeypatched rather than
hitting the network.
"""

from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import grimmory_auth, hardcover, library_check, main, models
from app.main import COOKIE_NAME, app, sign_session_cookie
from app.metadata import SearchResult


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("TBR_DB_PATH", db_path)
    monkeypatch.setenv("TBR_SECRET_KEY", "test-secret")
    monkeypatch.setenv(grimmory_auth.GRIMMORY_BASE_URL_ENV, "https://grimmory.example.com")
    monkeypatch.setattr(library_check, "sync_user_reading_status", lambda *a, **k: None)
    conn = models.get_connection(db_path)
    models.init_db(conn)
    conn.close()
    return TestClient(app)


def _make_user(name="Alice"):
    conn = models.get_connection()
    user = models.get_or_create_user(conn, name)
    conn.close()
    return user


def _logged_in_client(client):
    user = _make_user()
    client.cookies.set(COOKIE_NAME, sign_session_cookie(user.id))
    return user


# --- auth ---


def test_api_me_returns_null_when_anonymous(client):
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json() is None


def test_api_home_requires_login_returns_401_not_a_redirect(client):
    response = client.get("/api/home", follow_redirects=False)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_api_login_success_sets_cookie_and_returns_me(client, monkeypatch):
    monkeypatch.setattr(
        grimmory_auth, "login", lambda username, password: ("fake-token", "fake-refresh")
    )

    response = client.post("/api/login", json={"username": "Alice", "password": "hunter2"})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Alice"
    assert body["onboarded"] is False
    assert response.cookies.get(COOKIE_NAME) is not None

    me = client.get("/api/me").json()
    assert me == body


def test_api_login_invalid_credentials_returns_401(client, monkeypatch):
    def fail(username, password):
        raise grimmory_auth.GrimmoryLoginError("Invalid username or password")

    monkeypatch.setattr(grimmory_auth, "login", fail)

    response = client.post("/api/login", json={"username": "Alice", "password": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"
    assert response.cookies.get(COOKIE_NAME) is None


def test_api_me_reflects_admin_flag(client, monkeypatch):
    monkeypatch.setenv("TBR_ADMIN_USERNAME", "Alice")
    _logged_in_client(client)

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["is_admin"] is True


def test_api_logout_clears_cookie(client):
    _logged_in_client(client)
    response = client.post("/api/logout")
    assert response.status_code == 204
    # Matches test_main.py's test_logout_clears_cookie — check the response's own Set-Cookie
    # rather than a follow-up request; httpx's TestClient cookie jar doesn't always apply an
    # expired-cookie deletion to its persistent jar for later requests in this test environment.
    assert response.cookies.get(COOKIE_NAME) is None


# --- home / shelves ---


def test_api_home_lists_shelves(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune", author="Frank Herbert")
    models.add_tbr_entry(conn, user.id, book.id)
    conn.close()

    response = client.get("/api/home")

    assert response.status_code == 200
    shelves = {shelf["status"]: shelf for shelf in response.json()["shelves"]}
    assert [e["book"]["title"] for e in shelves["wanted"]["entries"]] == ["Dune"]
    assert shelves["reading"]["entries"] == []
    assert shelves["finished"]["entries"] == []


def test_api_shelf_rejects_unknown_status(client):
    _logged_in_client(client)
    response = client.get("/api/shelf/bogus")
    assert response.status_code == 404


def test_api_shelf_returns_matching_entries_only(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    wanted = models.create_book(conn, title="Wanted Book")
    reading = models.create_book(conn, title="Reading Book")
    models.add_tbr_entry(conn, user.id, wanted.id, status="wanted")
    models.add_tbr_entry(conn, user.id, reading.id, status="reading")
    conn.close()

    response = client.get("/api/shelf/reading")

    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "Currently Reading"
    assert [e["book"]["title"] for e in body["entries"]] == ["Reading Book"]


# --- onboarding ---


def test_api_onboarding_sets_goal_and_marks_onboarded(client):
    user = _logged_in_client(client)

    response = client.post("/api/onboarding", json={"target_count": 24})

    assert response.status_code == 200
    assert response.json()["onboarded"] is True
    conn = models.get_connection()
    goal = models.get_goal(conn, user.id, "year")
    conn.close()
    assert goal.target_count == 24


def test_api_onboarding_without_target_count_still_marks_onboarded(client):
    _logged_in_client(client)
    response = client.post("/api/onboarding", json={})
    assert response.status_code == 200
    assert response.json()["onboarded"] is True


# --- tbr entries ---


def test_api_add_to_tbr_creates_book_and_entry(client):
    user = _logged_in_client(client)

    response = client.post(
        "/api/tbr", json={"title": "Dune", "author": "Frank Herbert", "isbn": "9780441172719"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["book"]["title"] == "Dune"
    assert body["status"] == "wanted"

    conn = models.get_connection()
    entries = models.list_tbr_entries_with_books(conn, user.id)
    conn.close()
    assert len(entries) == 1


def test_api_add_to_tbr_downloads_real_cover_even_when_search_gave_a_placeholder(client, monkeypatch):
    # Regression test: a search result almost always carries a placeholder cover_url (e.g. an
    # Open Library thumbnail), which used to suppress the immediate Grimmory cover download
    # entirely - see library_check._has_local_cover.
    _logged_in_client(client)
    calls = []
    monkeypatch.setattr(
        main.library_check, "download_cover_for_book_now", lambda book_id, grimmory_id: calls.append((book_id, grimmory_id))
    )

    response = client.post(
        "/api/tbr",
        json={
            "title": "Dune",
            "author": "Frank Herbert",
            "isbn": "9780441172719",
            "cover_url": "https://covers.openlibrary.org/b/id/1-M.jpg",
            "grimmory_id": "42",
        },
    )

    assert response.status_code == 201
    book_id = response.json()["book"]["id"]
    assert calls == [(book_id, 42)]


def test_api_remove_from_tbr(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    conn.close()

    response = client.post(f"/api/tbr/{entry.id}/remove")

    assert response.status_code == 204
    conn = models.get_connection()
    assert models.get_tbr_entry(conn, entry.id) is None
    conn.close()


def test_api_cannot_remove_another_users_entry(client):
    owner = _make_user("Owner")
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    entry = models.add_tbr_entry(conn, owner.id, book.id)
    conn.close()

    _logged_in_client(client)  # logs in as a different user ("Alice")
    response = client.post(f"/api/tbr/{entry.id}/remove")

    assert response.status_code == 204
    conn = models.get_connection()
    assert models.get_tbr_entry(conn, entry.id) is not None
    conn.close()


def test_api_cannot_remove_finished_entry(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    models.set_tbr_entry_status(conn, entry.id, "finished", "2026-01-01T00:00:00+00:00")
    conn.close()

    response = client.post(f"/api/tbr/{entry.id}/remove")

    assert response.status_code == 204
    conn = models.get_connection()
    assert models.get_tbr_entry(conn, entry.id) is not None
    conn.close()


def test_api_set_tbr_dates_requires_ownership(client):
    owner = _make_user("Owner")
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    entry = models.add_tbr_entry(conn, owner.id, book.id, status="reading")
    conn.close()

    _logged_in_client(client)
    response = client.post(f"/api/tbr/{entry.id}/dates", json={"started_at": "2026-01-01"})

    assert response.status_code == 404


def test_api_set_tbr_dates_marks_started_at_manual(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    entry = models.add_tbr_entry(conn, user.id, book.id, status="reading")
    conn.close()

    response = client.post(f"/api/tbr/{entry.id}/dates", json={"started_at": "2026-01-01"})

    assert response.status_code == 200
    body = response.json()
    assert body["started_at"] == "2026-01-01"
    assert body["started_at_manual"] is True


def test_api_reorder_wanted_shelf(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    a = models.add_tbr_entry(conn, user.id, models.create_book(conn, title="A").id)
    b = models.add_tbr_entry(conn, user.id, models.create_book(conn, title="B").id)
    c = models.add_tbr_entry(conn, user.id, models.create_book(conn, title="C").id)
    conn.close()

    response = client.post("/api/shelf/wanted/reorder", json={"entry_ids": [b.id, c.id, a.id]})

    assert response.status_code == 200
    body = response.json()
    assert [e["book"]["title"] for e in body["entries"]] == ["B", "C", "A"]

    # Persisted, not just reflected in the immediate response.
    refreshed = client.get("/api/shelf/wanted")
    assert [e["book"]["title"] for e in refreshed.json()["entries"]] == ["B", "C", "A"]

    # Home page's wanted shelf reflects the same order.
    home = client.get("/api/home")
    home_wanted = next(s for s in home.json()["shelves"] if s["status"] == "wanted")
    assert [e["book"]["title"] for e in home_wanted["entries"]] == ["B", "C", "A"]


def test_api_reorder_wanted_shelf_cannot_touch_another_users_entry(client):
    owner = _make_user("Owner")
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    owners_entry = models.add_tbr_entry(conn, owner.id, book.id)
    conn.close()

    _logged_in_client(client)  # a different user ("Alice")
    response = client.post("/api/shelf/wanted/reorder", json={"entry_ids": [owners_entry.id]})

    assert response.status_code == 200  # silently no-ops the id it doesn't own, not an error
    conn = models.get_connection()
    untouched = models.get_tbr_entry(conn, owners_entry.id)
    conn.close()
    assert untouched.sort_order == owners_entry.sort_order


def test_api_reorder_wanted_shelf_ignores_non_wanted_entry_ids(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    reading_entry = models.add_tbr_entry(
        conn, user.id, models.create_book(conn, title="Reading Book").id, status="reading"
    )
    conn.close()

    response = client.post("/api/shelf/wanted/reorder", json={"entry_ids": [reading_entry.id]})

    assert response.status_code == 200
    conn = models.get_connection()
    untouched = models.get_tbr_entry(conn, reading_entry.id)
    conn.close()
    assert untouched.sort_order is None


# --- book detail ---


def test_api_book_detail_not_found_returns_404(client):
    _logged_in_client(client)
    response = client.get("/api/book/999")
    assert response.status_code == 404


def test_api_book_detail_includes_tiles_and_burndown(client, monkeypatch):
    user = _logged_in_client(client)
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    models.set_book_grimmory_id(conn, book.id, 42)
    entry = models.add_tbr_entry(conn, user.id, book.id, status="reading")
    models.set_tbr_entry_started_at(conn, entry.id, "2026-01-01", manual=True)
    models.set_grimmory_refresh_token(conn, user.id, "stored-refresh")
    conn.close()

    monkeypatch.setattr(grimmory_auth, "get_valid_access_token", lambda conn, u: "access-token")
    monkeypatch.setattr(
        library_check,
        "fetch_reading_sessions_for_book",
        lambda base_url, token, book_id: [
            {
                "startTime": "2026-01-01T10:00:00Z",
                "endProgress": 10.0,
                "progressDelta": 10.0,
                "durationSeconds": 600,
            }
        ],
    )

    response = client.get(f"/api/book/{entry.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["progress_percent"] == 10.0
    assert body["burndown"] == [
        {"date": "2025-12-31", "remaining_percent": 100},
        {"date": "2026-01-01", "remaining_percent": 90},
    ]
    assert any(t["label"] == "Reading Days" for t in body["tiles"])


# --- stats / calendar ---


def test_api_stats_counts_books_finished_this_year(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    entry = models.add_tbr_entry(conn, user.id, book.id)
    year = date.today().year
    models.set_tbr_entry_status(conn, entry.id, "finished", f"{year}-06-01T00:00:00+00:00")
    conn.close()

    response = client.get("/api/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["year"] == year
    assert body["finished_count"] == 1
    assert any(t["label"] == "Books finished" and t["value"] == "1" for t in body["tiles"])


def test_api_calendar_places_reading_span_on_grid(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    entry = models.add_tbr_entry(conn, user.id, book.id, status="reading")
    models.set_tbr_entry_started_at(conn, entry.id, "2026-08-05", manual=True)
    conn.close()

    response = client.get("/api/calendar", params={"month": "2026-08"})

    assert response.status_code == 200
    body = response.json()
    assert body["year"] == 2026
    assert body["month"] == 8
    assert [s["entry_id"] for s in body["spans"]] == [entry.id]

    cell = next(c for week in body["grid"] for c in week if c["date"] == "2026-08-05")
    assert cell["active_entry_ids"] == [entry.id]
    assert cell["cover_entry_ids"] == [entry.id]


# --- settings ---


def test_api_settings_returns_goal_and_spice_labels(client):
    user = _logged_in_client(client)
    conn = models.get_connection()
    models.upsert_goal(conn, user.id, "year", 12)
    conn.close()

    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["goal"]["target_count"] == 12
    assert body["grimmory_admin_configured"] is False
    assert len(body["spice_labels"]) == 6


def test_api_settings_goal_upserts(client):
    _logged_in_client(client)
    response = client.post("/api/settings/goal", json={"target_count": 30})
    assert response.status_code == 200
    assert response.json()["target_count"] == 30


def test_api_settings_sync_uses_stored_session_when_password_blank(client, monkeypatch):
    user = _logged_in_client(client)
    conn = models.get_connection()
    models.set_grimmory_refresh_token(conn, user.id, "stored-refresh")
    conn.close()

    monkeypatch.setattr(
        grimmory_auth, "get_valid_access_token", lambda conn, u: "refreshed-access-token"
    )
    sync_calls = []
    monkeypatch.setattr(
        library_check,
        "sync_user_reading_status",
        lambda conn, user_id, base_url, access_token: sync_calls.append(access_token),
    )

    response = client.post("/api/settings/sync", json={})

    assert response.status_code == 200
    assert response.json() == {"error": None}
    assert sync_calls == ["refreshed-access-token"]


def test_api_settings_sync_prompts_reconnect_when_no_valid_session(client, monkeypatch):
    _logged_in_client(client)
    monkeypatch.setattr(grimmory_auth, "get_valid_access_token", lambda conn, u: None)

    response = client.post("/api/settings/sync", json={})

    assert response.status_code == 200
    assert response.json() == {"error": "reconnect_needed"}


def _configure_grimmory_admin(username="kyle-admin", password="adminpass"):
    conn = models.get_connection()
    models.set_grimmory_admin_settings(conn, username=username, password=password)
    conn.close()


def test_api_settings_spice_sets_level_and_calls_sync(client, monkeypatch):
    user = _logged_in_client(client)
    _configure_grimmory_admin()
    monkeypatch.setattr(grimmory_auth, "_admin_login", lambda base_url, u, p: "admin-token")
    monkeypatch.setattr(grimmory_auth, "find_grimmory_user_id", lambda base_url, token, username: 42)
    sync_calls = []
    monkeypatch.setattr(
        grimmory_auth,
        "sync_restriction_level",
        lambda base_url, token, user_id, level: sync_calls.append((user_id, level)),
    )

    response = client.post("/api/settings/spice", json={"level": 3})

    assert response.status_code == 200
    assert response.json() == {"spice_level": 3, "error": None}
    assert sync_calls == [(42, 3)]


def test_api_settings_spice_clamps_out_of_range_level(client, monkeypatch):
    _logged_in_client(client)
    _configure_grimmory_admin()
    monkeypatch.setattr(grimmory_auth, "_admin_login", lambda base_url, u, p: "admin-token")
    monkeypatch.setattr(grimmory_auth, "find_grimmory_user_id", lambda base_url, token, username: 42)
    sync_calls = []
    monkeypatch.setattr(
        grimmory_auth, "sync_restriction_level", lambda base_url, token, user_id, level: sync_calls.append(level)
    )

    response = client.post("/api/settings/spice", json={"level": 99})

    assert response.status_code == 200
    assert sync_calls == [5]


def test_api_settings_shelf_fields_default_for_fresh_user(client):
    _logged_in_client(client)

    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["want_to_read_shelf_id"] is None
    assert body["sync_to_device_enabled"] is False
    assert body["sync_to_device_shelf_id"] is None


def test_api_settings_shelves_not_configured(client, monkeypatch):
    _logged_in_client(client)
    monkeypatch.delenv(grimmory_auth.GRIMMORY_BASE_URL_ENV, raising=False)

    response = client.get("/api/settings/shelves")

    assert response.status_code == 200
    body = response.json()
    assert body["shelves"] == []
    assert body["error"] == "Grimmory login is not configured"


def test_api_settings_shelves_prompts_reconnect_when_no_valid_session(client, monkeypatch):
    _logged_in_client(client)
    monkeypatch.setattr(grimmory_auth, "get_valid_access_token", lambda conn, u: None)

    response = client.get("/api/settings/shelves")

    assert response.status_code == 200
    body = response.json()
    assert body["shelves"] == []
    assert body["error"] == "reconnect_needed"


def test_api_settings_shelves_returns_own_shelves(client, monkeypatch):
    _logged_in_client(client)
    monkeypatch.setattr(grimmory_auth, "get_valid_access_token", lambda conn, u: "access-token")
    monkeypatch.setattr(grimmory_auth, "get_own_grimmory_user_id", lambda base_url, token: 7)
    monkeypatch.setattr(
        library_check,
        "list_own_shelves",
        lambda base_url, token, own_id: [
            {"id": 1, "name": "Want to Read", "userId": own_id},
            {"id": 2, "name": "Booknook: Sync to Device", "userId": own_id},
        ],
    )

    response = client.get("/api/settings/shelves")

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["shelves"] == [
        {"id": 1, "name": "Want to Read"},
        {"id": 2, "name": "Booknook: Sync to Device"},
    ]


def test_api_settings_shelves_surfaces_library_check_unavailable(client, monkeypatch):
    _logged_in_client(client)
    monkeypatch.setattr(grimmory_auth, "get_valid_access_token", lambda conn, u: "access-token")
    monkeypatch.setattr(grimmory_auth, "get_own_grimmory_user_id", lambda base_url, token: 7)

    def raise_unavailable(*a, **k):
        raise library_check.LibraryCheckUnavailable("shelf api down")

    monkeypatch.setattr(library_check, "list_own_shelves", raise_unavailable)

    response = client.get("/api/settings/shelves")

    assert response.status_code == 200
    body = response.json()
    assert body["shelves"] == []
    assert body["error"] == "shelf api down"


def test_api_settings_shelves_update_persists_and_round_trips(client):
    _logged_in_client(client)

    response = client.post(
        "/api/settings/shelves",
        json={
            "want_to_read_shelf_id": 1,
            "sync_to_device_enabled": True,
            "sync_to_device_shelf_id": 2,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "want_to_read_shelf_id": 1,
        "sync_to_device_enabled": True,
        "sync_to_device_shelf_id": 2,
    }

    settings = client.get("/api/settings").json()
    assert settings["want_to_read_shelf_id"] == 1
    assert settings["sync_to_device_enabled"] is True
    assert settings["sync_to_device_shelf_id"] == 2


# --- search ---


def test_api_search_library_finds_catalog_match(client):
    _logged_in_client(client)
    conn = models.get_connection()
    models.replace_library_catalog(
        conn,
        [
            models.LibraryCatalogEntry(
                title="Dune", isbn13="9780441172719", isbn10=None, authors=["Frank Herbert"]
            )
        ],
    )
    conn.close()

    response = client.get("/api/search/library", params={"q": "dune"})

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["title"] == "Dune"


def test_api_search_uses_hardcover_when_configured(client, monkeypatch):
    _logged_in_client(client)
    conn = models.get_connection()
    models.set_search_settings(conn, hardcover_api_key="test-key")
    conn.close()
    monkeypatch.setattr(
        main,
        "search_hardcover",
        lambda q, key: [SearchResult(title="Dune (Hardcover)", author="Frank Herbert", isbn="123", cover_url=None)],
    )

    response = client.get("/api/search", params={"q": "dune"})

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["title"] == "Dune (Hardcover)"
    assert body["show_more"] is True


def test_api_search_shows_fallback_error_on_hardcover_failure(client, monkeypatch):
    _logged_in_client(client)
    conn = models.get_connection()
    models.set_search_settings(conn, hardcover_api_key="test-key")
    conn.close()

    def fail(q, key):
        raise hardcover.HardcoverSearchError("boom")

    monkeypatch.setattr(main, "search_hardcover", fail)

    response = client.get("/api/search", params={"q": "dune"})

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is True
    assert "Hardcover search failed" in body["error_message"]


def test_api_search_more_returns_open_library_results(client, monkeypatch):
    _logged_in_client(client)
    monkeypatch.setattr(
        main,
        "search_books",
        lambda q: [SearchResult(title="Dune (Open Library)", author="Frank Herbert", isbn="123", cover_url=None)],
    )

    response = client.get("/api/search/more", params={"q": "dune"})

    assert response.status_code == 200
    assert response.json()["results"][0]["title"] == "Dune (Open Library)"


def test_api_search_handles_upstream_failure(client, monkeypatch):
    _logged_in_client(client)

    def raise_error(q):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(main, "search_books", raise_error)

    response = client.get("/api/search", params={"q": "dune"})

    assert response.status_code == 200
    assert response.json()["error"] is True


# --- preferences ---


def test_api_set_view_preference_persists_choice(client):
    user = _logged_in_client(client)
    response = client.post("/api/preferences/view", json={"view": "cover"})
    assert response.status_code == 204
    conn = models.get_connection()
    stored = models.get_user(conn, user.id)
    conn.close()
    assert stored.view_preference == "cover"


def test_api_set_view_preference_rejects_invalid_value(client):
    _logged_in_client(client)
    response = client.post("/api/preferences/view", json={"view": "bogus"})
    assert response.status_code == 422


# --- admin (no in-app auth, matching the HTML routes' posture) ---


def test_api_admin_shows_aggregate_entries_without_login(client):
    user = _make_user("Bob")
    conn = models.get_connection()
    book = models.create_book(conn, title="Dune")
    models.add_tbr_entry(conn, user.id, book.id)
    conn.close()

    response = client.get("/api/admin")

    assert response.status_code == 200
    body = response.json()
    assert body["needed_entries"][0]["title"] == "Dune"
    assert body["needed_entries"][0]["wanted_by"] == ["Bob"]


def test_api_admin_settings_never_leaks_stored_passwords(client):
    conn = models.get_connection()
    models.set_library_settings(
        conn, base_url="https://old.example.com", username="tbr-sync", password="hunter2", sync_interval_minutes=30
    )
    models.set_grimmory_admin_settings(conn, username="admin", password="adminpass")
    models.set_search_settings(conn, hardcover_api_key="secret-key")
    conn.close()

    response = client.get("/api/admin/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["library_settings"]["password_set"] is True
    assert "password" not in body["library_settings"]
    assert "hunter2" not in response.text
    assert body["grimmory_admin_settings"]["password_set"] is True
    assert "adminpass" not in response.text
    assert body["hardcover_api_key_set"] is True
    assert "secret-key" not in response.text


def test_api_admin_settings_save_blank_password_keeps_existing_password(client):
    conn = models.get_connection()
    models.set_library_settings(
        conn, base_url="https://old.example.com", username="tbr-sync", password="hunter2", sync_interval_minutes=30
    )
    conn.close()

    response = client.post(
        "/api/admin/settings",
        json={"base_url": "https://new.example.com", "username": "tbr-sync", "sync_interval_minutes": 45},
    )

    assert response.status_code == 200
    assert response.json()["library_settings"]["base_url"] == "https://new.example.com"
    conn = models.get_connection()
    settings = models.get_library_settings(conn)
    conn.close()
    assert settings.password == "hunter2"
    assert settings.sync_interval_minutes == 45
