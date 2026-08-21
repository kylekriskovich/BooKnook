# Everything that authenticates against Grimmory on behalf of a *person* rather than the
# dedicated read-only sync account: TBR's user-facing sign-in, the shared per-user session helper
# that keeps a user's Grimmory refresh token usable without re-prompting for their password on
# every action (see get_valid_access_token), and the separate *admin*-privileged actions (content
# restrictions for the spice scale) that need Grimmory admin rights on a user's behalf.
# Separate from app/library_check.py, which authenticates as its own dedicated read-only user for
# the admin catalog sync - that's a third, distinct account type from the two here.

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import date
from typing import Optional

import httpx

from app import grimmory_http
from app.library_check import LOGIN_PATH, LibraryCheckUnavailable
from app.models import User, get_grimmory_admin_settings, get_user, set_grimmory_refresh_token

logger = logging.getLogger(__name__)

GRIMMORY_BASE_URL_ENV = "GRIMMORY_BASE_URL"
REFRESH_PATH = "/api/v1/auth/refresh"
BOOK_PROGRESS_PATH = "/api/v1/books/progress"
USERS_PATH = "/api/v1/users"
USERS_ME_PATH = "/api/v1/users/me"
CONTENT_RESTRICTIONS_PATH = "/api/v1/users/{user_id}/content-restrictions"

# ApiError.INVALID_CREDENTIALS maps to 400 in Grimmory's own source, but a bad username/password
# never reaches that app-level handler in practice - Spring Security's authentication filter chain
# rejects it first and its default entry point returns 401 instead. Confirmed against a live
# instance. Treat both as "bad credentials" in case a future Grimmory version's filter chain
# changes it back.
INVALID_CREDENTIALS_STATUSES = {400, 401}

# Index = chili count (0-5); RESTRICTION_TIERS[level + 1] is the ageRating threshold to exclude
# for levels 0-4. Level 5 removes the restriction entirely rather than using a threshold.
RESTRICTION_TIERS = [6, 10, 13, 16, 18, 21]

# One lock per user_id, guarding get_valid_access_token below - Grimmory's refresh tokens rotate
# and are revoked on use, so two concurrent refresh attempts for the same user would otherwise
# race. Plain threading.Lock (not asyncio) since every route here runs in FastAPI's threadpool.
_refresh_locks: "defaultdict[int, threading.Lock]" = defaultdict(threading.Lock)


# Function Name: refresh_lock
# Description: The same per-user lock get_valid_access_token uses internally, exposed so callers
#   that write a user's stored refresh token / cached access token directly (see app/main.py's
#   /api/login and /api/settings/sync, right after a fresh grimmory_auth.login()) can hold it too.
#   Without this, a concurrent get_valid_access_token/refresh() call for the same user could read
#   the refresh token *just before* login's write lands, rotate it against Grimmory, and then write
#   its own (now-stale-relative-to-login) rotated pair back over login's fresh one - discarding the
#   session login just established. Not reentrant-safe to nest a second acquisition inside code
#   that itself calls get_valid_access_token for the same user (plain threading.Lock) - callers
#   should only wrap the direct write, not an entire operation that might call back in.
# Parameters:
# - user_id (int): Local user id to lock.
# Returns: threading.Lock
def refresh_lock(user_id: int) -> threading.Lock:
    return _refresh_locks[user_id]

# In-memory access-token cache, keyed by local user_id: (access_token, monotonic deadline).
# get_valid_access_token used to call Grimmory's refresh endpoint unconditionally on *every* call
# - every book-detail page view, every periodic sync cycle for every user - even though the access
# token it already had was still good for up to two hours (Grimmory's own accessTokenExpirationMs).
# That's a lot of avoidable refresh-endpoint traffic (rotating the stored refresh token every time
# too, for no reason), and this process-lifetime cache is what actually cuts it down: a cached,
# still-fresh token is reused directly instead of triggering another refresh. Deliberately in
# memory only, not persisted - losing it on restart just means the next call refreshes once, same
# as today's behavior.
_access_token_cache: "dict[int, tuple[str, float]]" = {}

# Refreshed this long before Grimmory's own expiry, not right up against it - a token that's about
# to expire mid-request is worse than refreshing a little early.
_ACCESS_TOKEN_SAFETY_MARGIN_SECONDS = 5 * 60


# Function Name: cache_access_token
# Description: Caches a freshly-issued access token for a user so a subsequent
#   get_valid_access_token call can reuse it instead of making an unnecessary refresh() call.
#   Called both by get_valid_access_token itself after a refresh, and by callers of login() (see
#   app/main.py) since a fresh login also hands back a perfectly good, not-yet-used access token.
# Parameters:
# - user_id (int): Local user id.
# - access_token (str): The access token to cache.
# - expires_in (Optional[int]): Seconds until Grimmory considers this token expired, per its own
#   response - None (or anything too small to be worth caching) just skips caching, falling back
#   to today's behavior of refreshing on the next call.
# Returns: None
def cache_access_token(user_id: int, access_token: str, expires_in: Optional[int]) -> None:
    if not expires_in or expires_in <= _ACCESS_TOKEN_SAFETY_MARGIN_SECONDS:
        _access_token_cache.pop(user_id, None)
        return
    _access_token_cache[user_id] = (
        access_token, time.monotonic() + expires_in - _ACCESS_TOKEN_SAFETY_MARGIN_SECONDS
    )


# Function Name: evict_access_token
# Description: Evicts a cached access token by its own value, if it's still the cached one for
#   whichever user it belongs to. Callers that use a get_valid_access_token result to make further
#   Grimmory calls (see app/main.py, app/library_check.py) should call this whenever one of those
#   calls comes back rejected - our own cached deadline can't know that Grimmory invalidated a
#   token early (a signing-key rotation, an explicit revocation, anything not captured by simple
#   expiry), so without this a rejected-but-not-yet-"expired" token would keep getting handed back
#   and keep failing for up to ~2 hours instead of the next attempt getting a real refresh.
#   Matches by token value (not user_id) since callers several layers down from
#   get_valid_access_token only ever see the token string itself, not which local user it's for.
# Parameters:
# - access_token (str): The access token that was rejected.
# Returns: None
def evict_access_token(access_token: str) -> None:
    stale_user_ids = [
        user_id
        for user_id, (cached_token, _deadline) in _access_token_cache.items()
        if cached_token == access_token
    ]
    for user_id in stale_user_ids:
        _access_token_cache.pop(user_id, None)


# TEMPORARY diagnostic (see the 2026-08-21 investigation: a household user's Grimmory session
# kept getting force-invalidated seconds after a fresh login, with no explanation found in
# Grimmory's own audit log). log_token_write/_token_fingerprint below let the logs show, per
# user_id, exactly which refresh token was written by which code path and which one was read back
# out before being submitted to Grimmory - so a live recurrence will show whether a failed refresh
# used the token we *think* is current, or something already overwrote it first. Remove once the
# root cause is confirmed.


# Function Name: _token_fingerprint
# Description: Short, non-reversible identifier for a refresh token, safe to log - lets diagnostic
#   log lines confirm whether two operations touched the *same* token without ever logging the
#   actual secret.
# Parameters:
# - token (Optional[str]): Refresh token value, or None.
# Returns: 12-char hex fingerprint (str), or "none" if token is falsy.
def _token_fingerprint(token: Optional[str]) -> str:
    if not token:
        return "none"
    return hashlib.sha256(token.encode()).hexdigest()[:12]


# Function Name: log_token_write
# Description: Logs a fingerprint of a refresh token every time one is written to the DB, tagged
#   with which code path wrote it and for which local user.
# Parameters:
# - user_id (int): Local user id the token belongs to.
# - token (Optional[str]): The refresh token being written (None when clearing a rejected one).
# - source (str): Which code path performed the write, e.g. "api_login".
# Returns: None
def log_token_write(user_id: int, token: Optional[str], source: str) -> None:
    logger.info(
        "Grimmory refresh token WRITE user_id=%s source=%s fingerprint=%s",
        user_id, source, _token_fingerprint(token),
    )


class GrimmoryLoginError(Exception):
    """Raised on any login/refresh failure. Message is safe to show directly to the user."""


# --- regular-user login/session ---

# Function Name: login
# Description: Validates a username/password against Grimmory.
# Parameters:
# - username (str): Grimmory username.
# - password (str): Grimmory password.
# Returns: Tuple of (access_token, refresh_token, expires_in_seconds) on success. expires_in_seconds
#   is None if Grimmory's response didn't include one (older/customized instance) - callers should
#   treat that as "don't cache" (see cache_access_token).
def login(username: str, password: str) -> tuple[str, str, Optional[int]]:
    # Password itself is never stored; the refresh token is persisted so later actions can reuse
    # it via get_valid_access_token instead of asking for the password again.
    base_url = os.environ.get(GRIMMORY_BASE_URL_ENV)
    if not base_url:
        raise GrimmoryLoginError("Grimmory login is not configured")

    url = f"{base_url.rstrip('/')}{LOGIN_PATH}"
    start = time.monotonic()
    try:
        response = httpx.post(
            url, json={"username": username, "password": password}, timeout=10.0
        )
    except httpx.HTTPError as exc:
        logger.warning("Grimmory login request failed for user %r: %s", username, exc)
        raise GrimmoryLoginError("Couldn't reach Grimmory — try again shortly") from exc
    grimmory_http.log_call("POST", url, response, time.monotonic() - start)

    if response.status_code in INVALID_CREDENTIALS_STATUSES:
        raise GrimmoryLoginError("Invalid username or password")
    if response.status_code >= 400:
        raise GrimmoryLoginError("Couldn't reach Grimmory — try again shortly")

    body = response.json()
    return body["accessToken"], body["refreshToken"], body.get("expires")

# Function Name: refresh
# Description: Exchanges a refresh token for a new access/refresh token pair.
# Parameters:
# - base_url (str): Grimmory base URL.
# - refresh_token (str): Current refresh token.
# Returns: Tuple of (access_token, refresh_token, expires_in_seconds) - see login() for
#   expires_in_seconds' meaning.
def refresh(base_url: str, refresh_token: str) -> tuple[str, str, Optional[int]]:
    # Grimmory rotates and revokes the old refresh token on every call - the returned refresh
    # token must replace the stored one immediately (see get_valid_access_token).
    url = f"{base_url.rstrip('/')}{REFRESH_PATH}"
    start = time.monotonic()
    try:
        response = httpx.post(url, json={"refreshToken": refresh_token}, timeout=10.0)
    except httpx.HTTPError as exc:
        logger.warning("Grimmory refresh request failed: %s", exc)
        raise GrimmoryLoginError("Couldn't reach Grimmory — try again shortly") from exc
    grimmory_http.log_call("POST", url, response, time.monotonic() - start)

    if response.status_code >= 400:
        raise GrimmoryLoginError("Grimmory session expired")

    body = response.json()
    return body["accessToken"], body["refreshToken"], body.get("expires")

# Function Name: get_valid_access_token
# Description: Returns a fresh Grimmory access token for a user using their stored refresh token.
# Parameters:
# - db_connection: Database connection.
# - user (User): The user to get an access token for.
# Returns: Access token string, or None if there's no stored session or it's no longer valid.
def get_valid_access_token(db_connection, user: User) -> Optional[str]:
    if not user.grimmory_refresh_token:
        return None
    base_url = os.environ.get(GRIMMORY_BASE_URL_ENV)
    if not base_url:
        return None

    # Cache check outside the lock - the common case (a still-fresh cached token) never needs to
    # contend with other requests/the periodic sync at all. See _access_token_cache above for why
    # this exists: this used to call refresh() unconditionally on every single call.
    cached = _access_token_cache.get(user.id)
    if cached is not None and time.monotonic() < cached[1]:
        return cached[0]

    # Re-reads the token from the DB after acquiring the lock rather than trusting the caller's
    # possibly-stale `user.grimmory_refresh_token` - two concurrent requests for the same user
    # could otherwise race against Grimmory's token rotation and wrongly clear a still-valid
    # session.
    with _refresh_locks[user.id]:
        # Re-check now that we hold the lock - another thread may have already refreshed (or a
        # concurrent login() may have cached a fresh one) while we were waiting for it.
        cached = _access_token_cache.get(user.id)
        if cached is not None and time.monotonic() < cached[1]:
            return cached[0]

        current = get_user(db_connection, user.id)
        if current is None or not current.grimmory_refresh_token:
            return None
        # TEMPORARY diagnostic - see log_token_write above.
        logger.info(
            "Grimmory refresh token READ user_id=%s fingerprint=%s",
            user.id, _token_fingerprint(current.grimmory_refresh_token),
        )
        try:
            access_token, new_refresh_token, expires_in = refresh(
                base_url, current.grimmory_refresh_token
            )
        except GrimmoryLoginError:
            set_grimmory_refresh_token(db_connection, user.id, None)
            log_token_write(user.id, None, "get_valid_access_token(rejected)")
            user.grimmory_refresh_token = None
            _access_token_cache.pop(user.id, None)
            return None

        set_grimmory_refresh_token(db_connection, user.id, new_refresh_token)
        log_token_write(user.id, new_refresh_token, "get_valid_access_token")
        user.grimmory_refresh_token = new_refresh_token
        cache_access_token(user.id, access_token, expires_in)
        return access_token

# Function Name: update_book_finished_date
# Description: Best-effort write-back of a book's finished date to Grimmory.
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): Calling user's own Grimmory access token.
# - grimmory_book_id (int): Grimmory's numeric id for the book.
# - finished_at (date): The finished date to set.
# Returns: None
def update_book_finished_date(
    base_url: str, access_token: str, grimmory_book_id: int, finished_at: date
) -> None:
    # Callers are expected to swallow LibraryCheckUnavailable and keep the local edit regardless -
    # the local save must never depend on this succeeding.
    url = f"{base_url.rstrip('/')}{BOOK_PROGRESS_PATH}"
    start = time.monotonic()
    try:
        response = httpx.post(
            url,
            json={
                "bookId": grimmory_book_id,
                "dateFinished": f"{finished_at.isoformat()}T00:00:00Z",
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        grimmory_http.log_call("POST", url, response, time.monotonic() - start)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory book-progress update failed for book %s: %s", grimmory_book_id, exc)
        raise LibraryCheckUnavailable(f"Grimmory API request failed: {exc}") from exc

# Function Name: get_own_grimmory_user_id
# Description: Returns the calling user's own Grimmory numeric user id.
# Parameters:
# - base_url (str): Grimmory base URL.
# - access_token (str): Calling user's own Grimmory access token.
# Returns: Grimmory user id (int)
def get_own_grimmory_user_id(base_url: str, access_token: str) -> int:
    # Needed because GET /api/v1/shelves returns own + public shelves mixed with no server-side
    # owner filter - filtering a shelf list down to "shelves I own" requires knowing this first
    # (see app/library_check.py:list_own_shelves).
    url = f"{base_url.rstrip('/')}{USERS_ME_PATH}"
    start = time.monotonic()
    try:
        response = httpx.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10.0)
        grimmory_http.log_call("GET", url, response, time.monotonic() - start)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory /users/me request failed: %s", exc)
        raise LibraryCheckUnavailable(f"Grimmory API request failed: {exc}") from exc
    except ValueError as exc:
        # response.json() raises json.JSONDecodeError (a ValueError subclass) on a non-JSON body
        # (e.g. an HTML error page from a proxy in front of Grimmory).
        raise LibraryCheckUnavailable(f"Grimmory API returned an invalid response: {exc}") from exc
    user_id = body.get("id") if isinstance(body, dict) else None
    if user_id is None:
        raise LibraryCheckUnavailable("Grimmory API response for /users/me is missing 'id'")
    return user_id


# --- admin-privileged actions (content restrictions / the spice scale) ---

# Function Name: _auth_header
# Description: Generates the authorization header for Grimmory API requests.
# Parameters:
# - auth_token (str): Admin Account Auth token.
# Returns: Authorization header (dict)
def _auth_header(auth_token: str) -> dict:
    return {"Authorization": f"Bearer {auth_token}"}

# Function Name: is_configured
# Description: Checks if the Grimmory admin settings are configured.
# Parameters:
# - db_connection: Database connection.
# Returns: True if configured, False otherwise.
def is_configured(db_connection) -> bool:
    base_url = os.environ.get(GRIMMORY_BASE_URL_ENV)
    settings = get_grimmory_admin_settings(db_connection)
    return bool(base_url and settings and settings.username and settings.password)

# Function Name: get_admin_session
# Description: Retrieves an admin session for Grimmory.
# Parameters:
# - db_connection: Database connection.
# Returns: Tuple containing the base URL and admin access token, or None if not configured.
def get_admin_session(db_connection) -> Optional[tuple[str, str]]:
    base_url = os.environ.get(GRIMMORY_BASE_URL_ENV)
    settings = get_grimmory_admin_settings(db_connection)
    if not base_url or not settings or not settings.username or not settings.password:
        return None
    access_token = _admin_login(base_url, settings.username, settings.password)
    return base_url, access_token

# Function Name: _admin_login
# Description: Sends the request to Grimmory to log in as an admin.
# Parameters:
# - base_url (str): Grimmory base URL.
# - username (str): Admin username.
# - password (str): Admin password.
# Returns: Admin access token (str)
def _admin_login(base_url: str, username: str, password: str) -> str:
    # Refresh token is deliberately never persisted - this account only ever makes one call.
    url = f"{base_url.rstrip('/')}{LOGIN_PATH}"
    start = time.monotonic()
    try:
        response = httpx.post(
            url, json={"username": username, "password": password}, timeout=10.0
        )
        grimmory_http.log_call("POST", url, response, time.monotonic() - start)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory admin login request failed: %s", exc)
        raise LibraryCheckUnavailable(f"Grimmory admin login failed: {exc}") from exc
    return response.json()["accessToken"]

# Function Name: find_grimmory_user_id
# Description: Sends the request to Grimmory to retrieve a user's ID based on their username.
# Parameters:
# - base_url (str): Grimmory base URL.
# - admin_token (str): Admin Account Auth token.
# - username (str): Username of the user.
# Returns: User ID (int) or None if not found.
def find_grimmory_user_id(base_url: str, admin_token: str, username: str) -> Optional[int]:
    url = f"{base_url.rstrip('/')}{USERS_PATH}"
    start = time.monotonic()
    try:
        response = httpx.get(url, headers=_auth_header(admin_token), timeout=10.0)
        grimmory_http.log_call("GET", url, response, time.monotonic() - start)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory users list request failed: %s", exc)
        raise LibraryCheckUnavailable(f"Grimmory API request failed: {exc}") from exc

    for user in response.json():
        if user.get("username") == username:
            return user.get("id")
    return None

# Function Name: get_content_restrictions
# Description: Sends the request to Grimmory to retrieve a user's content restrictions.
# Parameters:
# - base_url (str): Grimmory base URL.
# - admin_token (str): Admin Account Auth token.
# - user_id (int): Current user's id.
# Returns: List of content restrictions (list[dict])
def get_content_restrictions(base_url: str, admin_token: str, user_id: int) -> list[dict]:
    url = f"{base_url.rstrip('/')}{CONTENT_RESTRICTIONS_PATH.format(user_id=user_id)}"
    start = time.monotonic()
    try:
        response = httpx.get(url, headers=_auth_header(admin_token), timeout=10.0)
        grimmory_http.log_call("GET", url, response, time.monotonic() - start)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory content-restrictions GET failed for user %s: %s", user_id, exc)
        raise LibraryCheckUnavailable(f"Grimmory API request failed: {exc}") from exc
    return response.json()

# Function Name: put_content_restrictions
# Description: Sends the request to Grimmory to update a user's content restrictions.
# Parameters:
# - base_url (str): Grimmory base URL.
# - admin_token (str): Admin Account Auth token.
# - user_id (int): Current user's id.
# - restrictions (list[dict]): List of content restrictions to apply.
# Returns: None
def put_content_restrictions(
    base_url: str, admin_token: str, user_id: int, restrictions: list[dict]
) -> None:
    url = f"{base_url.rstrip('/')}{CONTENT_RESTRICTIONS_PATH.format(user_id=user_id)}"
    start = time.monotonic()
    try:
        response = httpx.put(
            url, json=restrictions, headers=_auth_header(admin_token), timeout=10.0
        )
        grimmory_http.log_call("PUT", url, response, time.monotonic() - start)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        if not grimmory_http.already_logged(exc):
            logger.warning("Grimmory content-restrictions PUT failed for user %s: %s", user_id, exc)
        raise LibraryCheckUnavailable(f"Grimmory API request failed: {exc}") from exc

# Function Name: sync_restriction_level
# Description: Updates a user's content restrictions based on their preferred "spice" level.
# Parameters:
# - base_url (str): Grimmory base URL.
# - admin_token (str): Admin Account Auth token.
# - user_id (int): Current user's id.
# - restriction_level (int): Current Restriction level.
# Returns: None
def sync_restriction_level(
    base_url: str, admin_token: str, user_id: int, restriction_level: int
) -> None:
    # GET-merge-PUT: every other restriction type/mode is left untouched, since Grimmory's PUT
    # replaces a user's entire restriction list wholesale.
    user_restrictions = get_content_restrictions(base_url, admin_token, user_id)
    kept = [
        rec
        for rec in user_restrictions
        if not (rec.get("restrictionType") == "AGE_RATING" and rec.get("mode") == "EXCLUDE")
    ]

    if restriction_level < len(RESTRICTION_TIERS) - 1:
        threshold = RESTRICTION_TIERS[restriction_level + 1]
        kept.append(
            {
                "userId": user_id,
                "restrictionType": "AGE_RATING",
                "mode": "EXCLUDE",
                "value": str(threshold),
            }
        )

    put_content_restrictions(base_url, admin_token, user_id, kept)
