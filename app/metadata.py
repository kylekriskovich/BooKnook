# Open Library search client - title/author query to results with cover/ISBN.

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL_TEMPLATE = "https://covers.openlibrary.org/b/id/{cover_i}-M.jpg"


@dataclass
class SearchResult:
    title: str
    author: Optional[str]
    isbn: Optional[str]
    cover_url: Optional[str]
    published_date: Optional[str] = None
    grimmory_id: Optional[int] = None

# Function Name: search_books
# Description: Searches Open Library for books matching a query.
# Parameters:
# - query (str): Title/author search text.
# - limit (int): Max number of results to return.
# Returns: List of SearchResult.
def search_books(query: str, limit: int = 20) -> list[SearchResult]:
    query = query.strip()
    if not query:
        return []

    response = httpx.get(
        SEARCH_URL,
        params={
            "q": query,
            "limit": limit,
            "fields": "title,author_name,isbn,cover_i,first_publish_year",
        },
        timeout=5.0,
    )
    response.raise_for_status()
    docs = response.json().get("docs", [])

    results = []
    for doc in docs:
        title = doc.get("title")
        if not title:
            continue
        author_names = doc.get("author_name") or []
        isbns = doc.get("isbn") or []
        cover_i = doc.get("cover_i")
        first_publish_year = doc.get("first_publish_year")
        results.append(
            SearchResult(
                title=title,
                author=", ".join(author_names) or None,
                isbn=isbns[0] if isbns else None,
                cover_url=COVER_URL_TEMPLATE.format(cover_i=cover_i) if cover_i else None,
                published_date=str(first_publish_year) if first_publish_year else None,
            )
        )
    return results
