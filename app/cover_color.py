from __future__ import annotations

import io
import os
from typing import Optional

import httpx
from PIL import Image

from app.models import Book, covers_dir, set_book_cover_color

# Function Name: extract_color
# Description: Extracts the dominant color from an image.
# Parameters:
# - image_bytes: Image byte content.
# Returns: String representing the hexadecimal color or None.
def extract_color(image_bytes: bytes) -> Optional[str]:
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Downsample to a single pixel - cheap stand-in for a true dominant-color algorithm.
            r, g, b = img.convert("RGB").resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    except Exception:
        return None
    return f"#{r:02x}{g:02x}{b:02x}"

# Function Name: _fetch_cover_bytes
# Description: Fetches the cover image bytes.
# Parameters:
# - cover_url: URL or local path of the cover image.
# Returns: Image byte content or None.
def _fetch_cover_bytes(cover_url: str) -> Optional[bytes]:
    if cover_url.startswith("/covers/"):
        # Self-hosted Grimmory covers are read straight off disk; everything else goes over HTTP.
        cover_path = os.path.join(covers_dir(), os.path.basename(cover_url))
        try:
            with open(cover_path, "rb") as f:
                return f.read()
        except OSError:
            return None
    try:
        response = httpx.get(cover_url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.content

# Function Name: ensure_cover_color
# Description: Ensures the cover color is set for a book.
# Parameters:
# - db_connection: Database connection.
# - book: Book object.
# Returns: Hexadecimal color string or None.
def ensure_cover_color(db_connection, book: Book) -> Optional[str]:
    if book.cover_color:
        return book.cover_color
    if not book.cover_url:
        return None
    image_bytes = _fetch_cover_bytes(book.cover_url)
    if image_bytes is None:
        return None
    color = extract_color(image_bytes)
    if color is None:
        # Best-effort only - callers fall back to the id-cycled palette on any failure.
        return None
    set_book_cover_color(db_connection, book.id, color)
    book.cover_color = color
    return color
