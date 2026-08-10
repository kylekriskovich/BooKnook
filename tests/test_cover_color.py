import io

import httpx
import pytest
from PIL import Image

from app import cover_color, models


def _solid_png_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), rgb).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def conn():
    connection = models.get_connection(":memory:")
    models.init_db(connection)
    yield connection
    connection.close()


def _book(conn, cover_url=None):
    book = models.create_book(conn, "Dune", isbn="9780441172719")
    if cover_url:
        models.set_book_cover_url(conn, book.id, cover_url)
        book = models.get_book(conn, book.id)
    return book


def test_extract_color_returns_hex_for_solid_image():
    assert cover_color.extract_color(_solid_png_bytes((255, 0, 0))) == "#ff0000"


def test_extract_color_none_for_garbage_bytes():
    assert cover_color.extract_color(b"not an image") is None


def test_ensure_cover_color_none_when_no_cover_url(conn):
    book = _book(conn, cover_url=None)
    assert cover_color.ensure_cover_color(conn, book) is None


def test_ensure_cover_color_returns_cached_value_without_recomputing(conn, monkeypatch):
    book = _book(conn, cover_url="https://example.com/cover.jpg")
    book.cover_color = "#123456"

    monkeypatch.setattr(
        cover_color, "_fetch_cover_bytes", lambda url: (_ for _ in ()).throw(AssertionError())
    )

    assert cover_color.ensure_cover_color(conn, book) == "#123456"


def test_ensure_cover_color_reads_self_hosted_cover_from_disk(conn, monkeypatch, tmp_path):
    (tmp_path / "5.jpg").write_bytes(_solid_png_bytes((0, 255, 0)))
    monkeypatch.setattr(cover_color, "covers_dir", lambda: str(tmp_path))
    book = _book(conn, cover_url="/covers/5.jpg")

    color = cover_color.ensure_cover_color(conn, book)

    assert color == "#00ff00"
    assert book.cover_color == "#00ff00"
    assert models.get_book(conn, book.id).cover_color == "#00ff00"  # persisted


def test_ensure_cover_color_fetches_external_cover_over_http(conn, monkeypatch):
    image_bytes = _solid_png_bytes((0, 0, 255))

    def fake_get(url, timeout=None, follow_redirects=None):
        assert url == "https://example.com/cover.jpg"
        return httpx.Response(200, content=image_bytes, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    book = _book(conn, cover_url="https://example.com/cover.jpg")

    assert cover_color.ensure_cover_color(conn, book) == "#0000ff"


def test_ensure_cover_color_none_when_fetch_fails(conn, monkeypatch):
    def fake_get(url, timeout=None, follow_redirects=None):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    book = _book(conn, cover_url="https://example.com/cover.jpg")

    assert cover_color.ensure_cover_color(conn, book) is None
    assert models.get_book(conn, book.id).cover_color is None
