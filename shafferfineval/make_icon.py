"""
ShafferFinEval -- generate the app icon.

Writes `assets/shafferfineval.ico` (and matching PNGs) with no third-party
imaging library, because nothing else in this project needs one either.

The icon is committed, but this script is the source of truth for it: an
icon you can regenerate is a decision you can revisit, a checked-in binary
nobody can rebuild is not.

    python3 make_icon.py
"""

from __future__ import annotations

import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "assets")

#: Matches the terminal palette in views/common.py and .streamlit/config.toml.
BACKGROUND = (14, 17, 23, 255)        # #0e1117
PANEL = (22, 27, 34, 255)             # #161b22
BORDER = (44, 51, 58, 255)            # #2c333a
LINE = (122, 166, 60, 255)            # #7aa63c  -- the bullish green
BASELINE = (107, 118, 129, 255)       # #6b7681

SIZES = (16, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4

#: The rising line, in unit coordinates with y growing downward.
CHART = ((0.16, 0.70), (0.33, 0.54), (0.46, 0.62), (0.62, 0.34), (0.84, 0.24))
CORNER_RADIUS = 0.20
STROKE = 0.075


def _blend(dst: tuple, src: tuple, alpha: float) -> tuple:
    """Source-over composite of `src` onto `dst` at `alpha`."""
    if alpha <= 0:
        return dst
    if alpha >= 1:
        return src
    return tuple(int(round(d + (s - d) * alpha)) for d, s in zip(dst, src))


def _rounded_rect_contains(x: float, y: float, radius: float) -> bool:
    """Is (x, y) inside the unit square with rounded corners?"""
    cx = min(max(x, radius), 1.0 - radius)
    cy = min(max(y, radius), 1.0 - radius)
    dx, dy = x - cx, y - cy
    return (dx * dx + dy * dy) <= radius * radius


def _distance_to_segment(px, py, ax, ay, bx, by) -> float:
    """Shortest distance from a point to a line segment."""
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    length_sq = vx * vx + vy * vy
    if length_sq == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / length_sq))
    qx, qy = ax + t * vx, ay + t * vy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def _distance_to_polyline(px, py, points) -> float:
    return min(
        _distance_to_segment(px, py, points[i][0], points[i][1],
                             points[i + 1][0], points[i + 1][1])
        for i in range(len(points) - 1)
    )


def render(size: int) -> bytes:
    """Render one RGBA icon at `size` x `size`, supersampled then averaged."""
    big = size * SUPERSAMPLE
    # Thin strokes vanish at 16px, so scale the line up as the icon shrinks.
    stroke = STROKE * (1.0 + (48 - min(size, 48)) / 48.0 * 0.55)
    border_width = max(1.0 / big, 0.012)

    hi = []
    for row in range(big):
        y = (row + 0.5) / big
        line = []
        for col in range(big):
            x = (col + 0.5) / big
            if not _rounded_rect_contains(x, y, CORNER_RADIUS):
                line.append((0, 0, 0, 0))
                continue
            # Panel fill, with a slightly lighter inner face.
            pixel = BACKGROUND
            if _rounded_rect_contains(x, y, CORNER_RADIUS) and not \
                    _rounded_rect_contains(x, y, CORNER_RADIUS - border_width):
                pixel = BORDER
            elif 0.10 < x < 0.90 and 0.10 < y < 0.90:
                pixel = PANEL
            # A dim baseline under the chart.
            if abs(y - 0.78) < 0.016 and 0.14 < x < 0.86:
                pixel = _blend(pixel, BASELINE, 0.55)
            # The rising line on top.
            if _distance_to_polyline(x, y, CHART) < stroke / 2:
                pixel = LINE
            line.append(pixel)
        hi.append(line)

    # Downsample by averaging each SUPERSAMPLE x SUPERSAMPLE block.
    out = bytearray()
    n = SUPERSAMPLE * SUPERSAMPLE
    for row in range(size):
        for col in range(size):
            r = g = b = a = 0
            for dy in range(SUPERSAMPLE):
                for dx in range(SUPERSAMPLE):
                    pr, pg, pb, pa = hi[row * SUPERSAMPLE + dy][col * SUPERSAMPLE + dx]
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            if a:
                out += bytes((r // a, g // a, b // a, a // n))
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def write_png(rgba: bytes, size: int) -> bytes:
    """Minimal RGBA PNG. No filtering -- these images are tiny."""
    raw = b"".join(
        b"\x00" + rgba[row * size * 4:(row + 1) * size * 4] for row in range(size))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def write_ico(pngs: dict, path: str) -> None:
    """Pack PNG images into a Windows .ico (PNG-in-ICO, Vista and later)."""
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(header) + 16 * len(sizes)
    entries, blobs = b"", b""
    for size in sizes:
        data = pngs[size]
        # 256 is written as 0 in the directory -- the field is one byte.
        entries += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, "wb") as handle:
        handle.write(header + entries + blobs)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    pngs = {}
    for size in SIZES:
        pngs[size] = write_png(render(size), size)
        print(f"  rendered {size}x{size}")
    ico_path = os.path.join(OUT_DIR, "shafferfineval.ico")
    write_ico(pngs, ico_path)
    png_path = os.path.join(OUT_DIR, "shafferfineval-256.png")
    with open(png_path, "wb") as handle:
        handle.write(pngs[256])
    print(f"wrote {ico_path} ({os.path.getsize(ico_path)} bytes)")
    print(f"wrote {png_path} ({os.path.getsize(png_path)} bytes)")


if __name__ == "__main__":
    main()
