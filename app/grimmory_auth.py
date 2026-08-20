# Everything that authenticates against Grimmory on behalf of a *person* rather than the
# dedicated read-only sync account: TBR's user-facing sign-in, the shared per-user session helper
# that keeps a user's Grimmory refresh token usable without re-prompting for their password on
# every action (see get_valid_access_token), and the separate *admin*-privileged actions (content
# restrictions for the spice scale) that need Grimmory admin rights on a user's behalf.
# Separate from app/library_check.py, which authenticates as its own dedicated read-only user for
# the admin catalog sync - that's a third, distinct account type from the two here.

from __future__ import annotations

import os
import threading
from collections import defaultdict
from datetime import date
from typing import Optional

import httpx

from app.library_check import LOGIN_PATH, LibraryCheckUnavailable
from app.models import User, get_grimmory_admin_settings, get_user, set_grimmory_refresh_token

GRIMMORY_BASE_URL_ENV = "GRIMMORY_BASE_URL"
REFRESH_PATH = "/api/v1/auth/refresh"
BOOK_PROGRESS_PATH = "/api/v1/books/progress"
USERS_PATH = "/api/v1/users"
USERS_ME_PATH = "/api/v1/users/me"
CONTENT_RESTRICTIONS_PATH = "/api/v1/users/{user_id}/content-restrictions"

# Grimmory returns 400,401 (ApiError) for a bad username/password.
INVALID_CREDENTIALS_STATUSES = {400, 401}

# Index = chili count (0-5); RESTRICTION_TIERS[level + 1] is the ageRating threshold to exclude
# for levels 0-4. Level 5 removes the restriction entirely rather than using a threshold.
RESTRICTION_TIERS = [6, 10, 13, 16, 18, 21]

# One lock per user_id, guarding get_valid_access_token below - Grimmory's refresh tokens rotate
# and are revoked on use, so two concurrent refresh attempts for the same user would otherwise
# race. Plain threading.Lock (not asyncio) since every route here runs in FastAPI's threadpool.
_refresh_locks: "defaultdict[int, threading.Lock]" = defaultdict(threading.Lock)


class GrimmoryLoginError(Exception):
    """Raised on any login/refresh failure. Message is safe to show directly to the user."""


# --- regular-user login/session ---

# Function Name: login
# Description: Validates a username/password against Grimmory.
# Parameters:
# - username (str): Grimmory username.
# - password (str): Grimmory password.
# Returns: Tuple of (access_token, refresh_token) on success.
def login(username: str, password: str) -> tuple[str, str]:
    # Password itself is never stored; the refresh token is persisted so later actions can reuse
    # it via get_valid_access_token instead of asking for the password again.
    base_url = os.environ.get(GRIMMORY_BASE_URL_ENV)
    if not base_url:
        raise GrimmoryLoginError("Grimmory login is not configured")

    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}{LOGIN_PATH}",
            json={"username": username, "password": password},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise GrimmoryLoginError("Couldn't reach Grimmory — try again shortly") from exc

    if response.status_code in INVALID_CREDENTIALS_STATUSES:
        raise GrimmoryLoginError("Invalid username or password")
    if response.status_code >= 400:
        raise GrimmoryLoginError("Couldn't reach Grimmory — try again shortly")

    body = response.json()
    return body["accessToken"], body["refreshToken"]

# Function Name: refresh
# Description: Exchanges a refresh token for a new access/refresh token pair.
# Parameters:
# - base_url (str): Grimmory base URL.
# - refresh_token (str): Current refresh token.
# Returns: Tuple of (access_token, refresh_token).
def refresh(base_url: str, refresh_token: str) -> tuple[str, str]:
    # Grimmory rotates and revokes the old refresh token on every call - the returned refresh
    # token must replace the stored one immediately (see get_valid_access_token).
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}{REFRESH_PATH}",
            json={"refreshToken": refresh_token},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise GrimmoryLoginError("Couldn't reach Grimmory — try again shortly") from exc

    if response.status_code >= 400:
        raise GrimmoryLoginError("Grimmory session expired")

    body = response.json()
    return body["accessToken"], body["refreshToken"]

# Function Name: get_valid_access_token
# Description: Returns a fresh Grimmory access token for a user using their stored refresh token.
# Parameters:
# - db_connection: Database connection.
# - user (User): The user to get an access token for.
# Returns: Access token string, or None if there's no stored session or it's no longer valid.
def get_valid_access_token(db_connection, user: User) -> Optional[str]:
    # Re-reads the token from the DB after acquiring the lock rather than trusting the caller's
    # possibly-stale `user.grimmory_refresh_token` - two concurrent requests for the same user
    # could otherwise race against Grimmory's token rotation and wrongly clear a still-valid
    # session.
    if not user.grimmory_refresh_token:
        return None
    base_url = os.environ.get(GRIMMORY_BASE_URL_ENV)
    if not base_url:
        return None

    with _refresh_locks[user.id]:
        current = get_user(db_connection, user.id)
        if current is None or not current.grimmory_refresh_token:
            return None
        try:
            access_token, new_refresh_token = refresh(base_url, current.grimmory_refresh_token)
        except GrimmoryLoginError:
            set_grimmory_refresh_token(db_connection, user.id, None)
            user.grimmory_refresh_token = None
            return None

        set_grimmory_refresh_token(db_connection, user.id, new_refresh_token)
        user.grimmory_refresh_token = new_refresh_token
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
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}{BOOK_PROGRESS_PATH}",
            json={
                "bookId": grimmory_book_id,
                "dateFinished": f"{finished_at.isoformat()}T00:00:00Z",
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
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
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}{USERS_ME_PATH}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
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
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}{LOGIN_PATH}",
            json={"username": username, "password": password},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
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
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}{USERS_PATH}", headers=_auth_header(admin_token), timeout=10.0
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
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
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}{CONTENT_RESTRICTIONS_PATH.format(user_id=user_id)}",
            headers=_auth_header(admin_token),
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
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
    try:
        response = httpx.put(
            f"{base_url.rstrip('/')}{CONTENT_RESTRICTIONS_PATH.format(user_id=user_id)}",
            json=restrictions,
            headers=_auth_header(admin_token),
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
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
