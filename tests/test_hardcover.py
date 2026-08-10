import json

import httpx
import pytest

from app import hardcover


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload


def _hits_payload(hits):
    return {"data": {"search": {"results": {"hits": hits}}}}


@pytest.fixture(autouse=True)
def _reset_rate_limit(monkeypatch):
    # Otherwise the throttle added for real Hardcover rate limits would make tests sleep.
    monkeypatch.setattr(hardcover, "_last_request_time", 0.0)


def test_search_hardcover_parses_hits(monkeypatch):
    hits = [
        {
            "document": {
                "title": "Dune",
                "author_names": ["Frank Herbert"],
                "isbns": ["0441172717", "9780441172719"],
                "image": {"url": "https://example.com/dune.jpg"},
            }
        }
    ]
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse(_hits_payload(hits))

    monkeypatch.setattr(hardcover.httpx, "post", fake_post)

    results = hardcover.search_hardcover("dune", "test-api-key")

    assert len(results) == 1
    assert results[0].title == "Dune"
    assert results[0].author == "Frank Herbert"
    assert results[0].isbn == "9780441172719"  # prefers the 13-digit ISBN
    assert results[0].cover_url == "https://example.com/dune.jpg"
    assert calls[0]["url"] == hardcover.GRAPHQL_URL
    assert calls[0]["headers"]["Authorization"] == "Bearer test-api-key"
    assert calls[0]["json"]["variables"] == {"q": "dune", "limit": 10}


def test_search_hardcover_skips_hits_without_title(monkeypatch):
    hits = [{"document": {"author_names": ["No Title Here"]}}]
    monkeypatch.setattr(hardcover.httpx, "post", lambda *a, **k: FakeResponse(_hits_payload(hits)))

    assert hardcover.search_hardcover("query", "key") == []


def test_search_hardcover_handles_no_hits(monkeypatch):
    monkeypatch.setattr(hardcover.httpx, "post", lambda *a, **k: FakeResponse(_hits_payload([])))

    assert hardcover.search_hardcover("query", "key") == []


def test_search_hardcover_parses_stringified_results_field(monkeypatch):
    hits = [{"document": {"title": "Dune", "author_names": [], "isbns": []}}]
    payload = {"data": {"search": {"results": json.dumps({"hits": hits})}}}
    monkeypatch.setattr(hardcover.httpx, "post", lambda *a, **k: FakeResponse(payload))

    results = hardcover.search_hardcover("dune", "key")

    assert results[0].title == "Dune"
    assert results[0].isbn is None


def test_search_hardcover_raises_on_graphql_errors(monkeypatch):
    payload = {"errors": [{"message": "invalid token"}]}
    monkeypatch.setattr(hardcover.httpx, "post", lambda *a, **k: FakeResponse(payload))

    with pytest.raises(hardcover.HardcoverSearchError):
        hardcover.search_hardcover("dune", "bad-key")


def test_search_hardcover_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(hardcover.httpx, "post", lambda *a, **k: FakeResponse({}, status_code=500))

    with pytest.raises(hardcover.HardcoverSearchError):
        hardcover.search_hardcover("dune", "key")


def test_search_hardcover_raises_when_unreachable(monkeypatch):
    def raise_error(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(hardcover.httpx, "post", raise_error)

    with pytest.raises(hardcover.HardcoverSearchError):
        hardcover.search_hardcover("dune", "key")


def test_search_hardcover_throttles_back_to_back_calls(monkeypatch):
    monkeypatch.setattr(hardcover.httpx, "post", lambda *a, **k: FakeResponse(_hits_payload([])))
    sleeps = []
    monkeypatch.setattr(hardcover.time, "sleep", lambda seconds: sleeps.append(seconds))

    hardcover.search_hardcover("dune", "key")  # first call: nothing to wait for
    hardcover.search_hardcover("dune", "key")  # second call: immediately after the first

    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= hardcover.MIN_REQUEST_INTERVAL_SECONDS


def test_search_hardcover_does_not_throttle_when_interval_already_elapsed(monkeypatch):
    monkeypatch.setattr(hardcover.httpx, "post", lambda *a, **k: FakeResponse(_hits_payload([])))
    monkeypatch.setattr(hardcover, "_last_request_time", 0.0)
    sleeps = []
    monkeypatch.setattr(hardcover.time, "sleep", lambda seconds: sleeps.append(seconds))

    hardcover.search_hardcover("dune", "key")

    assert sleeps == []


def test_pick_isbn_prefers_13_digit():
    assert hardcover._pick_isbn(["0441172717", "9780441172719"]) == "9780441172719"


def test_pick_isbn_falls_back_to_first_when_no_13_digit():
    assert hardcover._pick_isbn(["0441172717"]) == "0441172717"


def test_pick_isbn_empty_list():
    assert hardcover._pick_isbn([]) is None
