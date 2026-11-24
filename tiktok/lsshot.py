#!/usr/bin/env python3
"""Screenshot and measure the LIVE Studio canvas over CDP.

The mirror needs geometry: OBS gives us pixel rects on a 1920x1080 / 1080x1920
canvas, while TL stores a `transform {translateX, translateY, scaleX, scaleY,
size{cx,cy}, preview_cx, preview_cy}`. To convert between them we have to know
where a source actually lands on screen, which means looking at the canvas.

This module:
  * finds the preview element in the studio window and reports its rect,
  * captures a PNG of just that region (Page.captureScreenshot with clip),
  * provides a colour-blob measurement helper so a solid probe source can be
    located precisely.

Usage:
    python lsshot.py                 # capture preview -> preview.png, print rect
    python lsshot.py --full          # whole window instead
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import lsclient as ls

# candidates for the canvas preview container, most specific first
PREVIEW_SELECTORS = [
    "[class*=preview-container]",
    "[class*=previewContainer]",
    "[class*=canvas-preview]",
    "[class*=preview] canvas",
    "canvas",
    "video",
]


def preview_rect() -> dict | None:
    sel_json = json.dumps(PREVIEW_SELECTORS)
    body = ("const sels = " + sel_json + ";"
            "for (const sel of sels) {"
            "  const els = [...document.querySelectorAll(sel)];"
            "  const best = els.map(e => { const r = e.getBoundingClientRect();"
            "      return {sel, x: Math.round(r.left), y: Math.round(r.top),"
            "              w: Math.round(r.width), h: Math.round(r.height)}; })"
            "    .filter(o => o.w > 120 && o.h > 120)"
            "    .sort((a,b) => (b.w*b.h) - (a.w*a.h))[0];"
            "  if (best) return best;"
            "}"
            "return null;")
    r = ls.run(body)
    v = r.get("value")
    return v if isinstance(v, dict) else None


async def _capture(clip: dict | None, out: Path) -> bool:
    import websockets
    t = ls.main_target()
    async with websockets.connect(t["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024,
                                  open_timeout=20) as ws:
        params = {"format": "png"}
        if clip:
            params["clip"] = {"x": clip["x"], "y": clip["y"],
                              "width": clip["w"], "height": clip["h"], "scale": 1}
        await ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot",
                                  "params": params}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
            if msg.get("id") != 1:
                continue
            res = msg.get("result", {})
            data = res.get("data")
            if not data:
                print("  capture failed:", json.dumps(msg.get("error"))[:200])
                return False
            out.write_bytes(base64.b64decode(data))
            return True


def measure_colour(png: Path, rgb: tuple[int, int, int], tol: int = 40) -> dict | None:
    """Bounding box of pixels close to `rgb`, in screenshot coordinates."""
    try:
        import numpy as np
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        print("  (measure_colour needs numpy + Pillow)", exc)
        return None
    im = np.asarray(Image.open(png).convert("RGB")).astype("int16")
    d = np.abs(im - np.array(rgb, dtype="int16")).sum(axis=2)
    ys, xs = np.where(d <= tol)
    if xs.size == 0:
        return None
    return {"x0": int(xs.min()), "y0": int(ys.min()),
            "x1": int(xs.max()), "y1": int(ys.max()),
            "w": int(xs.max() - xs.min() + 1), "h": int(ys.max() - ys.min() + 1),
            "pixels": int(xs.size)}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    full = "--full" in argv
    out = Path("preview.png")
    try:
        ls.main_target()
    except Exception as exc:  # noqa: BLE001
        print(f"  no studio target: {exc}")
        return 2

    rect = preview_rect()
    print(f"  preview rect: {json.dumps(rect)}")
    clip = None if full or not rect else rect
    ok = asyncio.run(_capture(clip, out))
    if not ok:
        return 1
    size = out.stat().st_size
    print(f"  wrote {out.resolve()}  ({size/1024:.1f} KB)")
    if rect and not full:
        print(f"  region: {rect['w']}x{rect['h']} at ({rect['x']},{rect['y']}) in the window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
