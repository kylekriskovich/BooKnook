import threading
import time

import httpx
import pytest

from app import grimmory_auth, library_check
from app.models import (
    User,
    get_connection,
    get_or_create_user,
    init_db,
    set_grimmory_refresh_token,
)


class FakeResponse:
    def __init__(
        self, status_code, access_token="fake-token", refresh_token="fake-refresh", expires=7200
    ):
        self.status_code = status_code
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expires = expires

    def json(self):
        return {
            "accessToken": self._access_token,
            "refreshToken": self._refresh_token,
            "expires": self._expires,
        }

    @property
    def content(self):
        return str(self.json()).encode()


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

    access_token, refresh_token, expires_in = grimmory_auth.login("kyle", "hunter2")

    assert access_token == "abc123"
    assert refresh_token == "refresh-abc"
    assert expires_in == 7200
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

    access_token, refresh_token, expires_in = grimmory_auth.refresh(
        "https://grimmory.example.com", "old-refresh"
    )

    assert access_token == "new-access"
    assert refresh_token == "new-refresh"
    assert expires_in == 7200
    assert calls[0]["url"] == "https://grimmory.example.com/api/v1/auth/refresh"
    assert calls[0]["json"] == {"refreshToken": "old-refresh"}


def test_refresh_raises_on_rejected_token(monkeypatch):
    monkeypatch.setattr(grimmory_auth.httpx, "post", lambda *a, **k: FakeResponse(400))

    with pytest.raises(grimmory_auth.GrimmoryLoginError):
        grimmory_auth.refresh("https://grimmory.example.com", "stale-refresh")


def test_get_valid_access_token_returns_none_when_never_connected(base_url, conn):
    user = get_or_create_user(conn, "kyle")

    assert grimmory_auth.get_valid_access_token(conn, user) is None


# --- access-token caching (cache_access_token / get_valid_access_token's cache check) ---


def test_cache_access_token_then_get_valid_access_token_skips_refresh(base_url, conn, monkeypatch):
    created = get_or_create_user(conn, "kyle")
    set_grimmory_refresh_token(conn, created.id, "some-refresh")
    user = User(id=created.id, name="kyle", grimmory_refresh_token="some-refresh")

    def fail_if_called(*a, **k):
        raise AssertionError("refresh() must not be called - a cached token is still fresh")

    monkeypatch.setattr(grimmory_auth, "refresh", fail_if_called)
    grimmory_auth.cache_access_token(created.id, "cached-access", 7200)

    assert grimmory_auth.get_valid_access_token(conn, user) == "cached-access"


def test_get_valid_access_token_refreshes_when_no_cached_token(base_url, conn, monkeypatch):
    created = get_or_create_user(conn, "kyle")
    set_grimmory_refresh_token(conn, created.id, "some-refresh")
    user = User(id=created.id, name="kyle", grimmory_refresh_token="some-refresh")

    monkeypatch.setattr(
        grimmory_auth, "refresh", lambda base_url, refresh_token: ("fresh-access", "new-refresh", 7200)
    )

    assert grimmory_auth.get_valid_access_token(conn, user) == "fresh-access"


def test_get_valid_access_token_refreshes_when_cached_token_is_stale(base_url, conn, monkeypatch):
    created = get_or_create_user(conn, "kyle")
    set_grimmory_refresh_token(conn, created.id, "some-refresh")
    user = User(id=created.id, name="kyle", grimmory_refresh_token="some-refresh")

    # A cache entry whose deadline is already in the past (simulating one that's expired) - past
    # calls that populated it with a real elapsed clock aren't reproducible here, so this reaches
    # into the cache dict directly the same way cache_access_token itself would compute a deadline.
    grimmory_auth._access_token_cache[created.id] = ("stale-cached-access", 0.0)
    monkeypatch.setattr(
        grimmory_auth, "refresh", lambda base_url, refresh_token: ("fresh-access", "new-refresh", 7200)
    )

    assert grimmory_auth.get_valid_access_token(conn, user) == "fresh-access"


def test_cache_access_token_skips_caching_when_expires_in_is_none(base_url, conn, monkeypatch):
    created = get_or_create_user(conn, "kyle")
    set_grimmory_refresh_token(conn, created.id, "some-refresh")
    user = User(id=created.id, name="kyle", grimmory_refresh_token="some-refresh")

    refresh_calls = []
    grimmory_auth.cache_access_token(created.id, "ignored", None)
    monkeypatch.setattr(
        grimmory_auth,
        "refresh",
        lambda base_url, refresh_token: refresh_calls.append(1) or ("fresh-access", "new-refresh", None),
    )

    assert grimmory_auth.get_valid_access_token(conn, user) == "fresh-access"
    assert refresh_calls == [1]


def test_cache_access_token_skips_caching_when_expires_in_within_safety_margin(base_url, conn, monkeypatch):
    created = get_or_create_user(conn, "kyle")
    set_grimmory_refresh_token(conn, created.id, "some-refresh")
    user = User(id=created.id, name="kyle", grimmory_refresh_token="some-refresh")

    refresh_calls = []
    # Shorter than _ACCESS_TOKEN_SAFETY_MARGIN_SECONDS - not worth caching at all.
    grimmory_auth.cache_access_token(created.id, "ignored", 60)
    monkeypatch.setattr(
        grimmory_auth,
        "refresh",
        lambda base_url, refresh_token: refresh_calls.append(1) or ("fresh-access", "new-refresh", None),
    )

    assert grimmory_auth.get_valid_access_token(conn, user) == "fresh-access"
    assert refresh_calls == [1]


# --- LibraryCheckUnavailable.is_auth_rejection / from_http_error ---


def test_is_auth_rejection_true_for_401_and_403():
    assert library_check.LibraryCheckUnavailable("x", status_code=401).is_auth_rejection
    assert library_check.LibraryCheckUnavailable("x", status_code=403).is_auth_rejection


def test_is_auth_rejection_false_for_other_statuses_and_none():
    assert not library_check.LibraryCheckUnavailable("x", status_code=500).is_auth_rejection
    assert not library_check.LibraryCheckUnavailable("x", status_code=409).is_auth_rejection
    assert not library_check.LibraryCheckUnavailable("x").is_auth_rejection


def test_from_http_error_carries_status_code_from_http_status_error():
    request = httpx.Request("GET", "https://example.test/x")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("boom", request=request, response=response)

    unavailable = library_check.LibraryCheckUnavailable.from_http_error(exc, "message")

    assert unavailable.status_code == 401
    assert unavailable.is_auth_rejection


def test_from_http_error_has_no_status_code_for_connection_errors():
    request = httpx.Request("GET", "https://example.test/x")
    exc = httpx.ConnectError("refused", request=request)

    unavailable = library_check.LibraryCheckUnavailable.from_http_error(exc, "message")

    assert unavailable.status_code is None
    assert not unavailable.is_auth_rejection


# --- evict_access_token ---


def test_evict_access_token_removes_matching_cache_entry():
    grimmory_auth.cache_access_token(1, "the-token", 7200)

    grimmory_auth.evict_access_token("the-token")

    assert grimmory_auth._access_token_cache.get(1) is None


def test_evict_access_token_ignores_non_matching_value():
    grimmory_auth.cache_access_token(1, "the-token", 7200)

    grimmory_auth.evict_access_token("some-other-token")

    cached = grimmory_auth._access_token_cache.get(1)
    assert cached is not None and cached[0] == "the-token"


def test_evicted_access_token_is_not_reused_by_get_valid_access_token(base_url, conn, monkeypatch):
    created = get_or_create_user(conn, "kyle")
    set_grimmory_refresh_token(conn, created.id, "some-refresh")
    user = User(id=created.id, name="kyle", grimmory_refresh_token="some-refresh")

    grimmory_auth.cache_access_token(created.id, "rejected-by-grimmory", 7200)
    grimmory_auth.evict_access_token("rejected-by-grimmory")

    monkeypatch.setattr(
        grimmory_auth,
        "refresh",
        lambda base_url, refresh_token: ("fresh-access", "new-refresh", 7200),
    )

    assert grimmory_auth.get_valid_access_token(conn, user) == "fresh-access"


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
        lambda base_url, refresh_token: ("fresh-access", "rotated-refresh", None),
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
            return "access-1", "token-b", None
        if refresh_token == "token-b":
            return "access-2", "token-c", None
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


# --- refresh_lock ---


def test_refresh_lock_is_the_same_lock_get_valid_access_token_uses_internally():
    # refresh_lock's whole point is that a caller writing a refresh token directly (see
    # app/main.py's /api/login) can't race get_valid_access_token's own refresh() - that only
    # holds if they're actually contending for the exact same lock object, not just two separate
    # locks that happen to share a name.
    assert grimmory_auth.refresh_lock(42) is grimmory_auth._refresh_locks[42]


def test_refresh_lock_blocks_concurrent_get_valid_access_token_for_same_user(
    base_url, conn, monkeypatch
):
    created = get_or_create_user(conn, "kyle")
    set_grimmory_refresh_token(conn, created.id, "some-refresh")
    user = User(id=created.id, name="kyle", grimmory_refresh_token="some-refresh")

    monkeypatch.setattr(
        grimmory_auth,
        "refresh",
        lambda base_url, refresh_token: ("fresh-access", "new-refresh", 7200),
    )

    entered_get_valid_access_token = threading.Event()
    release_refresh_lock = threading.Event()
    result: dict = {}

    def hold_refresh_lock_like_api_login():
        with grimmory_auth.refresh_lock(created.id):
            entered_get_valid_access_token.set()  # signal the other thread it's safe to try now
            # Blocks here exactly like /api/login's DB write would - proves a concurrent
            # get_valid_access_token call genuinely can't proceed past its own lock acquisition
            # while this "login" is still writing.
            release_refresh_lock.wait(timeout=2)

    def call_get_valid_access_token():
        entered_get_valid_access_token.wait(timeout=2)
        result["token"] = grimmory_auth.get_valid_access_token(conn, user)

    holder = threading.Thread(target=hold_refresh_lock_like_api_login)
    caller = threading.Thread(target=call_get_valid_access_token)
    holder.start()
    holder.join(timeout=0.2)  # give the holder time to actually acquire the lock first
    caller.start()

    # While the "login" thread still holds the lock, get_valid_access_token must not have
    # returned yet - it's blocked waiting for the same lock, not racing past it.
    time.sleep(0.1)
    assert "token" not in result

    release_refresh_lock.set()
    holder.join(timeout=2)
    caller.join(timeout=2)

    assert result.get("token") == "fresh-access"


# --- update_book_finished_date ---


class FakeHttpResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.content = b"{}"

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
        self.content = str(payload).encode()

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
        content = b"<html>not json</html>"

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
        self.content = str(payload).encode()

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
