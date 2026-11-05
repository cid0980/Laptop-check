"""
qr.py -- a small, dependency-free QR Code encoder (byte mode, versions 1-10).

Why hand-rolled? The report carries a QR code that links back to the shop's
record of this scan. Adding the `qrcode` + `pypng` packages would pull extra
dependencies into the single .exe for a 30-line visual. This module implements
ISO/IEC 18004 directly: Reed-Solomon over GF(256), block interleaving, the
eight masks with the full four-rule penalty score, and BCH format/version
information. Output is a plain 0/1 matrix, which the report renders as inline
SVG (crisp in print, no image decoding needed).

Supports versions 1-10 (up to 271 bytes at level L), which is far more than a
URL or a scan id needs. If the payload does not fit, encode() returns None and
the report simply omits the QR block - it never breaks.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# GF(256) with the QR primitive polynomial 0x11D
# --------------------------------------------------------------------------

EXP = [0] * 512
LOG = [0] * 256


def _init_gf() -> None:
    x = 1
    for i in range(255):
        EXP[i] = x
        LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        EXP[i] = EXP[i - 255]


_init_gf()


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


def rs_generator(degree: int) -> List[int]:
    poly = [1]
    for i in range(degree):
        # multiply by (x - alpha^i)
        new = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            new[j] ^= coef
            new[j + 1] ^= gf_mul(coef, EXP[i])
        poly = new
    return poly


def rs_encode(data: List[int], ec_count: int) -> List[int]:
    gen = rs_generator(ec_count)
    res = list(data) + [0] * ec_count
    for i in range(len(data)):
        coef = res[i]
        if coef:
            for j in range(len(gen)):
                res[i + j] ^= gf_mul(gen[j], coef)
    return res[len(data):]


# --------------------------------------------------------------------------
# capacity tables  (version -> ecc level -> (ec_per_block, [(blocks, data_cw), ...]))
# --------------------------------------------------------------------------

TOTAL_CODEWORDS = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134, 6: 172, 7: 196,
                   8: 242, 9: 292, 10: 346}

REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}

ECC: Dict[str, Dict[int, Tuple[int, List[Tuple[int, int]]]]] = {
    "L": {1: (7, [(1, 19)]), 2: (10, [(1, 34)]), 3: (15, [(1, 55)]), 4: (20, [(1, 80)]),
          5: (26, [(1, 108)]), 6: (18, [(2, 68)]), 7: (20, [(2, 78)]),
          8: (24, [(2, 97)]), 9: (30, [(2, 116)]), 10: (18, [(2, 68), (2, 69)])},
    "M": {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
          5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]),
          8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
          10: (26, [(4, 43), (1, 44)])},
    "Q": {1: (13, [(1, 13)]), 2: (22, [(1, 22)]), 3: (18, [(2, 17)]), 4: (26, [(2, 24)]),
          5: (18, [(2, 15), (2, 16)]), 6: (24, [(4, 19)]), 7: (18, [(2, 14), (4, 15)]),
          8: (22, [(4, 18), (2, 19)]), 9: (20, [(4, 16), (4, 17)]),
          10: (24, [(6, 19), (2, 20)])},
    "H": {1: (17, [(1, 9)]), 2: (28, [(1, 16)]), 3: (22, [(2, 13)]), 4: (16, [(4, 9)]),
          5: (22, [(2, 11), (2, 12)]), 6: (28, [(4, 15)]), 7: (26, [(4, 13), (1, 14)]),
          8: (26, [(4, 14), (2, 15)]), 9: (24, [(4, 12), (4, 13)]),
          10: (28, [(6, 15), (2, 16)])},
}

FORMAT_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

ALIGNMENT: Dict[int, List[int]] = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}


def data_codewords(version: int, ecl: str) -> int:
    ec_per_block, groups = ECC[ecl][version]
    return sum(b * d for b, d in groups)


def byte_capacity(version: int, ecl: str) -> int:
    count_bits = 8 if version <= 9 else 16
    return (data_codewords(version, ecl) * 8 - 4 - count_bits) // 8


# --------------------------------------------------------------------------
# bit stream
# --------------------------------------------------------------------------

class BitBuffer:
    def __init__(self) -> None:
        self.bits: List[int] = []

    def put(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def __len__(self) -> int:
        return len(self.bits)

    def to_bytes(self) -> List[int]:
        while len(self.bits) % 8:
            self.bits.append(0)
        out = []
        for i in range(0, len(self.bits), 8):
            v = 0
            for b in self.bits[i:i + 8]:
                v = (v << 1) | b
            out.append(v)
        return out


def make_codewords(data: bytes, version: int, ecl: str) -> List[int]:
    buf = BitBuffer()
    buf.put(0b0100, 4)                                  # byte mode
    buf.put(len(data), 8 if version <= 9 else 16)
    for b in data:
        buf.put(b, 8)
    total = data_codewords(version, ecl) * 8
    # terminator
    for _ in range(min(4, total - len(buf))):
        buf.bits.append(0)
    codewords = buf.to_bytes()
    pad = [0xEC, 0x11]
    i = 0
    while len(codewords) < data_codewords(version, ecl):
        codewords.append(pad[i % 2])
        i += 1
    return codewords


def interleave(codewords: List[int], version: int, ecl: str) -> List[int]:
    ec_per_block, groups = ECC[ecl][version]
    blocks: List[List[int]] = []
    ec_blocks: List[List[int]] = []
    pos = 0
    for count, size in groups:
        for _ in range(count):
            chunk = codewords[pos:pos + size]
            pos += size
            blocks.append(chunk)
            ec_blocks.append(rs_encode(chunk, ec_per_block))
    out: List[int] = []
    maxlen = max(len(b) for b in blocks)
    for i in range(maxlen):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_per_block):
        for e in ec_blocks:
            if i < len(e):
                out.append(e[i])
    return out


# --------------------------------------------------------------------------
# matrix
# --------------------------------------------------------------------------

def _finder(m: List[List[int]], r: List[List[bool]], row: int, col: int) -> None:
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            rr, cc = row + dr, col + dc
            if not (0 <= rr < len(m) and 0 <= cc < len(m)):
                continue
            if 0 <= dr <= 6 and 0 <= dc <= 6:
                on = (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4))
                m[rr][cc] = 1 if on else 0
            else:
                m[rr][cc] = 0
            r[rr][cc] = True


def _alignment(m: List[List[int]], r: List[List[bool]], row: int, col: int) -> None:
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            rr, cc = row + dr, col + dc
            on = (abs(dr) == 2 or abs(dc) == 2 or (dr == 0 and dc == 0))
            m[rr][cc] = 1 if on else 0
            r[rr][cc] = True


def build_matrix(version: int, ecl: str, codewords: List[int],
                 mask: int) -> List[List[int]]:
    size = version * 4 + 17
    m = [[0] * size for _ in range(size)]
    r = [[False] * size for _ in range(size)]

    _finder(m, r, 0, 0)
    _finder(m, r, 0, size - 7)
    _finder(m, r, size - 7, 0)

    # timing patterns
    for i in range(8, size - 8):
        v = 1 if i % 2 == 0 else 0
        m[6][i] = v
        r[6][i] = True
        m[i][6] = v
        r[i][6] = True

    # alignment patterns
    coords = ALIGNMENT[version]
    for rr in coords:
        for cc in coords:
            if (rr == 6 and cc == 6) or (rr == 6 and cc == size - 7) or (rr == size - 7 and cc == 6):
                continue
            _alignment(m, r, rr, cc)

    # reserve format areas
    for i in range(9):
        if not r[8][i]:
            r[8][i] = True
            m[8][i] = 0
        if not r[i][8]:
            r[i][8] = True
            m[i][8] = 0
    for i in range(8):
        if not r[8][size - 1 - i]:
            r[8][size - 1 - i] = True
            m[8][size - 1 - i] = 0
        if not r[size - 1 - i][8]:
            r[size - 1 - i][8] = True
            m[size - 1 - i][8] = 0
    # dark module
    m[size - 8][8] = 1
    r[size - 8][8] = True

    # version information (v >= 7)
    if version >= 7:
        vinfo = version_info(version)
        for i in range(18):
            bit = (vinfo >> i) & 1
            a, b = i // 3, i % 3 + size - 11
            m[a][b] = bit
            r[a][b] = True
            m[b][a] = bit
            r[b][a] = True

    # data placement
    bits: List[int] = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    bits.extend([0] * REMAINDER_BITS.get(version, 0))

    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if c < 0 or r[row][c]:
                    continue
                bit = bits[idx] if idx < len(bits) else 0
                idx += 1
                if mask_fn(mask, row, c):
                    bit ^= 1
                m[row][c] = bit
        upward = not upward
        col -= 2

    # format information (bit i = i-th LSB of the 15-bit value)
    fmt = format_info(ecl, mask)

    def bit(i: int) -> int:
        return (fmt >> i) & 1

    # copy 1: around the top-left finder pattern
    for i in range(6):
        m[i][8] = bit(i)
    m[7][8] = bit(6)
    m[8][8] = bit(7)
    m[8][7] = bit(8)
    for i in range(9, 15):
        m[8][14 - i] = bit(i)
    # copy 2: split between the other two finder patterns
    for i in range(8):
        m[8][size - 1 - i] = bit(i)
    for i in range(8, 15):
        m[size - 15 + i][8] = bit(i)
    m[size - 8][8] = 1                      # the always-dark module
    return m


def mask_fn(mask: int, row: int, col: int) -> bool:
    if mask == 0:
        return (row + col) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return col % 3 == 0
    if mask == 3:
        return (row + col) % 3 == 0
    if mask == 4:
        return (row // 2 + col // 3) % 2 == 0
    if mask == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if mask == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    if mask == 7:
        return ((row + col) % 2 + (row * col) % 3) % 2 == 0
    return False


def format_info(ecl: str, mask: int) -> int:
    data = (FORMAT_BITS[ecl] << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = ((data << 10) | (rem & 0x3FF)) ^ 0x5412
    return bits


def version_info(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (version << 12) | (rem & 0xFFF)


# --------------------------------------------------------------------------
# penalty scoring (rule 1-4)
# --------------------------------------------------------------------------

def penalty(m: List[List[int]]) -> int:
    size = len(m)
    score = 0
    # rule 1: runs of five or more
    for line in [m[r] for r in range(size)] + [[m[r][c] for r in range(size)] for c in range(size)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    # rule 2: 2x2 blocks
    for r in range(size - 1):
        for c in range(size - 1):
            v = m[r][c]
            if v == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    # rule 3: finder-like patterns
    pat_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat_b = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for r in range(size):
        for c in range(size - 10):
            row = m[r][c:c + 11]
            if row == pat_a or row == pat_b:
                score += 40
    for c in range(size):
        for r in range(size - 10):
            col = [m[r + i][c] for i in range(11)]
            if col == pat_a or col == pat_b:
                score += 40
    # rule 4: dark/light balance
    dark = sum(sum(row) for row in m)
    total = size * size
    ratio = 100.0 * dark / total
    score += 10 * int(abs(ratio - 50) // 5)
    return score


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def encode(text: str, ecl: str = "M", max_version: int = 10) -> Optional[List[List[int]]]:
    """Return the QR module matrix, or None if the payload does not fit."""
    if not text:
        return None
    data = text.encode("utf-8")
    version = None
    for v in range(1, max_version + 1):
        if byte_capacity(v, ecl) >= len(data):
            version = v
            break
    if version is None:
        return None
    cw = make_codewords(data, version, ecl)
    final = interleave(cw, version, ecl)
    best = None
    best_score = None
    for mask in range(8):
        m = build_matrix(version, ecl, final, mask)
        s = penalty(m)
        if best_score is None or s < best_score:
            best, best_score = m, s
    return best


def to_svg(matrix: List[List[int]], scale: int = 4, quiet: int = 4,
           dark: str = "#0b0e14", light: str = "#ffffff") -> str:
    size = len(matrix) + quiet * 2
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
        'shape-rendering="crispEdges">' % (size, size, size * scale, size * scale),
        '<rect width="%d" height="%d" fill="%s"/>' % (size, size, light),
    ]
    # merge runs of dark modules into single rects (smaller file, cleaner print)
    for r, row in enumerate(matrix):
        c = 0
        while c < len(row):
            if row[c]:
                start = c
                while c < len(row) and row[c]:
                    c += 1
                parts.append('<rect x="%d" y="%d" width="%d" height="1" fill="%s"/>'
                             % (start + quiet, r + quiet, c - start, dark))
            else:
                c += 1
    parts.append("</svg>")
    return "".join(parts)


def to_ascii(matrix: List[List[int]], quiet: int = 2) -> str:
    rows = []
    for r in range(-quiet, len(matrix) + quiet):
        line = ""
        for c in range(-quiet, len(matrix) + quiet):
            if 0 <= r < len(matrix) and 0 <= c < len(matrix):
                line += "##" if matrix[r][c] else "  "
            else:
                line += "  "
        rows.append(line)
    return "\n".join(rows)
