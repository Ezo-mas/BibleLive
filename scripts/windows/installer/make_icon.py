"""Generate biblelive.ico (open book under a cross) with the standard library only.

    python scripts/windows/installer/make_icon.py [output.ico]

The icon has 16, 32, 48 and 256 px images. The small ones are classic 32-bit DIB entries
(what Tk's ``iconbitmap`` and old shells read); the 256 px one is PNG-compressed, as in
every modern .ico. The file is committed; rerun this only to change the artwork.
"""

import struct
import sys
import zlib
from pathlib import Path

SIZES = (16, 32, 48, 256)
SUPERSAMPLE = 4

Point = tuple[float, float]
Color = tuple[int, int, int]

NAVY_TOP: Color = (0x2B, 0x4C, 0x86)
NAVY_BOTTOM: Color = (0x14, 0x28, 0x4B)
GOLD: Color = (0xF2, 0xC1, 0x4E)
PAGE: Color = (0xF7, 0xF3, 0xE8)
SPINE: Color = (0xC9, 0xB9, 0x8E)

# Unit-square artwork: (convex polygon, color).
LEFT_PAGE: list[Point] = [(0.14, 0.54), (0.48, 0.60), (0.48, 0.88), (0.14, 0.82)]
RIGHT_PAGE: list[Point] = [(0.52, 0.60), (0.86, 0.54), (0.86, 0.82), (0.52, 0.88)]
SPINE_BAR: list[Point] = [(0.48, 0.60), (0.52, 0.60), (0.52, 0.88), (0.48, 0.88)]
CROSS_V: list[Point] = [(0.455, 0.11), (0.545, 0.11), (0.545, 0.47), (0.455, 0.47)]
CROSS_H: list[Point] = [(0.36, 0.19), (0.64, 0.19), (0.64, 0.275), (0.36, 0.275)]
SHAPES: list[tuple[list[Point], Color]] = [
    (LEFT_PAGE, PAGE),
    (RIGHT_PAGE, PAGE),
    (SPINE_BAR, SPINE),
    (CROSS_V, GOLD),
    (CROSS_H, GOLD),
]


def in_polygon(poly: list[Point], x: float, y: float) -> bool:
    """Point inside a convex polygon (any winding)."""
    sign = 0
    for i, (x1, y1) in enumerate(poly):
        x2, y2 = poly[(i + 1) % len(poly)]
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if cross == 0:
            continue
        s = 1 if cross > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


def in_rounded_square(x: float, y: float, margin: float = 0.03, radius: float = 0.2) -> bool:
    lo, hi = margin, 1.0 - margin
    if not (lo <= x <= hi and lo <= y <= hi):
        return False
    cx = min(max(x, lo + radius), hi - radius)
    cy = min(max(y, lo + radius), hi - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def sample(x: float, y: float) -> tuple[Color, float]:
    """Color and coverage (0 or 1) of one sub-pixel."""
    if not in_rounded_square(x, y):
        return (0, 0, 0), 0.0
    color = tuple(round(a + (b - a) * y) for a, b in zip(NAVY_TOP, NAVY_BOTTOM, strict=True))
    result: Color = (color[0], color[1], color[2])
    for poly, shape_color in SHAPES:
        if in_polygon(poly, x, y):
            result = shape_color
    return result, 1.0


def render(size: int) -> bytes:
    """Top-down RGBA bytes of a ``size`` x ``size`` image (antialiased by supersampling)."""
    n = SUPERSAMPLE
    out = bytearray()
    for py in range(size):
        for px in range(size):
            r = g = b = a = 0.0
            for sy in range(n):
                for sx in range(n):
                    (cr, cg, cb), cov = sample(
                        (px + (sx + 0.5) / n) / size, (py + (sy + 0.5) / n) / size
                    )
                    r += cr * cov
                    g += cg * cov
                    b += cb * cov
                    a += cov
            if a:
                out += bytes((round(r / a), round(g / a), round(b / a), round(255 * a / (n * n))))
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def png_bytes(size: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + rgba[y * size * 4 : (y + 1) * size * 4] for y in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def dib_bytes(size: int, rgba: bytes) -> bytes:
    """ICO image: BITMAPINFOHEADER (height doubled), bottom-up BGRA pixels, 1-bpp AND mask."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bytearray()
    for y in range(size - 1, -1, -1):
        row = rgba[y * size * 4 : (y + 1) * size * 4]
        for x in range(size):
            r, g, b, a = row[x * 4 : x * 4 + 4]
            pixels += bytes((b, g, r, a))
    mask = bytes(((size + 31) // 32) * 4 * size)  # all zero: alpha channel decides
    return header + bytes(pixels) + mask


def build_ico() -> bytes:
    images = []
    for size in SIZES:
        rgba = render(size)
        images.append((size, png_bytes(size, rgba) if size >= 256 else dib_bytes(size, rgba)))
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, data in images:
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _size, data in images:
        out += data
    return bytes(out)


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path(__file__).with_name("biblelive.ico")
    target.write_bytes(build_ico())
    print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
