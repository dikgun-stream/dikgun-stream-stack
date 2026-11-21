#!/usr/bin/env python3
"""Add Apex HUD insets to the vertical scene, in the same style as Deadlock/Tarkov.

How the existing verticals do it
--------------------------------
V_GAME_Deadlock and V_GAME_Tarkov each reference their game_capture source MORE
THAN ONCE, each copy carrying a different crop, then scale and position those
copies as small insets over the 1080x1920 canvas. Deadlock uses three (lower
right), Tarkov two (top left, bottom left). No extra sources are needed - the
same capture is simply shown several times with different crops.

Why Apex needs this
-------------------
The vertical game band crops 420px off each side of a 1920x1080 capture, so the
visible source window is x 420..1500. Measured on a live frame, that destroys:

    evo_banner    36.4% of its width survives
    weapons       34.8%
    squads_left    0%
    minimap        0%

Coordinates below were template-matched against a live OBS frame and confirmed by
eye; the low match scores on the two text panels were only because the live values
differ from the reference screenshots (7 squads / 20 players vs 12 / 36).

Layout, chosen so nothing already in the scene is covered:
    game band   (0,0) 1080x1300     camera  (0,1297) 1080x623
    input overlay (30,1030) 420x240

Usage
-----
    python add_apex_hud_insets.py            # dry run, prints the plan
    python add_apex_hud_insets.py --apply    # write the collection

OBS MUST BE CLOSED for --apply: it rewrites the collection on save. A timestamped
backup is written next to the collection first.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

APPDATA = Path(os.environ["APPDATA"])
COLLECTION = APPDATA / "obs-studio" / "basic" / "scenes" / "DikGun.json"
CANVAS = "c70dca39-1325-4243-9e11-14d60cb38f29"
SRC_W, SRC_H = 1920, 1080
SCENE = "V_GAME_Apex"
CAPTURE = "SRC_Capture_Apex"
MARK = "APEX_HUD_"          # name prefix so re-runs replace rather than duplicate

# name -> (source_x, source_y, w, h, canvas_x, canvas_y, canvas_w, canvas_h)
INSETS = {
    f"{MARK}EVO":     (12,   913, 642, 142,    0,    0, 560, 124),
    f"{MARK}SQUADS":  (1534,  50, 265,  75,  742,    0, 338,  96),
    f"{MARK}MINIMAP": (40,    50, 256, 310,  802,  860, 272, 329),
    f"{MARK}WEAPONS": (1283, 956, 624, 103,  455, 1186, 620, 102),
}


def crop_for(x, y, w, h):
    """OBS crops from each edge, so right/bottom are distances, not coordinates."""
    return {
        "crop_left": int(x),
        "crop_top": int(y),
        "crop_right": int(SRC_W - (x + w)),
        "crop_bottom": int(SRC_H - (y + h)),
    }


def build_item(src_uuid, name, rect):
    x, y, w, h, cx, cy, cw, ch = rect
    item = {
        "name": CAPTURE,               # same source, shown again
        "source_uuid": src_uuid,
        "visible": True,
        "locked": True,                # insets should not be dragged by accident
        "rot": 0.0,
        "scale_ref": {"x": 1080.0, "y": 1920.0},
        "align": 5,                    # top-left
        "bounds_type": 1,              # scale to bounds
        "bounds_align": 0,
        "bounds_crop": False,
        **crop_for(x, y, w, h),
        "id": 0,                       # filled in by the caller
        "group_item_backup": False,
        "pos": {"x": float(cx), "y": float(cy)},
        "pos_rel": {"x": (cx / 1080.0) * 2 - 1, "y": (cy / 1920.0) * 2 - 1},
        "scale": {"x": 1.0, "y": 1.0},
        "scale_rel": {"x": 1.0, "y": 1.0},
        "bounds": {"x": float(cw), "y": float(ch)},
        "bounds_rel": {"x": cw / 1080.0, "y": ch / 1920.0},
        "scale_filter": "bilinear",
        "blend_method": "default",
        "blend_type": "normal",
        "show_transition": {"duration": 0},
        "hide_transition": {"duration": 0},
        "private_settings": {},
    }
    return item


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the collection")
    ap.add_argument("--collection", default=str(COLLECTION))
    a = ap.parse_args()

    path = Path(a.collection)
    if not path.is_file():
        raise SystemExit(f"collection not found: {path}")

    j = json.loads(path.read_text(encoding="utf-8"))
    scenes = [s for s in j.get("sources", []) if s.get("id") == "scene"
              and s.get("canvas_uuid") == CANVAS]
    if not scenes:
        raise SystemExit(f"no vertical scenes on canvas {CANVAS}")

    scene = next((s for s in scenes if s.get("name") == SCENE), None)
    if scene is None:
        raise SystemExit(f"scene not found: {SCENE}")

    src = next((s for s in j["sources"] if s.get("name") == CAPTURE), None)
    if src is None:
        raise SystemExit(f"capture source not found: {CAPTURE}")
    uuid = src.get("uuid") or src.get("source_uuid")

    items = scene["settings"].setdefault("items", [])

    # idempotent: drop any previous run's insets
    before = len(items)
    items[:] = [i for i in items if not (i.get("name") or "").startswith(MARK)]
    removed = before - len(items)

    next_id = max([i.get("id", 0) for i in items] + [0]) + 1
    new_items = []
    for name, rect in INSETS.items():
        it = build_item(uuid, name, rect)
        it["id"] = next_id
        next_id += 1
        new_items.append(it)

    # draw order: right after the camera, under the full-canvas browser overlays,
    # so chat and alerts stay readable on top
    insert_at = next((k for k, i in enumerate(items)
                      if (i.get("name") or "").startswith("OVL_")), len(items))
    items[insert_at:insert_at] = new_items

    print(f"  scene      : {scene['name']}")
    print(f"  capture    : {CAPTURE} (uuid {uuid})")
    print(f"  previous   : {removed} inset(s) from an earlier run removed")
    print(f"  inserting  : {len(new_items)} inset(s) at draw index {insert_at}")
    print(f"  total items: {len(items)}")
    print()
    print("  name              source crop (x,y,w,h)        canvas pos      size")
    for name, (x, y, w, h, cx, cy, cw, ch) in INSETS.items():
        c = crop_for(x, y, w, h)
        print(f"  {name:16} {x:4},{y:4} {w:4}x{h:3}   "
              f"({cx:4},{cy:4})  {cw:4}x{ch:3}   "
              f"crop L{c['crop_left']} T{c['crop_top']} R{c['crop_right']} B{c['crop_bottom']}")

    # collision check against the items that stay
    print()
    boxes = [(i.get("name"), i.get("pos", {}).get("x", 0), i.get("pos", {}).get("y", 0),
              i.get("bounds", {}).get("x", 0), i.get("bounds", {}).get("y", 0))
             for i in items]
    clashes = []
    for name, (x, y, w, h, cx, cy, cw, ch) in INSETS.items():
        for other, ox, oy, ow, oh in boxes:
            if other == name or other == CAPTURE or (other or "").startswith("OVL_"):
                continue
            if cx < ox + ow and cx + cw > ox and cy < oy + oh and cy + ch > oy:
                clashes.append((name, other))
    if clashes:
        for a_, b_ in clashes:
            print(f"  WARNING: {a_} overlaps {b_}")
    else:
        print("  no overlaps with the camera or the input overlay")

    if not a.apply:
        print()
        print("  dry run - nothing written. Close OBS, then re-run with --apply")
        return 0

    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(f".json.bak-apexhud-{stamp}")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(j, indent=4), encoding="utf-8")
    print()
    print(f"  written : {path}")
    print(f"  backup  : {backup.name}")
    print("  Start OBS and open V_GAME_Apex to check the insets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
