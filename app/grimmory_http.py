# Shared httpx instrumentation for every call this app makes to Grimmory's REST API. Before this
# existed, a Grimmory connection problem (wrong base URL, expired service-account credentials,
# Grimmory itself being down) surfaced only as a generic "Couldn't reach Grimmory" error to the
# user, with nothing in the app log saying which endpoint or status code was actually responsible.
# Every httpx call to Grimmory should go through client() below so failures are diagnosable from
# the log alone - see app/grimmory_auth.py and app/library_check.py for the call sites.

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("app.grimmory_api")

# Stashed on the request so the response hook (fired on the same request/response pair) can
# compute how long Grimmory took to answer.
_START_TIME_KEY = "booknook_request_start"

# Response bodies are only ever logged for error responses (see _format_status below) - a status
# code alone doesn't say *why* Grimmory rejected a request (invalid credentials? rate limited?
# something else entirely?), and Grimmory's own error responses are a small JSON message, never a
# secret. Sliced off the raw bytes *before* decoding (not off the fully-decoded text) so a
# surprisingly large body (an HTML error page from a proxy in front of Grimmory, say) only ever
# costs a bounded decode, not a decode of however much the client already buffered in memory.
_ERROR_BODY_LOG_LIMIT_BYTES = 500


def _truncate_body(content: bytes) -> str:
    truncated = len(content) > _ERROR_BODY_LOG_LIMIT_BYTES
    text = content[:_ERROR_BODY_LOG_LIMIT_BYTES].decode("utf-8", errors="replace").strip()
    return text + "... (truncated)" if truncated else text


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
        # response.read() is a no-op here in the common case - these calls are all non-streaming
        # (plain client.get/post/put), which httpx already buffers in full before this hook ever
        # fires. Kept as a defensive no-op for the (currently theoretical) case of a streamed call
        # being routed through client() in the future.
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
# Description: Same request/response logging as client() above, for the handful of call sites
#   (app/grimmory_auth.py) that use the bare httpx.get/post/put functions instead of a Client -
#   those aren't compatible with event_hooks, so callers log explicitly with the values they
#   already have in scope rather than pulling them back off the response. Takes the response
#   object itself (not a pre-extracted status/body) so a successful call - the overwhelming
#   majority - never pays for decoding a body it's not going to log; only an error response gets
#   its (bounded) body decoded at all.
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
# Description: True if exc is an httpx.HTTPStatusError - a response that already reached client()'s
#   event hooks or a bare log_call() above, so callers should skip logging it a second time in
#   their own except block. Only a connection-level failure (timeout, DNS, refused - no response
#   ever received) still needs its own log line there.
# Parameters:
# - exc (Exception): The caught httpx.HTTPError.
# Returns: bool
def already_logged(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError)
