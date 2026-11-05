#!/usr/bin/env python3
"""
make_icon.py -- regenerates assets/icon.png and assets/icon.ico.

No Pillow, no ImageMagick: it draws the logo pixel-by-pixel with the project's
own PNG encoder and packs the PNGs into an ICO container by hand (an ICO entry
may hold a PNG image since Windows Vista).

Design: dark rounded tile, blue panel, a white laptop with a green
"verified" tick - reads clearly even at 16 px.

    python tools/make_icon.py
"""

from __future__ import annotations

import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import png  # noqa: E402

BG = (13, 20, 33)        # tile background
PANEL = (27, 110, 243)   # brand blue
PANEL2 = (24, 84, 190)
WHITE = (245, 248, 252)
GREEN = (34, 197, 94)
DARK = (10, 16, 28)


def rounded(w: int, h: int, r: int) -> list:
    """mask[y][x] = True inside a rounded rectangle"""
    mask = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            cx = min(max(x, r), w - 1 - r)
            cy = min(max(y, r), h - 1 - r)
            mask[y][x] = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return mask


def circle_mask(w: int, h: int, cx: float, cy: float, rad: float) -> list:
    return [[(x - cx) ** 2 + (y - cy) ** 2 <= rad * rad for x in range(w)] for y in range(h)]


def draw(size: int) -> bytes:
    w = h = size
    s = size / 256.0                      # design space is 256x256
    rgb = bytearray([0, 0, 0, 0] * (w * h))

    tile = rounded(w, h, int(52 * s))
    panel = rounded(w - 2 * int(16 * s), h - 2 * int(16 * s), int(40 * s))
    badge = circle_mask(w, h, 186 * s, 186 * s, 58 * s)
    badge_ring = circle_mask(w, h, 186 * s, 186 * s, 66 * s)

    def put(x: int, y: int, c) -> None:
        o = (y * w + x) * 4
        rgb[o:o + 4] = bytes((c[0], c[1], c[2], 255))

    def blend(x: int, y: int, c, a: float) -> None:
        o = (y * w + x) * 4
        r0, g0, b0 = rgb[o], rgb[o + 1], rgb[o + 2]
        rgb[o] = int(r0 * (1 - a) + c[0] * a)
        rgb[o + 1] = int(g0 * (1 - a) + c[1] * a)
        rgb[o + 2] = int(b0 * (1 - a) + c[2] * a)
        rgb[o + 3] = 255

    # laptop geometry in design units
    scr_x0, scr_y0, scr_x1, scr_y1 = 52, 58, 204, 158
    base_y0, base_y1 = 166, 186
    base_x0, base_x1 = 34, 222

    for y in range(h):
        for x in range(w):
            dx, dy = x / s, y / s
            if not tile[y][x]:
                continue
            col = BG
            # blue panel behind the laptop
            if panel[y - int(16 * s)][x - int(16 * s)] if (y >= int(16 * s) and x >= int(16 * s)
                                                           and y < h - int(16 * s)
                                                           and x < w - int(16 * s)) else False:
                t = dy / 256.0
                col = tuple(int(PANEL[i] * (1 - t * 0.45) + PANEL2[i] * (t * 0.45)) for i in range(3))
            # screen outline
            if scr_x0 <= dx <= scr_x1 and scr_y0 <= dy <= scr_y1:
                edge = (dx <= scr_x0 + 7 or dx >= scr_x1 - 7 or
                        dy <= scr_y0 + 7 or dy >= scr_y1 - 7)
                col = WHITE if edge else DARK
                if not edge:
                    # faint scanline pattern inside the screen
                    if int(dy) % 10 < 2:
                        col = (30, 42, 66)
            # base / keyboard deck
            if base_y0 <= dy <= base_y1:
                t = (dy - base_y0) / max(1.0, (base_y1 - base_y0))
                x0 = base_x0 + (scr_x0 - 8 - base_x0) * (1 - t)
                x1 = base_x1 + (scr_x1 + 8 - base_x1) * (1 - t)
                if x0 <= dx <= x1:
                    col = WHITE
                    if base_y0 + 5 < dy < base_y1 - 4 and x0 + 10 < dx < x1 - 10:
                        col = (206, 216, 230)
            # green verified badge
            if badge[y][x]:
                col = GREEN
                # white tick
                tx, ty = (dx - 186) / 58.0, (dy - 186) / 58.0
                seg = (-0.45 <= tx <= 0.05 and abs(ty - (tx * 0.9 + 0.28)) < 0.16) or \
                      (-0.05 <= tx <= 0.5 and abs(ty - (-tx * 1.15 + 0.20)) < 0.16)
                if seg:
                    col = WHITE
            elif badge_ring[y][x]:
                col = BG
            put(x, y, col)

    # anti-alias the tile edge by alpha
    out = bytearray(w * h * 4)
    r = int(52 * s)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            out[o:o + 3] = rgb[o:o + 3]
            cx = min(max(x, r), w - 1 - r)
            cy = min(max(y, r), h - 1 - r)
            d2 = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            out[o + 3] = 255 if d2 <= r - 1 else (128 if d2 <= r + 0.6 else 0)
    return bytes(out)


def downscale(rgba: bytes, src: int, dst: int) -> bytes:
    box = src // dst
    out = bytearray(dst * dst * 4)
    for y in range(dst):
        for x in range(dst):
            r = g = b = a = 0
            for j in range(box):
                for i in range(box):
                    o = ((y * box + j) * src + (x * box + i)) * 4
                    r += rgba[o]; g += rgba[o + 1]; b += rgba[o + 2]; a += rgba[o + 3]
            n = box * box
            o = (y * dst + x) * 4
            out[o:o + 4] = bytes((r // n, g // n, b // n, a // n))
    return bytes(out)


def rgba_to_png(rgba: bytes, size: int) -> bytes:
    # PNG wants RGB (or RGBA); keep alpha for the icon tile
    import zlib
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def build_ico(images: list) -> bytes:
    """images = [(size, png_bytes), ...]"""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = b""
    blob = b""
    for size, data in images:
        w = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset)
        blob += data
        offset += len(data)
    return header + entries + blob


def main() -> int:
    sizes = [256, 48, 32, 16]
    base = draw(256)
    pngs = []
    for size in sizes:
        rgba = base if size == 256 else downscale(base, 256, size)
        pngs.append((size, rgba_to_png(rgba, size)))
    os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
    with open(os.path.join(ROOT, "assets", "icon.png"), "wb") as fh:
        fh.write(pngs[0][1])
    with open(os.path.join(ROOT, "assets", "icon.ico"), "wb") as fh:
        fh.write(build_ico(pngs))
    print("assets/icon.png %d bytes, assets/icon.ico %d bytes"
          % (len(pngs[0][1]), os.path.getsize(os.path.join(ROOT, "assets", "icon.ico"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
