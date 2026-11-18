#!/usr/bin/env python3
"""
Decide whether the BSG launcher's Play button is actually on screen.

The launcher is CefSharp, so its UI is invisible to UI Automation (verified: 8
accessibility nodes, no buttons, and --force-renderer-accessibility does not
change that). A blind coordinate click is therefore the only option -- but a
blind click is dangerous, because that bottom-right corner shows different
controls depending on state (Play / Stop / login / update).

So instead of trusting the position, this compares the button region against a
reference capture. Only click when the picture matches.

Emits JSON: {"match": bool, "score": float, "luma": float, "x": int, "y": int}
x/y are absolute screen coordinates of the middle of the button.
"""

from __future__ import annotations

import json
import sys

import numpy as np
from PIL import Image

# The Play button occupies this fraction of the launcher window. Measured from a
# live capture; deliberately generous so small layout drift still matches.
BOX = (0.755, 0.835, 0.985, 0.955)
MATCH_SCORE = 0.55      # Pearson correlation on a downscaled grayscale crop
MATCH_LUMA = 95.0       # the Play button is a bright sand gradient


def crop_fraction(img: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    w, h = img.size
    return img.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))


def signature(img: Image.Image) -> np.ndarray:
    small = img.convert("L").resize((64, 24), Image.BILINEAR)
    return np.asarray(small, dtype=float).ravel()


def main() -> int:
    if len(sys.argv) < 4:
        print(json.dumps({"error": "usage: eft_play.py <screenshot> <winX> <winY> [ref]"}))
        return 2

    shot = sys.argv[1]
    win_x, win_y = int(sys.argv[2]), int(sys.argv[3])
    ref_path = sys.argv[4] if len(sys.argv) > 4 else "eft-play-ref.png"

    screen = Image.open(shot).convert("RGB")
    region = crop_fraction(screen, BOX)
    ref = Image.open(ref_path).convert("RGB")

    a, b = signature(region), signature(ref)
    if a.std() < 1e-6 or b.std() < 1e-6:
        score = 0.0
    else:
        score = float(np.corrcoef(a, b)[0, 1])
    luma = float(np.asarray(region.convert("L"), dtype=float).mean())

    w, h = screen.size
    cx = win_x + int((BOX[0] + BOX[2]) / 2 * w)
    cy = win_y + int((BOX[1] + BOX[3]) / 2 * h)

    print(json.dumps({
        "match": bool(score >= MATCH_SCORE and luma >= MATCH_LUMA),
        "score": round(score, 3),
        "luma": round(luma, 1),
        "x": cx,
        "y": cy,
        "window": [w, h],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
