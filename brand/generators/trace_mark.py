#!/usr/bin/env python
"""Trace the DG mark from a raster image into polygon geometry.

The supplied artwork is a 300x300 JPEG, and the mark inside it is only about
198x104 pixels. Rasterising that at banner size means upscaling a JPEG, which
gives soft edges and coloured fringing. Tracing it once to polygons means every
later output is rendered from geometry instead, so it is crisp at any size.

    python trace_mark.py --in dg-source.jpg --out dg-vector.json

The result is only 3 shapes and 88 points, so it is cheap to inspect by hand.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

# Upscale before thresholding: tracing a 300px JPEG directly makes the contour
# follow compression noise along every edge.
TRACE_SCALE = 6
BLUR_KERNEL = (5, 5)
THRESHOLD = 128
MIN_COMPONENT_AREA = 2000
APPROX_EPSILON = 2.2
MIN_CONTOUR_AREA = 200


def trace(src: Path) -> dict:
    img = cv2.imread(str(src))
    if img is None:
        raise SystemExit(f"cannot read {src}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    big = cv2.resize(gray, None, fx=TRACE_SCALE, fy=TRACE_SCALE,
                     interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, BLUR_KERNEL, 0)

    # the mark is dark on a light field, so invert to make the mark white
    _, binary = cv2.threshold(big, THRESHOLD, 255, cv2.THRESH_BINARY_INV)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    clean = np.zeros_like(binary)
    kept = 0
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] > MIN_COMPONENT_AREA:
            clean[labels == i] = 255
            kept += 1
    if kept == 0:
        raise SystemExit("no mark found - check the threshold")

    ys, xs = np.where(clean > 0)
    mark = clean[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    height, width = mark.shape

    contours, _ = cv2.findContours(mark, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    for contour in contours:
        if cv2.contourArea(contour) < MIN_CONTOUR_AREA:
            continue
        approx = cv2.approxPolyDP(contour, APPROX_EPSILON, True)
        polys.append([[int(x), int(y)] for x, y in approx.reshape(-1, 2)])

    return {
        "w": int(width),
        "h": int(height),
        "polys": polys,
        "source": src.name,
        "note": "polygons in source-image units; scale by target_height / h",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="src", default="dg-source.jpg")
    ap.add_argument("--out", dest="dst", default="dg-vector.json")
    args = ap.parse_args()

    data = trace(Path(args.src))
    Path(args.dst).write_text(json.dumps(data, indent=1), encoding="utf-8")

    points = sum(len(p) for p in data["polys"])
    print(f"  shapes : {len(data['polys'])}")
    print(f"  points : {points}")
    print(f"  raster : {data['w']}x{data['h']}  aspect {data['w']/data['h']:.3f}")
    print(f"  wrote  : {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
