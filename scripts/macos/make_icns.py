"""Generate biblelive.icns, the Mac app icon, with the standard library only.

    python scripts/macos/make_icns.py [output.icns]

The same artwork as the Windows icon (scripts/windows/installer/make_icon.py: an open book
under a cross), laid out on Apple's icon grid: the rounded square fills 824 of 1024 px with
the rest left transparent, so it sits the same size as other apps in the Dock and Finder.
Every size is a PNG entry (16 to 1024 px, 1x and 2x). The file is committed; rerun this only
to change the artwork.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "windows" / "installer"))

from make_icon import NAVY_BOTTOM, NAVY_TOP, SHAPES, Color, in_polygon, png_bytes  # noqa: E402

# Apple's grid: 100 px margin and a ~185 px corner radius on a 1024 px canvas.
MARGIN = 100 / 1024
RADIUS = 185 / 824  # as a fraction of the body

# (icns type, pixel size): ic11-ic14 are the 2x variants of 16, 32, 128 and 256 pt.
ENTRIES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic11", 32),
    (b"ic12", 64),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic13", 256),
    (b"ic09", 512),
    (b"ic14", 512),
    (b"ic10", 1024),
)


def _in_body(u: float, v: float) -> bool:
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        return False
    cu = min(max(u, RADIUS), 1.0 - RADIUS)
    cv = min(max(v, RADIUS), 1.0 - RADIUS)
    return (u - cu) ** 2 + (v - cv) ** 2 <= RADIUS**2


def sample(x: float, y: float) -> tuple[Color, float]:
    """Color and coverage of one sub-pixel; the artwork is drawn in the body's unit square."""
    u, v = (x - MARGIN) / (1 - 2 * MARGIN), (y - MARGIN) / (1 - 2 * MARGIN)
    if not _in_body(u, v):
        return (0, 0, 0), 0.0
    shade = tuple(round(a + (b - a) * v) for a, b in zip(NAVY_TOP, NAVY_BOTTOM, strict=True))
    result: Color = (shade[0], shade[1], shade[2])
    for poly, color in SHAPES:
        if in_polygon(poly, u, v):
            result = color
    return result, 1.0


def render(size: int) -> bytes:
    """Top-down RGBA bytes, antialiased by supersampling (less for the big sizes)."""
    n = 4 if size <= 256 else 2
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


def build_icns() -> bytes:
    pngs: dict[int, bytes] = {}
    body = bytearray()
    for kind, size in ENTRIES:
        if size not in pngs:
            pngs[size] = png_bytes(size, render(size))
        body += kind + struct.pack(">I", 8 + len(pngs[size])) + pngs[size]
    return b"icns" + struct.pack(">I", 8 + len(body)) + bytes(body)


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path(__file__).with_name("biblelive.icns")
    target.write_bytes(build_icns())
    print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
