# Hardcover.app GraphQL search client - a richer alternative to Open Library for adding books.
# Requires a personal Hardcover API token (Bearer auth, no OAuth) - see /admin/settings.

from __future__ import annotations

import json
import threading
import time

import httpx

from app.metadata import SearchResult

GRAPHQL_URL = "https://api.hardcover.app/v1/graphql"

SEARCH_QUERY = """
query BookSearch($q: String!, $limit: Int!) {
  search(query: $q, query_type: "Book", per_page: $limit, page: 1) {
    results
  }
}
"""

# Matches Grimmory's own client-side throttle for this API (see its HardcoverBookSearchService).
MIN_REQUEST_INTERVAL_SECONDS = 1.2

# Hardcover's catalog is community-editable - occasionally a document has garbage stuffed into
# its title (e.g. an entire CSV export row pasted in by mistake). No real book title is anywhere
# near this long, so treat it as a sign the entry is malformed and skip it.
MAX_TITLE_LENGTH = 200

_rate_limit_lock = threading.Lock()
_last_request_time = 0.0


class HardcoverSearchError(Exception):
    """Raised when a Hardcover search request fails. Message is safe to show to the user."""

# Function Name: _wait_for_rate_limit
# Description: Blocks the calling thread until the minimum request interval has elapsed.
# Parameters: None
# Returns: None
def _wait_for_rate_limit() -> None:
    # FastAPI runs sync routes in a threadpool, so blocking here doesn't stall the event loop.
    global _last_request_time
    with _rate_limit_lock:
        wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.monotonic()

# Function Name: search_hardcover
# Description: Searches Hardcover for books matching a query.
# Parameters:
# - query (str): Title/author search text.
# - api_key (str): Hardcover personal API token.
# - limit (int): Max number of results to return.
# Returns: List of SearchResult.
def search_hardcover(query: str, api_key: str, limit: int = 10) -> list[SearchResult]:
    _wait_for_rate_limit()
    try:
        response = httpx.post(
            GRAPHQL_URL,
            json={"query": SEARCH_QUERY, "variables": {"q": query, "limit": limit}},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise HardcoverSearchError(f"Hardcover request failed: {exc}") from exc

    if payload.get("errors"):
        raise HardcoverSearchError(str(payload["errors"]))

    results_field = payload.get("data", {}).get("search", {}).get("results")
    if isinstance(results_field, str):
        # The GraphQL field is typed as a JSON scalar - some Typesense-backed setups return it
        # already-parsed, others as a JSON-encoded string. Handle both.
        try:
            results_field = json.loads(results_field)
        except (TypeError, ValueError) as exc:
            raise HardcoverSearchError(f"Unexpected Hardcover response shape: {exc}") from exc

    hits = (results_field or {}).get("hits", [])
    results = []
    for hit in hits:
        doc = hit.get("document") or {}
        title = doc.get("title")
        if not title or len(title) > MAX_TITLE_LENGTH:
            continue
        author = ", ".join(doc.get("author_names") or []) or None
        isbn = _pick_isbn(doc.get("isbns") or [])
        image = doc.get("image") or {}
        results.append(SearchResult(title=title, author=author, isbn=isbn, cover_url=image.get("url")))
    return results

# Function Name: _pick_isbn
# Description: Picks the preferred ISBN from a list of candidates.
# Parameters:
# - isbns (list[str]): Candidate ISBNs.
# Returns: Preferred ISBN string, or None if the list is empty.
def _pick_isbn(isbns: list[str]) -> "str | None":
    # Prefers a 13-digit ISBN - app/library_check.py's ownership check tries isbn13 first.
    for isbn in isbns:
        if len(isbn.replace("-", "")) == 13:
            return isbn
    return isbns[0] if isbns else None
