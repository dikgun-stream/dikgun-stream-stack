#!/usr/bin/env python3
"""Add Apex HUD insets to the vertical scene, Deadlock/Tarkov style.

The existing verticals solve this by showing their game_capture source several
times, each copy with a different crop, scaled into the 1080x1920 canvas. Apex had
no insets, and its band crop (420px off each side = source x 420..1500) destroys
the HUD.

Apex is harder than Deadlock or Tarkov, because its HUD bleeds INTO that centre
crop, so insets alone would show the HUD twice. Measured on a live frame:

    healthbar    canvas x   0..234   y 1094..1270   36.4% visible  -> masked
    weapons      canvas x 863..1080  y 1151..1275   34.8% visible  -> covered by
                                                                      the weapons
                                                                      inset itself
    squads       canvas x 1114..1379                          0%     -> inset
    minimap      canvas x -380..-124                          0%     -> inset

So: four insets, one opaque mask over the healthbar bleed, and the weapons inset
sized to sit over the weapons bleed instead of beside it. The input overlay moves
right, out of the masked corner.

Coordinates are template-matched against a live OBS frame (GetSourceScreenshot of
SRC_Capture_Apex) and each one was confirmed by cropping it out and looking at it.
Scores were moderate on the text panels only because the live values differ from
the reference screenshots.

Usage
-----
    python add_apex_hud_insets.py            # dry run, prints the plan
    python add_apex_hud_insets.py --apply    # write the collection

OBS MUST BE CLOSED for --apply. A timestamped backup is written first.
"""

from __future__ import annotations

import argparse
import datetime
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
INPUT_ITEM = "OVL_Input_V_apex_Apex"
MASK_SOURCE = "SRC_HUD_MASK_V"
MARK = "APEX_HUD_"

# name suffix -> (src_x, src_y, w, h, canvas_x, canvas_y, canvas_w, canvas_h)
INSETS = {
    "HEALTHBAR": (13,   908, 425, 122,    0,    0, 480, 138),
    "SQUADS":    (1534,  50, 265,  75,  742,    0, 338,  96),
    "MINIMAP":   (40,    50, 256, 310,  802,  120, 272, 329),
    "WEAPONS":   (1283, 956, 624, 103,  455, 1148, 620, 128),
}

# opaque cover for the healthbar bleed
MASK = (0, 1088, 236, 188)
# the input overlay started at (30,1030) 420x240, inside the masked corner
INPUT_NEW_POS = (620, 880)

INK = [0.070588, 0.078431, 0.086275, 1.0]


def crop_for(x, y, w, h):
    """OBS crops from each edge, so right/bottom are distances, not coordinates."""
    return {
        "crop_left": int(x), "crop_top": int(y),
        "crop_right": int(SRC_W - (x + w)),
        "crop_bottom": int(SRC_H - (y + h)),
    }


def item(name, uuid, pos, size, align=5):
    return {
        "name": name, "source_uuid": uuid, "visible": True, "locked": True,
        "rot": 0.0, "scale_ref": {"x": 1080.0, "y": 1920.0}, "align": align,
        "bounds_type": 1, "bounds_align": 0, "bounds_crop": False,
        "id": 0, "group_item_backup": False,
        "pos": {"x": float(pos[0]), "y": float(pos[1])},
        # OBS treats the *_rel forms as canonical and derives pos/bounds from them.
        # Derived from a known-good item (Deadlock band): bounds_rel = bounds/960
        # and pos_rel = (pos - canvas_centre)/960, where the centre is (540,960).
        # Getting this wrong once already placed every inset off-canvas at half
        # size, which is why nothing appeared to change.
        "pos_rel": {"x": (pos[0] - 540.0) / 960.0,
                    "y": (pos[1] - 960.0) / 960.0},
        "scale": {"x": 1.0, "y": 1.0}, "scale_rel": {"x": 1.0, "y": 1.0},
        "bounds": {"x": float(size[0]), "y": float(size[1])},
        "bounds_rel": {"x": size[0] / 960.0, "y": size[1] / 960.0},
        "scale_filter": "bilinear", "blend_method": "default",
        "blend_type": "normal",
        "show_transition": {"duration": 0}, "hide_transition": {"duration": 0},
        "private_settings": {},
    }


def ensure_mask_source(j, scene_items):
    """A solid colour source for the mask; created once, reused on re-runs."""
    src = next((s for s in j["sources"] if s.get("name") == MASK_SOURCE), None)
    if src:
        return src["uuid"]
    uuid = __import__("uuid").uuid4().hex
    j["sources"].append({
        "prev_ver": 537001985, "name": MASK_SOURCE,
        "uuid": uuid, "id": "color_source_v3",
        "versioned_id": "color_source_v3", "settings": {
            "color": INK, "width": 1920, "height": 1080,
        },
        "mixers": 0, "sync": 0, "flags": 0, "volume": 1.0,
        "balance": 0.5, "enabled": True, "muted": False,
        "push-to-mute": False, "push-to-mute-delay": 0,
        "push-to-talk": False, "push-to-talk-delay": 0,
        "sync": 0, "filters": [], "deinterlace_mode": 0,
        "deinterlace_field_order": 0, "monitoring_type": 0,
        "canvas_uuid": None, "hotkeys": {},
    })
    return uuid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--collection", default=str(COLLECTION))
    a = ap.parse_args()

    path = Path(a.collection)
    j = json.loads(path.read_text(encoding="utf-8"))

    scene = next((s for s in j["sources"] if s.get("id") == "scene"
                  and s.get("canvas_uuid") == CANVAS and s.get("name") == SCENE), None)
    if scene is None:
        raise SystemExit(f"scene not found: {SCENE}")
    cap = next((s for s in j["sources"] if s.get("name") == CAPTURE), None)
    if cap is None:
        raise SystemExit(f"capture not found: {CAPTURE}")

    items = scene["settings"].setdefault("items", [])
    before = len(items)
    items[:] = [i for i in items if not (i.get("name") or "").startswith(MARK)]
    # An earlier version named the insets after the capture source, so they became
    # indistinguishable from the main band and survived every cleanup. Keep only
    # the FIRST item using the capture (the gameplay band) and drop the rest.
    seen_cap = False
    kept = []
    for i in items:
        if (i.get("name") or "") == CAPTURE:
            if seen_cap:
                continue
            seen_cap = True
        kept.append(i)
    items[:] = kept
    removed = before - len(items)

    mask_uuid = ensure_mask_source(j, items)
    next_id = max([i.get("id", 0) for i in items] + [0]) + 1

    new = []
    # mask first so the insets draw over it
    m = item(MARK + "MASK", mask_uuid, (MASK[0], MASK[1]), (MASK[2], MASK[3]))
    m.update(crop_for(0, 0, SRC_W, SRC_H))
    m["id"] = next_id; next_id += 1
    new.append(m)
    for key, rect in INSETS.items():
        x, y, w, h, cx, cy, cw, ch = rect
        it = item(MARK + key, cap["uuid"], (cx, cy), (cw, ch))
        it.update(crop_for(x, y, w, h))
        it["id"] = next_id; next_id += 1
        new.append(it)

    insert_at = next((k for k, i in enumerate(items)
                      if (i.get("name") or "").startswith("OVL_")), len(items))
    items[insert_at:insert_at] = new

    # move the input overlay out of the masked corner
    moved = False
    for i in items:
        if (i.get("name") or "") == INPUT_ITEM:
            old = (i["pos"]["x"], i["pos"]["y"])
            i["pos"] = {"x": float(INPUT_NEW_POS[0]), "y": float(INPUT_NEW_POS[1])}
            i["pos_rel"] = {"x": (INPUT_NEW_POS[0] - 540.0) / 960.0,
                            "y": (INPUT_NEW_POS[1] - 960.0) / 960.0}
            moved = old != INPUT_NEW_POS

    print(f"  scene        : {scene['name']}")
    print(f"  capture      : {CAPTURE}")
    print(f"  removed      : {removed} earlier inset(s)")
    print(f"  inserting    : {len(new)} item(s) at draw index {insert_at}")
    print(f"  input overlay: {'moved to ' + str(INPUT_NEW_POS) if moved else 'already placed'}")
    print()
    print("  item            source crop            canvas pos      size")
    for key, (x, y, w, h, cx, cy, cw, ch) in INSETS.items():
        print(f"  {MARK+key:16} {x:4},{y:4} {w:4}x{h:3}   ({cx:4},{cy:4})  {cw:4}x{ch:3}")
    print(f"  {MARK+'MASK':16} opaque cover over the healthbar bleed "
          f"({MASK[0]},{MASK[1]}) {MASK[2]}x{MASK[3]}")

    if not a.apply:
        print()
        print("  dry run - nothing written. Close OBS, then re-run with --apply")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(f".json.bak-apexhud-{stamp}")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(j, indent=4), encoding="utf-8")
    print()
    print(f"  written: {path}")
    print(f"  backup : {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
