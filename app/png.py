"""
png.py -- a tiny, dependency-free PNG writer/encoder (standard library only).

We need this twice:
  1. Full-screen test patterns for the display check (gradient, checkerboard...).
  2. Saving the camera frames captured during the guided camera test.

tk.PhotoImage can load a PNG straight from base64, and a browser can embed the
same bytes in the HTML report, so one encoder serves the GUI *and* the report.
"""

from __future__ import annotations

import base64
import struct
import zlib
from typing import List, Optional, Tuple


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png(width: int, height: int, rgb: bytes, level: int = 6) -> bytes:
    """rgb = width*height*3 bytes, top-down, R,G,B per pixel."""
    if len(rgb) != width * height * 3:
        raise ValueError("buffer size %d != %d" % (len(rgb), width * height * 3))
    rowlen = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)                                   # filter type 0 (None)
        raw += rgb[y * rowlen : (y + 1) * rowlen]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
            + _chunk(b"IEND", b""))


def encode_png_gray(width: int, height: int, gray: bytes, level: int = 6) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += gray[y * width : (y + 1) * width]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
            + _chunk(b"IEND", b""))


def png_b64(width: int, height: int, rgb: bytes) -> str:
    return base64.b64encode(encode_png(width, height, rgb)).decode("ascii")


def save_png(path: str, width: int, height: int, rgb: bytes) -> bool:
    try:
        with open(path, "wb") as fh:
            fh.write(encode_png(width, height, rgb))
        return True
    except Exception:
        return False


def file_to_data_uri(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")
    except Exception:
        return ""


# --------------------------------------------------------------------------
# pattern generators -- all build row templates and repeat them (fast)
# --------------------------------------------------------------------------

def solid(width: int, height: int, rgb: Tuple[int, int, int]) -> bytes:
    row = bytes(rgb) * width
    return row * height


def h_gradient(width: int, height: int,
               a: Tuple[int, int, int] = (0, 0, 0),
               b: Tuple[int, int, int] = (255, 255, 255)) -> bytes:
    steps = max(1, width - 1)
    row = bytearray()
    for x in range(width):
        t = x / steps
        row += bytes((
            int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t),
        ))
    return bytes(row) * height


def v_gradient(width: int, height: int,
               a: Tuple[int, int, int] = (0, 0, 0),
               b: Tuple[int, int, int] = (255, 255, 255)) -> bytes:
    steps = max(1, height - 1)
    out = bytearray()
    row_cache = {}
    for y in range(height):
        t = y / steps
        px = bytes((
            int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t),
        ))
        if px not in row_cache:
            row_cache[px] = px * width
        out += row_cache[px]
    return bytes(out)


def checkerboard(width: int, height: int, cell: int = 24,
                 a: Tuple[int, int, int] = (255, 255, 255),
                 b: Tuple[int, int, int] = (0, 0, 0)) -> bytes:
    cell = max(1, cell)
    row_a = bytearray()
    row_b = bytearray()
    for x in range(width):
        row_a += bytes(a if (x // cell) % 2 == 0 else b)
        row_b += bytes(b if (x // cell) % 2 == 0 else a)
    row_a, row_b = bytes(row_a), bytes(row_b)
    out = bytearray()
    for y in range(height):
        out += row_a if (y // cell) % 2 == 0 else row_b
    return bytes(out)


def stripes(width: int, height: int, px: int = 1,
            a: Tuple[int, int, int] = (255, 255, 255),
            b: Tuple[int, int, int] = (0, 0, 0), vertical: bool = True) -> bytes:
    """1-pixel alternating lines: reveals scaling blur and panel sharpness."""
    px = max(1, px)
    if vertical:
        row = bytearray()
        for x in range(width):
            row += bytes(a if (x // px) % 2 == 0 else b)
        return bytes(row) * height
    out = bytearray()
    ra, rb = bytes(a) * width, bytes(b) * width
    for y in range(height):
        out += ra if (y // px) % 2 == 0 else rb
    return bytes(out)


def grid(width: int, height: int, step: int = 80,
         bg: Tuple[int, int, int] = (255, 255, 255),
         line: Tuple[int, int, int] = (0, 0, 0), thick: int = 2) -> bytes:
    """Geometry / convergence grid with a centre crosshair (row-template based)."""
    step = max(2, step)
    thick = max(1, thick)
    bgrow = bytes(bg) * width
    linerow = bytes(line) * width

    def vline_row(extra: Optional[int] = None) -> bytes:
        row = bytearray(bgrow)
        marks = list(range(0, width, step))
        if extra is not None:
            marks.append(extra)
        for x in marks:
            for t in range(thick):
                i = (x + t) * 3
                if i + 2 < len(row):
                    row[i], row[i + 1], row[i + 2] = line
        return bytes(row)

    normal = vline_row()
    centre = vline_row(width // 2)
    out = []
    cy = height // 2
    for y in range(height):
        if y % step < thick or abs(y - cy) < thick:
            out.append(linerow)
        elif any(abs((x + t) - cy) < 1 for x in (0,) for t in (0,)):
            out.append(normal)
        else:
            out.append(centre if abs(y - cy) < step else normal)
    return b"".join(out)


def gray_steps(width: int, height: int, steps: int = 16) -> bytes:
    """Banding test: smooth steps from black to white."""
    band = max(1, width // steps)
    row = bytearray()
    for x in range(width):
        idx = min(steps - 1, x // band)
        v = int(255 * idx / max(1, steps - 1))
        row += bytes((v, v, v))
    return bytes(row) * height


def corner_boxes(width: int, height: int,
                 bg: Tuple[int, int, int] = (0, 0, 0),
                 box: Tuple[int, int, int] = (38, 38, 38), size: int = 150) -> bytes:
    """Backlight-bleed helper: near-black screen with slightly lighter corners."""
    size = max(8, min(size, width // 3, height // 3))
    bgrow = bytes(bg) * width
    corner = bytes(box) * size + bytes(bg) * (width - 2 * size) + bytes(box) * size
    left = bytes(box) * size + bytes(bg) * (width - size)
    right = bytes(bg) * (width - size) + bytes(box) * size
    out = []
    for y in range(height):
        if y < size:
            out.append(corner)
        elif y >= height - size:
            out.append(corner)
        else:
            out.append(bgrow)
    _ = (left, right)
    return b"".join(out)


def bgr_bottom_up_to_rgb(buf: bytes, width: int, height: int) -> bytes:
    """Convert a 24-bit bottom-up DIB (what DirectShow hands us) to top-down RGB."""
    row = width * 3
    out = bytearray(len(buf))
    for y in range(height):
        src = (height - 1 - y) * row
        dst = y * row
        chunk = bytearray(buf[src : src + row])
        chunk[0::3], chunk[2::3] = chunk[2::3], chunk[0::3]   # BGR -> RGB
        out[dst : dst + row] = chunk
    return bytes(out)
