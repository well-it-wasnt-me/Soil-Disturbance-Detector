"""Draw the app icon (the sidebar brand mark from src/dgd/ui/index.html) as a Windows .ico.

    python packaging/windows/make_icon.py packaging/windows/dgd.ico

Drawn rather than committed, so the repository stays free of binaries and the
icon cannot drift from the mark in the UI. Needs Pillow, which matplotlib
already brings.
"""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw

TILE, INK, ACCENT = (0xE6, 0xEC, 0xEF), (0x16, 0x23, 0x2B), (0x2B, 0xA3, 0xA3)
INK_HALF = tuple((a + b) // 2 for a, b in zip(INK, TILE, strict=True))  # the faded wave, ink at 50% on the tile
SIZES = [16, 24, 32, 48, 64, 128, 256]
S = 1024  # drawn big, scaled down: Pillow lines are not antialiased


def _cubic(p0, p1, p2, p3, n=400):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3, strict=True)) for t in (i / n for i in range(n + 1))]


def _wave(y):
    # SVG: M6 y c3-2.4 5.5-2.4 8 0 s5 2.4 8 0, in the 28 x 28 viewBox
    return _cubic((6, y), (9, y - 2.4), (11.5, y - 2.4), (14, y)) + _cubic((14, y), (16.5, y + 2.4), (19, y + 2.4), (22, y))[1:]


def draw() -> Image.Image:
    k = S / 28
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=7 * k, fill=TILE)
    r = 1.8 * k / 2
    for y, color in ((12.5, INK_HALF), (18, INK)):
        # A round brush dabbed along the curve: thick Pillow lines get jagged joints.
        for x, yy in _wave(y):
            d.ellipse((x * k - r, yy * k - r, x * k + r, yy * k + r), fill=color)
    d.rounded_rectangle((11 * k, 6 * k, 18 * k, 9.5 * k), radius=1.2 * k, fill=ACCENT)
    return img


def main(out: str) -> None:
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"Wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dgd.ico")
