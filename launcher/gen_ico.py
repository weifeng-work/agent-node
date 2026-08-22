# -*- coding: utf-8 -*-
"""Generate multi-size tray .ico files (icon-green.ico / icon-red.ico).

Design: bright ring (green/red) + white inner highlight + dark border, so the
icon stays clearly visible on both light and dark taskbars.
Requires Pillow:  python -m pip install pillow
"""
import os
from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64]


def make_square(size, fill, border):
    """Draw a filled circle: dark border + white ring + bright inner fill.

    Antialiased via supersampling so the icon stays sharp and clearly visible
    on both light and dark taskbars.
    """
    scale = 4
    big = size * scale
    bigimg = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bigimg)
    # outer dark border ring
    bd.ellipse([0, 0, big - 1, big - 1], fill=border)
    # white ring (highlight)
    w = big * 0.22
    bd.ellipse([w, w, big - 1 - w, big - 1 - w], fill=(255, 255, 255, 255))
    # inner bright fill
    iw = big * 0.38
    bd.ellipse([iw, iw, big - 1 - iw, big - 1 - iw], fill=fill)
    return bigimg.resize((size, size), Image.LANCZOS)


def write_ico(path, fill, border):
    """Write a multi-size .ico using Pillow's native ICO encoder (reliable)."""
    imgs = [make_square(s, fill, border) for s in SIZES]
    base = imgs[imgs.index(max(imgs, key=lambda i: i.size))]
    base.save(path, format="ICO", sizes=[(s, s) for s in SIZES])

    """Re-open to confirm each entry decodes correctly."""
    verify = Image.open(path)
    verify.load()
    verify.seek(0)
    frames = 0
    try:
        while True:
            verify.seek(frames)
            frames += 1
    except EOFError:
        pass
    return frames


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    n1 = write_ico(os.path.join(here, "icon-green.ico"),
                   fill=(46, 209, 84, 255), border=(20, 90, 40, 255))
    n2 = write_ico(os.path.join(here, "icon-red.ico"),
                   fill=(235, 64, 64, 255), border=(120, 24, 24, 255))
    print(f"generated icon-green.ico ({n1} frames) / icon-red.ico ({n2} frames)")


if __name__ == "__main__":
    main()