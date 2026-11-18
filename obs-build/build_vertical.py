#!/usr/bin/env python3
"""
Build per-subject vertical (Aitum) scenes + the vertical overlay sources.

v3 change -- gameplay is now CROPPED, not letterboxed.

Rationale (user's): seeing the action large beats seeing the whole 16:9 frame
tiny. So we cut the sides and let the centre fill the frame.

Two things are being traded, and they should be kept distinct:
  * horizontal field of view  -- we deliberately throw the sides away
  * pixel density             -- cropping 1080 of 1920 wide keeps NATIVE pixels
                                 horizontally; the vertical scale-up is the only
                                 softness, controlled by `game_height`

OBS must be CLOSED -- it rewrites the collection on save.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

APPDATA = Path(os.environ["APPDATA"])
COLLECTION = APPDATA / "obs-studio" / "basic" / "scenes" / "DikGun.json"

AITUM_CANVAS = "c70dca39-1325-4243-9e11-14d60cb38f29"
V_W, V_H = 1080, 1920
PREV_VER = 537001985
SERVER = "http://127.0.0.1:7475"

# --- gameplay geometry ------------------------------------------------------
# Source is 1920x1080. Crop 420 px off each side -> 1080x1080 of NATIVE pixels.
GAME_CROP = 420
# How tall the game band is. 1080 = no vertical scaling (but only 56% of frame).
# 1440 = bottom quarter free for camera, at the cost of a 1.33x vertical stretch.
# 1300 is a compromise: ~20% stretch, ~32% of frame left for cam + chat.
GAME_HEIGHT = 1300
GAME_BOX = (0, 0, 1080, GAME_HEIGHT)

CAM_BOX = (600, GAME_HEIGHT + 30, 420, 236)     # bottom-right, clear of chat
CHAT_BOTTOM = 60

# letterboxed (non-cropped) bands, for content where cropping would destroy meaning
LETTERBOX_BOX = (0, 210, 1080, 608)
CAM_SMALL = (300, 850, 480, 270)
CAM_LARGE = (0, 420, 1080, 608)
BRB_BOX = (0, 656, 1080, 608)
FULL = (0, 0, 1080, 1920)


VERTICAL_INPUTS = {
    "OVL_Alerts_V":       {"url": f"{SERVER}/overlay/alerts"},
    "OVL_Chat_V":         {"url": f"{SERVER}/overlay/chat?bottom=470&width=520"},
    "OVL_Goals_V":        {"url": f"{SERVER}/overlay/goals"},
    "OVL_StartingSoon_V": {"url": f"{SERVER}/overlay/starting-soon"},
    "OVL_Topic_V":        {"url": f"{SERVER}/overlay/topic?bottom=520"},
}


def game(source: str) -> dict:
    """Cropped, vertically filled gameplay."""
    return {"source": source, "box": GAME_BOX, "crop_x": GAME_CROP, "stretch": True}


def box(source: str, b: tuple) -> dict:
    return {"source": source, "box": b}


LAYOUT = [
    {"name": "V_GAME_Tarkov", "items": [
        game("SRC_Capture_Tarkov"),
        box("SRC_Cam", CAM_BOX),
        box("OVL_Alerts_V", FULL),
        box("OVL_Chat_V", FULL),
        box("OVL_Topic_V", FULL),
    ]},
    {"name": "V_GAME_Deadlock", "items": [
        game("SRC_Capture_Deadlock"),
        box("SRC_Cam", CAM_BOX),
        box("OVL_Alerts_V", FULL),
        box("OVL_Chat_V", FULL),
        box("OVL_Topic_V", FULL),
    ]},
    {"name": "V_GAME_Apex", "items": [
        game("SRC_Capture_Apex"),
        box("SRC_Cam", CAM_BOX),
        box("OVL_Alerts_V", FULL),
        box("OVL_Chat_V", FULL),
        box("OVL_Topic_V", FULL),
    ]},
    # code stays letterboxed: cropping an editor cuts the text you came to read
    {"name": "V_CODE_Live", "items": [
        box("SRC_Capture_IDE", LETTERBOX_BOX),
        box("SRC_Cam", CAM_SMALL),
        box("OVL_Chat_V", FULL),
        box("OVL_Topic_V", FULL),
    ]},
    {"name": "V_JC_Talking", "items": [
        box("SRC_Cam", CAM_LARGE),
        box("OVL_Goals_V", FULL),
        box("OVL_Chat_V", FULL),
        box("OVL_Topic_V", FULL),
    ]},
    {"name": "V_SCREEN_Work", "items": [
        box("SRC_Capture_StreamMonitor", FULL),
        box("OVL_Chat_V", FULL),
    ]},
    {"name": "V_SHOW_StartingSoon", "items": [
        box("SRC_Cam", CAM_LARGE),
        box("OVL_StartingSoon_V", FULL),
    ]},
    {"name": "V_SHOW_BRB", "items": [
        box("OVL_BRB", BRB_BOX),
        box("OVL_Chat_V", FULL),
    ]},
    {"name": "V_SHOW_Ending", "items": [
        box("SRC_Cam", CAM_LARGE),
        box("OVL_Goals_V", FULL),
        box("OVL_Chat_V", FULL),
    ]},
]

LEGACY_SCENES = {"V_GAME_Gameplay", "V_CODE_Live", "V_JC_Talking",
                 "V_SHOW_StartingSoon", "V_SHOW_BRB"}


def to_rel(x: float, y: float) -> dict:
    half_h = V_H / 2.0
    return {"x": (x - V_W / 2.0) / half_h, "y": (y - V_H / 2.0) / half_h}


def size_to_rel(w: float, h: float) -> dict:
    half_h = V_H / 2.0
    return {"x": w / half_h, "y": h / half_h}


def make_item(spec: dict, source_uuid: str, item_id: int) -> dict:
    x, y, w, h = spec["box"]
    crop_x = spec.get("crop_x", 0)
    # stretch=True  -> OBS_BOUNDS_STRETCH (1) so the crop fills the band exactly
    # stretch=False -> OBS_BOUNDS_SCALE_INNER (2), aspect preserved
    bounds_type = 1 if spec.get("stretch") else 2

    return {
        "name": spec["source"],
        "source_uuid": source_uuid,
        "visible": True,
        "locked": False,
        "rot": 0.0,
        "scale_ref": {"x": float(V_W), "y": float(V_H)},
        "align": 5,
        "bounds_type": bounds_type,
        "bounds_align": 0,
        "bounds_crop": False,
        "crop_left": crop_x,
        "crop_top": 0,
        "crop_right": crop_x,
        "crop_bottom": 0,
        "id": item_id,
        "group_item_backup": False,
        "pos": {"x": float(x), "y": float(y)},
        "pos_rel": to_rel(x, y),
        "scale": {"x": 1.0, "y": 1.0},
        "scale_rel": {"x": 1.0, "y": 1.0},
        "bounds": {"x": float(w), "y": float(h)},
        "bounds_rel": size_to_rel(w, h),
        "scale_filter": "bicubic",
        "blend_method": "default",
        "blend_type": "normal",
        "show_transition": {"duration": 300},
        "hide_transition": {"duration": 300},
        "private_settings": {},
    }


def make_scene(spec: dict, uuid_by_name: dict[str, str]) -> dict:
    items = [make_item(s, uuid_by_name[s["source"]], i + 1) for i, s in enumerate(spec["items"])]
    return {
        "prev_ver": PREV_VER,
        "name": spec["name"],
        "uuid": str(uuid.uuid4()),
        "id": "scene",
        "versioned_id": "scene",
        "settings": {"id_counter": len(items), "custom_size": False, "items": items},
        "mixers": 0, "sync": 0, "flags": 0, "volume": 1.0, "balance": 0.5,
        "enabled": True, "muted": False,
        "push-to-mute": False, "push-to-mute-delay": 0,
        "push-to-talk": False, "push-to-talk-delay": 0,
        "hotkeys": {
            "OBSBasic.SelectScene": [],
            **{f"libobs.show_scene_item.{i+1}": [] for i in range(len(items))},
            **{f"libobs.hide_scene_item.{i+1}": [] for i in range(len(items))},
        },
        "deinterlace_mode": 0, "deinterlace_field_order": 0, "monitoring_type": 0,
        "canvas_uuid": AITUM_CANVAS,
        "private_settings": {},
    }


def make_browser_input(name: str, url: str) -> dict:
    return {
        "prev_ver": PREV_VER, "name": name, "uuid": str(uuid.uuid4()),
        "id": "browser_source", "versioned_id": "browser_source",
        "settings": {"url": url, "width": V_W, "height": V_H,
                     "shutdown": False, "restart_when_active": False,
                     "fps_custom": True, "fps": 30},
        "mixers": 255, "sync": 0, "flags": 0, "volume": 1.0, "balance": 0.5,
        "enabled": True, "muted": False,
        "push-to-mute": False, "push-to-mute-delay": 0,
        "push-to-talk": False, "push-to-talk-delay": 0,
        "hotkeys": {"ObsBrowser.Refresh": []},
        "deinterlace_mode": 0, "deinterlace_field_order": 0, "monitoring_type": 0,
        "private_settings": {},
    }


def main() -> int:
    if not COLLECTION.exists():
        print(f"ERROR: {COLLECTION} not found", file=sys.stderr)
        return 1

    data = json.loads(COLLECTION.read_text(encoding="utf-8"))
    generated = {s["name"] for s in LAYOUT}

    before = len(data["sources"])
    data["sources"] = [
        s for s in data["sources"]
        if not (s.get("id") == "scene" and s.get("canvas_uuid") == AITUM_CANVAS
                and s["name"] in (generated | LEGACY_SCENES))
    ]
    dropped_scenes = before - len(data["sources"])

    ours = set(VERTICAL_INPUTS)
    uuids = {s["uuid"] for s in data["sources"] if s.get("id") != "scene" and s["name"] in ours}
    before = len(data["sources"])
    data["sources"] = [s for s in data["sources"] if s["uuid"] not in uuids]
    dropped_inputs = before - len(data["sources"])

    for name, spec in VERTICAL_INPUTS.items():
        data["sources"].append(make_browser_input(name, spec["url"]))

    uuid_by_name = {s["name"]: s["uuid"] for s in data["sources"] if s.get("id") != "scene"}
    missing = sorted({s["source"] for spec in LAYOUT for s in spec["items"] if s["source"] not in uuid_by_name})
    if missing:
        print("ERROR: missing sources: " + ", ".join(missing), file=sys.stderr)
        return 2

    data["sources"].extend(make_scene(spec, uuid_by_name) for spec in LAYOUT)
    json.dump(data, open(COLLECTION, "w", encoding="utf-8"), indent=4, ensure_ascii=False)

    print(f"dropped {dropped_scenes} scene(s), {dropped_inputs} input(s); rebuilt {len(LAYOUT)} scenes")
    print(f"gameplay: crop {GAME_CROP}px per side -> 1080 wide native, scaled to 1080x{GAME_HEIGHT} "
          f"(vertical stretch {GAME_HEIGHT/1080:.2f}x)")
    print(f"camera band: y {GAME_HEIGHT+30}-{GAME_HEIGHT+30+236}")
    for spec in LAYOUT:
        print(f"  {spec['name']:22} <- " + ", ".join(s["source"] for s in spec["items"]))

    vert = [s for s in data["sources"] if s.get("id") == "scene" and s.get("canvas_uuid") == AITUM_CANVAS]
    main_ = [s for s in data["sources"] if s.get("id") == "scene" and s.get("canvas_uuid") != AITUM_CANVAS]
    print(f"\ntotal: {len(main_)} main-canvas, {len(vert)} vertical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
