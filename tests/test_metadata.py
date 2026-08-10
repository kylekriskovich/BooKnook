import httpx
import pytest

from app import metadata


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload


def test_search_books_empty_query_skips_request(monkeypatch):
    def fail_get(*args, **kwargs):
        raise AssertionError("should not call httpx.get for an empty query")

    monkeypatch.setattr(metadata.httpx, "get", fail_get)
    assert metadata.search_books("   ") == []


def test_search_books_parses_docs(monkeypatch):
    payload = {
        "docs": [
            {
                "title": "Dune",
                "author_name": ["Frank Herbert"],
                "isbn": ["9780441172719", "0441172717"],
                "cover_i": 12345,
            },
            {
                "title": "No metadata book",
            },
            {
                # missing title should be skipped
                "author_name": ["Someone"],
            },
        ]
    }
    monkeypatch.setattr(metadata.httpx, "get", lambda *a, **k: FakeResponse(payload))

    results = metadata.search_books("dune")

    assert len(results) == 2
    assert results[0].title == "Dune"
    assert results[0].author == "Frank Herbert"
    assert results[0].isbn == "9780441172719"
    assert results[0].cover_url == "https://covers.openlibrary.org/b/id/12345-M.jpg"

    assert results[1].title == "No metadata book"
    assert results[1].author is None
    assert results[1].isbn is None
    assert results[1].cover_url is None


def test_search_books_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(metadata.httpx, "get", lambda *a, **k: FakeResponse({}, status_code=500))
    with pytest.raises(httpx.HTTPStatusError):
        metadata.search_books("dune")
