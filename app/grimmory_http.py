# Shared httpx instrumentation for every call this app makes to Grimmory's REST API. Before this
# existed, a Grimmory connection problem (wrong base URL, expired service-account credentials,
# Grimmory itself being down) surfaced only as a generic "Couldn't reach Grimmory" error to the
# user, with nothing in the app log saying which endpoint or status code was actually responsible.
# Every httpx call to Grimmory should go through client() below so failures are diagnosable from
# the log alone - see app/grimmory_auth.py and app/library_check.py for the call sites.

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("app.grimmory_api")

# Stashed on the request so the response hook (fired on the same request/response pair) can
# compute how long Grimmory took to answer.
_START_TIME_KEY = "booknook_request_start"

# Response bodies are only ever logged for error responses (see _format_status below) - a status
# code alone doesn't say *why* Grimmory rejected a request (invalid credentials? rate limited?
# something else entirely?), and Grimmory's own error responses are a small JSON message, never a
# secret. Truncated defensively in case something unexpected (an HTML error page from a proxy
# in front of Grimmory, say) returns something much larger.
_ERROR_BODY_LOG_LIMIT = 500


def _truncate_body(text: str) -> str:
    text = text.strip()
    if len(text) > _ERROR_BODY_LOG_LIMIT:
        return text[:_ERROR_BODY_LOG_LIMIT] + "... (truncated)"
    return text


def _on_request(request: httpx.Request) -> None:
    request.extensions[_START_TIME_KEY] = time.monotonic()


def _on_response(response: httpx.Response) -> None:
    # Connection-level failures (timeout, DNS, refused) raise before a response ever exists, so
    # this hook never fires for those - callers log those themselves in their
    # `except httpx.HTTPError` block instead.
    start = response.request.extensions.get(_START_TIME_KEY)
    elapsed = f"{(time.monotonic() - start) * 1000:.0f}ms" if start is not None else "?"
    status = response.status_code
    log = logger.error if status >= 500 else logger.warning if status >= 400 else logger.info
    if status >= 400:
        try:
            response.read()
            body = _truncate_body(response.text)
        except httpx.HTTPError:
            body = "<unreadable>"
        log(
            "Grimmory %s %s -> %s (%s): %s",
            response.request.method, response.request.url, status, elapsed, body,
        )
    else:
        log("Grimmory %s %s -> %s (%s)", response.request.method, response.request.url, status, elapsed)


_EVENT_HOOKS = {"request": [_on_request], "response": [_on_response]}

# Function Name: client
# Description: httpx.Client pre-wired to log every Grimmory request/response (status + timing).
# Parameters:
# - **kwargs: Forwarded to httpx.Client as-is (base_url, timeout, etc).
# Returns: httpx.Client
def client(**kwargs) -> httpx.Client:
    kwargs.setdefault("event_hooks", _EVENT_HOOKS)
    return httpx.Client(**kwargs)

# Function Name: log_call
# Description: Same request/response logging as client() above, for the handful of call sites
#   (app/grimmory_auth.py) that use the bare httpx.get/post/put functions instead of a Client -
#   those aren't compatible with event_hooks, so callers log explicitly with the values they
#   already have in scope rather than pulling them back off the response.
# Parameters:
# - method (str): HTTP method, e.g. "POST".
# - url (str): Full request URL.
# - status_code (int): Response status code.
# - elapsed_seconds (float): Wall-clock time the request took.
# - body (Optional[str]): Response body text, logged only when status_code is an error - pass
#   `response.text` unconditionally, this decides whether it's worth including.
# Returns: None
def log_call(
    method: str, url: str, status_code: int, elapsed_seconds: float, body: Optional[str] = None
) -> None:
    elapsed = f"{elapsed_seconds * 1000:.0f}ms"
    log = logger.error if status_code >= 500 else logger.warning if status_code >= 400 else logger.info
    if status_code >= 400 and body:
        log(
            "Grimmory %s %s -> %s (%s): %s",
            method, url, status_code, elapsed, _truncate_body(body),
        )
    else:
        log("Grimmory %s %s -> %s (%s)", method, url, status_code, elapsed)

# Function Name: already_logged
# Description: True if exc is an httpx.HTTPStatusError - a response that already reached client()'s
#   event hooks or a bare log_call() above, so callers should skip logging it a second time in
#   their own except block. Only a connection-level failure (timeout, DNS, refused - no response
#   ever received) still needs its own log line there.
# Parameters:
# - exc (Exception): The caught httpx.HTTPError.
# Returns: bool
def already_logged(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError)
