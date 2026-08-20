import httpx
import pytest

from app import grimmory_auth
from app.models import (
    User,
    get_connection,
    get_or_create_user,
    init_db,
    set_grimmory_refresh_token,
)


class FakeResponse:
    def __init__(self, status_code, access_token="fake-token", refresh_token="fake-refresh"):
        self.status_code = status_code
        self._access_token = access_token
        self._refresh_token = refresh_token

    def json(self):
        return {"accessToken": self._access_token, "refreshToken": self._refresh_token}


@pytest.fixture
def base_url(monkeypatch):
    monkeypatch.setenv(grimmory_auth.GRIMMORY_BASE_URL_ENV, "https://grimmory.example.com")


@pytest.fixture
def conn():
    connection = get_connection(":memory:")
    init_db(connection)
    yield connection
    connection.close()


def test_login_raises_when_not_configured(monkeypatch):
    monkeypatch.delenv(grimmory_auth.GRIMMORY_BASE_URL_ENV, raising=False)

    with pytest.raises(grimmory_auth.GrimmoryLoginError):
        grimmory_auth.login("kyle", "hunter2")


def test_login_succeeds(base_url, monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        return FakeResponse(200, access_token="abc123", refresh_token="refresh-abc")

    monkeypatch.setattr(grimmory_auth.httpx, "post", fake_post)

    access_token, refresh_token = grimmory_auth.login("kyle", "hunter2")

    assert access_token == "abc123"
    assert refresh_token == "refresh-abc"
    assert calls[0]["url"] == "https://grimmory.example.com/api/v1/auth/login"
    assert calls[0]["json"] == {"username": "kyle", "password": "hunter2"}


@pytest.mark.parametrize("status_code", [400, 401])
def test_login_raises_on_invalid_credentials(base_url, monkeypatch, status_code):
    monkeypatch.setattr(grimmory_auth.httpx, "post", lambda *a, **k: FakeResponse(status_code))

    with pytest.raises(grimmory_auth.GrimmoryLoginError, match="Invalid username or password"):
        grimmory_auth.login("kyle", "wrong-password")


def test_login_raises_on_server_error(base_url, monkeypatch):
    monkeypatch.setattr(grimmory_auth.httpx, "post", lambda *a, **k: FakeResponse(500))

    with pytest.raises(grimmory_auth.GrimmoryLoginError):
        grimmory_auth.login("kyle", "hunter2")


def test_login_raises_when_unreachable(base_url, monkeypatch):
    def raise_error(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(grimmory_auth.httpx, "post", raise_error)

    with pytest.raises(grimmory_auth.GrimmoryLoginError):
        grimmory_auth.login("kyle", "hunter2")


def test_refresh_returns_new_token_pair(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        return FakeResponse(200, access_token="new-access", refresh_token="new-refresh")

    monkeypatch.setattr(grimmory_auth.httpx, "post", fake_post)

    access_token, refresh_token = grimmory_auth.refresh(
        "https://grimmory.example.com", "old-refresh"
    )

    assert access_token == "new-access"
    assert refresh_token == "new-refresh"
    assert calls[0]["url"] == "https://grimmory.example.com/api/v1/auth/refresh"
    assert calls[0]["json"] == {"refreshToken": "old-refresh"}


def test_refresh_raises_on_rejected_token(monkeypatch):
    monkeypatch.setattr(grimmory_auth.httpx, "post", lambda *a, **k: FakeResponse(400))

    with pytest.raises(grimmory_auth.GrimmoryLoginError):
        grimmory_auth.refresh("https://grimmory.example.com", "stale-refresh")


def test_get_valid_access_token_returns_none_when_never_connected(base_url, conn):
    user = get_or_create_user(conn, "kyle")

    assert grimmory_auth.get_valid_access_token(conn, user) is None


def test_get_valid_access_token_returns_none_when_unconfigured(monkeypatch, conn):
    monkeypatch.delenv(grimmory_auth.GRIMMORY_BASE_URL_ENV, raising=False)
    user = User(id=1, name="kyle", grimmory_refresh_token="some-token")

    assert grimmory_auth.get_valid_access_token(conn, user) is None


def test_get_valid_access_token_refreshes_and_persists_rotated_token(base_url, conn, monkeypatch):
    created = get_or_create_user(conn, "kyle")
    from app.models import set_grimmory_refresh_token

    set_grimmory_refresh_token(conn, created.id, "old-refresh")
    user = User(id=created.id, name="kyle", grimmory_refresh_token="old-refresh")

    monkeypatch.setattr(
        grimmory_auth,
        "refresh",
        lambda base_url, refresh_token: ("fresh-access", "rotated-refresh"),
    )

    access_token = grimmory_auth.get_valid_access_token(conn, user)

    assert access_token == "fresh-access"
    assert user.grimmory_refresh_token == "rotated-refresh"
    row = conn.execute(
        "SELECT grimmory_refresh_token FROM users WHERE id = ?", (created.id,)
    ).fetchone()
    assert row["grimmory_refresh_token"] == "rotated-refresh"


def test_get_valid_access_token_clears_stored_token_on_rejection(base_url, conn, monkeypatch):
    created = get_or_create_user(conn, "kyle")
    from app.models import set_grimmory_refresh_token

    set_grimmory_refresh_token(conn, created.id, "stale-refresh")
    user = User(id=created.id, name="kyle", grimmory_refresh_token="stale-refresh")

    def raise_login_error(base_url, refresh_token):
        raise grimmory_auth.GrimmoryLoginError("Grimmory session expired")

    monkeypatch.setattr(grimmory_auth, "refresh", raise_login_error)

    access_token = grimmory_auth.get_valid_access_token(conn, user)

    assert access_token is None
    assert user.grimmory_refresh_token is None
    row = conn.execute(
        "SELECT grimmory_refresh_token FROM users WHERE id = ?", (created.id,)
    ).fetchone()
    assert row["grimmory_refresh_token"] is None


def test_get_valid_access_token_uses_fresh_db_token_not_stale_caller_object(
    base_url, conn, monkeypatch
):
    # Regression test for a real bug (caught 2026-07-29 against a live account): two requests for
    # the same user each get their own freshly-loaded User object (see main.py:current_user), so
    # the second one's `.grimmory_refresh_token` can be stale by the time it's this function's
    # turn to run — it must re-read the DB inside the lock rather than trust that attribute,
    # since Grimmory's refresh tokens are single-use and would reject the stale one.
    created = get_or_create_user(conn, "kyle")
    set_grimmory_refresh_token(conn, created.id, "token-a")

    refresh_calls = []

    def fake_refresh(base_url, refresh_token):
        refresh_calls.append(refresh_token)
        if refresh_token == "token-a":
            return "access-1", "token-b"
        if refresh_token == "token-b":
            return "access-2", "token-c"
        raise grimmory_auth.GrimmoryLoginError("stale/rejected token")

    monkeypatch.setattr(grimmory_auth, "refresh", fake_refresh)

    # Both objects represent independent per-request loads of the same user, both still showing
    # the token as it was before either request ran.
    first_request_user = User(id=created.id, name="kyle", grimmory_refresh_token="token-a")
    second_request_user = User(id=created.id, name="kyle", grimmory_refresh_token="token-a")

    first_token = grimmory_auth.get_valid_access_token(conn, first_request_user)
    second_token = grimmory_auth.get_valid_access_token(conn, second_request_user)

    assert first_token == "access-1"
    assert second_token == "access-2"  # would be None (rejected) without the fix
    assert refresh_calls == ["token-a", "token-b"]
    row = conn.execute(
        "SELECT grimmory_refresh_token FROM users WHERE id = ?", (created.id,)
    ).fetchone()
    assert row["grimmory_refresh_token"] == "token-c"


# --- update_book_finished_date ---


class FakeHttpResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def test_update_book_finished_date_posts_expected_body(monkeypatch):
    import datetime as dt

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return FakeHttpResponse(200)

    monkeypatch.setattr(grimmory_auth.httpx, "post", fake_post)

    grimmory_auth.update_book_finished_date(
        "https://grimmory.example.com", "access-token", 160, dt.date(2026, 1, 11)
    )

    assert calls[0]["url"] == "https://grimmory.example.com/api/v1/books/progress"
    assert calls[0]["json"] == {"bookId": 160, "dateFinished": "2026-01-11T00:00:00Z"}
    assert calls[0]["headers"] == {"Authorization": "Bearer access-token"}


def test_update_book_finished_date_raises_on_http_failure(monkeypatch):
    import datetime as dt

    monkeypatch.setattr(
        grimmory_auth.httpx, "post", lambda *a, **k: FakeHttpResponse(500)
    )

    with pytest.raises(grimmory_auth.LibraryCheckUnavailable):
        grimmory_auth.update_book_finished_date(
            "https://grimmory.example.com", "access-token", 160, dt.date(2026, 1, 11)
        )


# --- get_own_grimmory_user_id ---


class FakeMeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload


def test_get_own_grimmory_user_id_returns_id(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        return FakeMeResponse({"id": 42, "username": "kyle"})

    monkeypatch.setattr(grimmory_auth.httpx, "get", fake_get)

    assert grimmory_auth.get_own_grimmory_user_id("https://grimmory.example.com", "token") == 42
    assert calls[0]["url"] == "https://grimmory.example.com/api/v1/users/me"
    assert calls[0]["headers"] == {"Authorization": "Bearer token"}


def test_get_own_grimmory_user_id_raises_on_http_failure(monkeypatch):
    monkeypatch.setattr(grimmory_auth.httpx, "get", lambda *a, **k: FakeMeResponse({}, 500))

    with pytest.raises(grimmory_auth.LibraryCheckUnavailable):
        grimmory_auth.get_own_grimmory_user_id("https://grimmory.example.com", "token")


def test_get_own_grimmory_user_id_raises_when_id_missing(monkeypatch):
    # A well-formed but unexpected payload (missing "id") must raise LibraryCheckUnavailable, not
    # a bare KeyError.
    monkeypatch.setattr(
        grimmory_auth.httpx, "get", lambda *a, **k: FakeMeResponse({"username": "kyle"})
    )

    with pytest.raises(grimmory_auth.LibraryCheckUnavailable):
        grimmory_auth.get_own_grimmory_user_id("https://grimmory.example.com", "token")


def test_get_own_grimmory_user_id_raises_on_invalid_json(monkeypatch):
    class NonJsonResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not JSON")

    monkeypatch.setattr(grimmory_auth.httpx, "get", lambda *a, **k: NonJsonResponse())

    with pytest.raises(grimmory_auth.LibraryCheckUnavailable):
        grimmory_auth.get_own_grimmory_user_id("https://grimmory.example.com", "token")


# --- admin-privileged actions (content restrictions / the spice scale) ---


class FakeAdminResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload


class RecordingTransport:
    """Fakes httpx at the transport level so both bare httpx.get/put/post and httpx.Client(...)
    calls in the admin-actions section of grimmory_auth.py are exercised uniformly."""

    def __init__(self, responder):
        self._responder = responder
        self.calls = []

    def __call__(self, method, url, json=None, headers=None, **kwargs):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers})
        return self._responder(method, url, json)


@pytest.fixture
def patched_admin_httpx(monkeypatch):
    responder_holder = {}

    def install(responder):
        transport = RecordingTransport(responder)
        responder_holder["transport"] = transport

        def fake_get(url, headers=None, timeout=None):
            return transport("GET", url, headers=headers)

        def fake_put(url, json=None, headers=None, timeout=None):
            return transport("PUT", url, json=json, headers=headers)

        def fake_post(url, json=None, timeout=None):
            return transport("POST", url, json=json)

        class FakeClient:
            def __init__(self, base_url="", timeout=None):
                self.base_url = base_url

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, path, headers=None):
                return transport("GET", self.base_url + path, headers=headers)

            def put(self, path, json=None, headers=None):
                return transport("PUT", self.base_url + path, json=json, headers=headers)

        monkeypatch.setattr(grimmory_auth.httpx, "get", fake_get)
        monkeypatch.setattr(grimmory_auth.httpx, "put", fake_put)
        monkeypatch.setattr(grimmory_auth.httpx, "post", fake_post)
        monkeypatch.setattr(grimmory_auth.httpx, "Client", FakeClient)
        return transport

    return install


ADMIN_BASE_URL = "https://grimmory.example.com"


def test_find_grimmory_user_id_matches_by_username(patched_admin_httpx):
    users = [{"id": 1, "username": "alice"}, {"id": 2, "username": "bob"}]
    patched_admin_httpx(lambda method, url, json=None: FakeAdminResponse(users))

    assert grimmory_auth.find_grimmory_user_id(ADMIN_BASE_URL, "admin-token", "bob") == 2


def test_find_grimmory_user_id_returns_none_when_not_found(patched_admin_httpx):
    patched_admin_httpx(
        lambda method, url, json=None: FakeAdminResponse([{"id": 1, "username": "alice"}])
    )

    assert grimmory_auth.find_grimmory_user_id(ADMIN_BASE_URL, "admin-token", "carol") is None


@pytest.mark.parametrize(
    "spice_level,expected_threshold",
    [(0, "10"), (1, "13"), (2, "16"), (3, "18"), (4, "21")],
)
def test_sync_restriction_level_sets_threshold_for_levels_0_to_4(
    patched_admin_httpx, spice_level, expected_threshold
):
    current = [{"id": 1, "restrictionType": "TAG", "mode": "EXCLUDE", "value": "spoilers"}]
    put_bodies = []

    def responder(method, url, json=None):
        if method == "GET":
            return FakeAdminResponse(current)
        put_bodies.append(json)
        return FakeAdminResponse(json)

    patched_admin_httpx(responder)

    grimmory_auth.sync_restriction_level(ADMIN_BASE_URL, "admin-token", 7, spice_level)

    body = put_bodies[0]
    age_rows = [r for r in body if r["restrictionType"] == "AGE_RATING"]
    assert len(age_rows) == 1
    assert age_rows[0]["mode"] == "EXCLUDE"
    assert age_rows[0]["value"] == expected_threshold
    # Unrelated restriction (TAG/spoilers) survives untouched.
    assert any(r["restrictionType"] == "TAG" and r["value"] == "spoilers" for r in body)


def test_sync_restriction_level_level_5_removes_age_restriction(patched_admin_httpx):
    current = [
        {"id": 1, "restrictionType": "AGE_RATING", "mode": "EXCLUDE", "value": "18"},
        {"id": 2, "restrictionType": "CATEGORY", "mode": "EXCLUDE", "value": "Horror"},
    ]
    put_bodies = []

    def responder(method, url, json=None):
        if method == "GET":
            return FakeAdminResponse(current)
        put_bodies.append(json)
        return FakeAdminResponse(json)

    patched_admin_httpx(responder)

    grimmory_auth.sync_restriction_level(ADMIN_BASE_URL, "admin-token", 7, 5)

    body = put_bodies[0]
    assert not any(r["restrictionType"] == "AGE_RATING" for r in body)
    # Unrelated restriction (CATEGORY/Horror) survives untouched.
    assert any(r["restrictionType"] == "CATEGORY" and r["value"] == "Horror" for r in body)


def test_sync_restriction_level_replaces_existing_age_rating_row(patched_admin_httpx):
    current = [{"id": 1, "restrictionType": "AGE_RATING", "mode": "EXCLUDE", "value": "13"}]
    put_bodies = []

    def responder(method, url, json=None):
        if method == "GET":
            return FakeAdminResponse(current)
        put_bodies.append(json)
        return FakeAdminResponse(json)

    patched_admin_httpx(responder)

    grimmory_auth.sync_restriction_level(ADMIN_BASE_URL, "admin-token", 7, 3)

    body = put_bodies[0]
    age_rows = [r for r in body if r["restrictionType"] == "AGE_RATING"]
    assert len(age_rows) == 1
    assert age_rows[0]["value"] == "18"


def test_sync_restriction_level_ignores_allow_only_age_rating_row(patched_admin_httpx):
    # An AGE_RATING row in ALLOW_ONLY mode is a different concept from our EXCLUDE-based scale —
    # must not be touched or duplicated against.
    current = [{"id": 1, "restrictionType": "AGE_RATING", "mode": "ALLOW_ONLY", "value": "18"}]
    put_bodies = []

    def responder(method, url, json=None):
        if method == "GET":
            return FakeAdminResponse(current)
        put_bodies.append(json)
        return FakeAdminResponse(json)

    patched_admin_httpx(responder)

    grimmory_auth.sync_restriction_level(ADMIN_BASE_URL, "admin-token", 7, 2)

    body = put_bodies[0]
    allow_only_rows = [r for r in body if r["mode"] == "ALLOW_ONLY"]
    exclude_rows = [r for r in body if r["restrictionType"] == "AGE_RATING" and r["mode"] == "EXCLUDE"]
    assert len(allow_only_rows) == 1
    assert len(exclude_rows) == 1
    assert exclude_rows[0]["value"] == "16"
