"""Draw the app icon (a dark tile with a rising bar chart) as PNGs with the standard library only.

    python3 tools/make_icons.py            # writes finsim/static/icon-{192,256,512}.png

The PNGs feed the web manifest (install from the browser), the desktop launchers (`python3 -m finsim install`)
and the .ico / .icns bundles those launchers derive at install time.
"""
from __future__ import annotations

import os
import struct
import sys
import zlib
from typing import List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), "finsim", "static")

BG = (13, 17, 23)            # tile
BG2 = (22, 27, 34)           # inner panel
BAR = (46, 204, 113)         # green bars
BAR_DOWN = (231, 76, 60)     # one red bar
GRID = (48, 54, 61)
LINE = (88, 166, 255)


def _png(width: int, height: int, rows: List[bytes]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def draw(size: int) -> bytes:
    s = size
    px: List[List[Tuple[int, int, int, int]]] = [[(0, 0, 0, 0)] * s for _ in range(s)]
    r = s * 0.22                        # corner radius of the tile

    def inside_tile(x: float, y: float) -> bool:
        cx = min(max(x, r), s - r)
        cy = min(max(y, r), s - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def rect(x0: float, y0: float, x1: float, y1: float, col: Tuple[int, int, int]) -> None:
        for y in range(max(0, int(y0)), min(s, int(y1))):
            for x in range(max(0, int(x0)), min(s, int(x1))):
                px[y][x] = col + (255,)

    for y in range(s):
        for x in range(s):
            if inside_tile(x + 0.5, y + 0.5):
                px[y][x] = BG + (255,)
    # inner panel
    m = s * 0.14
    rect(m, m, s - m, s - m, BG2)
    # grid lines
    for k in (0.35, 0.5, 0.65):
        rect(m, s * k, s - m, s * k + max(1, s // 128), GRID)
    # bars: five, rising, one down
    n = 5
    gap = (s - 2 * m) / (n * 1.6 + 0.6)
    bw = gap * 1.0
    heights = (0.30, 0.42, 0.36, 0.58, 0.72)
    x = m + gap * 0.6
    base = s - m - s * 0.06
    for i, h in enumerate(heights):
        top = base - (s - 2 * m) * h
        rect(x, top, x + bw, base, BAR_DOWN if i == 2 else BAR)
        x += gap * 1.6
    # a line across the bar tops
    pts = []
    x = m + gap * 0.6 + bw / 2
    for h in heights:
        pts.append((x, base - (s - 2 * m) * h - s * 0.05))
        x += gap * 1.6
    lw = max(2, s // 48)
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        steps = int(max(abs(xb - xa), abs(yb - ya))) + 1
        for t in range(steps + 1):
            u = t / steps
            cx, cy = xa + (xb - xa) * u, ya + (yb - ya) * u
            rect(cx - lw / 2, cy - lw / 2, cx + lw / 2, cy + lw / 2, LINE)
    rows = [b"".join(struct.pack("BBBB", *p) for p in row) for row in px]
    return _png(s, s, rows)


def main(out_dir: str = STATIC) -> List[str]:
    written = []
    for size in (192, 256, 512):
        fp = os.path.join(out_dir, f"icon-{size}.png")
        with open(fp, "wb") as f:
            f.write(draw(size))
        written.append(fp)
    return written


if __name__ == "__main__":
    for fp in main(sys.argv[1] if len(sys.argv) > 1 else STATIC):
        print("wrote", fp)
