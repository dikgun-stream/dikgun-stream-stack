"""Matte the brand mark out of the duotone avatar into a transparent PNG.

The brand avatars are opaque RGB on a cream field, so the mark has to be keyed
out rather than loaded as-is. Recolouring is deliberate:

  ink  -> cream   (an ink D is invisible on the dark card; "invert the white and
                   black on the D")
  red  -> red     (the G keeps its brand colour)

Also splits the mark into left (D) and right (G) halves so the outro can slide
them together while using the REAL logo geometry instead of typeface letters.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = os.path.join(os.path.dirname(os.path.dirname(HERE)), "dikgun-stream", "brand")
SRC = os.path.join(BRAND, "avatars", "03-avatar-duotone-1024.png")

BG = np.array([236.0, 228.0, 210.0])     # cream field
INK = np.array([22.0, 20.0, 18.0])       # D
RED = np.array([200.0, 22.0, 29.0])      # G

OUT_CREAM = (236, 228, 210)
OUT_RED = (200, 22, 29)
SPLIT = 0.48                              # D ends / G begins, in viewBox x


def build() -> str:
    if not os.path.isfile(SRC):
        raise SystemExit(f"missing brand asset: {SRC}")
    rgb = np.asarray(Image.open(SRC).convert("RGB")).astype(np.float32)

    d_bg = np.linalg.norm(rgb - BG, axis=2)
    d_ink = np.linalg.norm(rgb - INK, axis=2)
    d_red = np.linalg.norm(rgb - RED, axis=2)

    # soft alpha: anything far from the cream field is part of the mark
    alpha = np.clip((d_bg - 18.0) / 70.0, 0.0, 1.0)

    out = np.zeros((*alpha.shape, 4), dtype=np.uint8)
    is_red = d_red < d_ink
    out[..., 0] = np.where(is_red, OUT_RED[0], OUT_CREAM[0])
    out[..., 1] = np.where(is_red, OUT_RED[1], OUT_CREAM[1])
    out[..., 2] = np.where(is_red, OUT_RED[2], OUT_CREAM[2])
    out[..., 3] = (alpha * 255).astype(np.uint8)

    img = Image.fromarray(out, "RGBA")
    bbox = img.split()[3].point(lambda v: 255 if v > 12 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    path = os.path.join(HERE, "mark_rgba.png")
    img.save(path)

    # halves for the merge animation
    w, h = img.size
    cut = int(w * SPLIT)
    a = np.array(img)
    left = a.copy()
    left[:, cut:, 3] = 0
    right = a.copy()
    right[:, :cut, 3] = 0
    Image.fromarray(left, "RGBA").save(os.path.join(HERE, "mark_left.png"))
    Image.fromarray(right, "RGBA").save(os.path.join(HERE, "mark_right.png"))

    # report, so the matte can be checked without eyeballing it
    aa = np.array(img)
    op = aa[..., 3] > 200
    red_px = int((op & ((aa[..., 0].astype(int) - aa[..., 2]) > 40)).sum())
    print(f"  cropped to {img.size} from {SRC.rsplit(os.sep, 1)[-1]}")
    print(f"  opaque px {int(op.sum())}   red px {red_px}   cream px {int(op.sum()) - red_px}")
    print(f"  split at x={cut} ({SPLIT:.0%})")
    print(f"  wrote mark_rgba.png, mark_left.png, mark_right.png")
    return path


if __name__ == "__main__":
    build()
