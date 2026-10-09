# Shared httpx instrumentation for every call this app makes to Grimmory's REST API, so a
# connection problem is diagnosable from the log alone instead of surfacing as a generic
# "Couldn't reach Grimmory" error - see app/grimmory_auth.py and app/library_check.py for call sites.

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("app.grimmory_api")

# Stashed on the request so the response hook can compute how long Grimmory took to answer.
_START_TIME_KEY = "booknook_request_start"

# Response bodies are only logged for error responses, sliced off the raw bytes before decoding so
# an unexpectedly large body only ever costs a bounded decode.
_ERROR_BODY_LOG_LIMIT_BYTES = 500


def _truncate_body(content: bytes) -> str:
    truncated = len(content) > _ERROR_BODY_LOG_LIMIT_BYTES
    text = content[:_ERROR_BODY_LOG_LIMIT_BYTES].decode("utf-8", errors="replace").strip()
    return text + "... (truncated)" if truncated else text


def _on_request(request: httpx.Request) -> None:
    request.extensions[_START_TIME_KEY] = time.monotonic()


def _on_response(response: httpx.Response) -> None:
    # Connection-level failures never reach here - callers log those in their own except block.
    start = response.request.extensions.get(_START_TIME_KEY)
    elapsed = f"{(time.monotonic() - start) * 1000:.0f}ms" if start is not None else "?"
    status = response.status_code
    log = logger.error if status >= 500 else logger.warning if status >= 400 else logger.info
    if status >= 400:
        try:
            response.read()
            body = _truncate_body(response.content)
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
# Description: Same request/response logging as client() above, for call sites using the bare
#   httpx.get/post/put functions instead of a Client, which aren't compatible with event_hooks.
# Parameters:
# - method (str): HTTP method, e.g. "POST".
# - url (str): Full request URL.
# - response (httpx.Response): The response received.
# - elapsed_seconds (float): Wall-clock time the request took.
# Returns: None
def log_call(method: str, url: str, response: httpx.Response, elapsed_seconds: float) -> None:
    status_code = response.status_code
    elapsed = f"{elapsed_seconds * 1000:.0f}ms"
    log = logger.error if status_code >= 500 else logger.warning if status_code >= 400 else logger.info
    if status_code >= 400:
        log(
            "Grimmory %s %s -> %s (%s): %s",
            method, url, status_code, elapsed, _truncate_body(response.content),
        )
    else:
        log("Grimmory %s %s -> %s (%s)", method, url, status_code, elapsed)

# Function Name: already_logged
# Description: True if exc is an httpx.HTTPStatusError, meaning it already went through client()'s
#   event hooks or log_call() - callers should skip logging it again.
# Parameters:
# - exc (Exception): The caught httpx.HTTPError.
# Returns: bool
def already_logged(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError)
