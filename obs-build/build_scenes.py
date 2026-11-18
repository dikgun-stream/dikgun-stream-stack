#!/usr/bin/env python3
"""
Generate the 'Path to Power' OBS scene collection (horizontal canvas).

Design goals
------------
1. Never destroy the existing collection. This writes a NEW collection file
   and leaves 'Untitled.json' untouched.
2. Declarative layout. Editing LAYOUT below and re-running is how we iterate
   per game / per stream topic.
3. Reuse existing sources verbatim (settings, uuid) so hardware bindings and
   StreamElements overlay URLs survive.
4. Fail loudly if a scene item points at a source that does not exist.

Usage:
    python build_scenes.py            # write the collection
    python build_scenes.py --dry-run  # validate + report, write nothing
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import uuid
from pathlib import Path

APPDATA = Path(os.environ["APPDATA"])
OBS_SCENES = APPDATA / "obs-studio" / "basic" / "scenes"
SOURCE_COLLECTION = OBS_SCENES / "Untitled.json"
OUTPUT_COLLECTION = OBS_SCENES / "DikGun.json"

COLLECTION_NAME = "DikGun"
MAIN_CANVAS = "6c69626f-6273-4c00-9d88-c5136d61696e"  # libobs main canvas
CANVAS_W, CANVAS_H = 1920, 1080
PREV_VER = 537001985
SCENE_VERSION = 2


# --------------------------------------------------------------------------
# Source renaming map: original name -> new canonical name
# Anything not listed keeps its original name.
# --------------------------------------------------------------------------
RENAME = {
    "EFT Capture": "SRC_Capture_Tarkov",
    "Logitech Camera": "SRC_Cam",
    "In-Game with Alerts": "OVL_Combined_TEMP",
    "Be Right Back - DM": "OVL_BRB",
}

# --------------------------------------------------------------------------
# New inputs to create (not present in the source collection)
# --------------------------------------------------------------------------
NEW_INPUTS = {
    "SRC_Capture_Deadlock": {
        "id": "game_capture",
        "settings": {"capture_mode": "window", "window": ""},
    },
    "SRC_Capture_Apex": {
        "id": "game_capture",
        "settings": {"capture_mode": "window", "window": ""},
    },
    "SRC_Capture_IDE": {
        "id": "window_capture",
        "settings": {"method": 2, "window": ""},  # 2 = Windows Graphics Capture
    },
    "SRC_Capture_Desktop": {
        "id": "monitor_capture",
        "settings": {"monitor_id": 0},
    },
}


# --------------------------------------------------------------------------
# Layout helpers
# --------------------------------------------------------------------------
def full(source: str) -> dict:
    """Fill the whole canvas at native scale, no scaling."""
    return {
        "source": source,
        "pos": (0.0, 0.0),
        "scale": (1.0, 1.0),
        "bounds": None,
    }


def boxed(source: str, x: float, y: float, w: float, h: float) -> dict:
    """
    Fixed-size placement using OBS 'scale to inner bounds'.

    Using bounds rather than a raw scale factor means the item keeps the same
    on-screen size even if the source resolution changes later (e.g. raising
    the webcam from 640x480 to 1280x720) -- no re-layout needed.
    """
    return {
        "source": source,
        "pos": (x, y),
        "scale": (1.0, 1.0),
        "bounds": (w, h),
    }


CAM_CORNER_W, CAM_CORNER_H = 480.0, 270.0
CAM_MARGIN = 24.0

CAM_TOP_RIGHT = boxed(
    "SRC_Cam",
    CANVAS_W - CAM_CORNER_W - CAM_MARGIN,
    CAM_MARGIN,
    CAM_CORNER_W,
    CAM_CORNER_H,
)

CAM_CENTERED = boxed(
    "SRC_Cam",
    (CANVAS_W - 1280.0) / 2,
    (CANVAS_H - 720.0) / 2,
    1280.0,
    720.0,
)


# --------------------------------------------------------------------------
# Scene layout -- EDIT THIS to iterate
# Items are painted in order: last item is on top.
# --------------------------------------------------------------------------
LAYOUT = [
    {
        "name": "GAME_Tarkov",
        "items": [full("SRC_Capture_Tarkov"), CAM_TOP_RIGHT, full("OVL_Combined_TEMP")],
    },
    {
        "name": "GAME_Deadlock",
        "items": [full("SRC_Capture_Deadlock"), CAM_TOP_RIGHT, full("OVL_Combined_TEMP")],
    },
    {
        "name": "GAME_Apex",
        "items": [full("SRC_Capture_Apex"), CAM_TOP_RIGHT, full("OVL_Combined_TEMP")],
    },
    {
        "name": "CODE_Live",
        "items": [full("SRC_Capture_IDE"), CAM_TOP_RIGHT, full("OVL_Combined_TEMP")],
    },
    {
        "name": "JC_Talking",
        "items": [CAM_CENTERED, full("OVL_Combined_TEMP")],
    },
    {
        "name": "SHOW_StartingSoon",
        "items": [full("SRC_Cam"), full("OVL_Combined_TEMP")],
    },
    {
        "name": "SHOW_BRB",
        "items": [full("OVL_BRB")],
    },
    {
        "name": "SHOW_Ending",
        "items": [CAM_CENTERED, full("OVL_Combined_TEMP")],
    },
]


DEFAULT_SCENE = "GAME_Tarkov"


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------
def make_input(name: str, spec: dict) -> dict:
    kind = spec["id"]
    return {
        "prev_ver": PREV_VER,
        "name": name,
        "uuid": str(uuid.uuid4()),
        "id": kind,
        "versioned_id": kind,
        "settings": spec.get("settings", {}),
        "mixers": 255,
        "sync": 0,
        "flags": 0,
        "volume": 1.0,
        "balance": 0.5,
        "enabled": True,
        "muted": False,
        "push-to-mute": False,
        "push-to-mute-delay": 0,
        "push-to-talk": False,
        "push-to-talk-delay": 0,
        "hotkeys": {},
        "deinterlace_mode": 0,
        "deinterlace_field_order": 0,
        "monitoring_type": 0,
        "private_settings": {},
    }


def to_rel(x: float, y: float) -> dict:
    """
    Convert canvas pixel coords to OBS's normalized scene space.

        rel = (pos - canvas_center) / (canvas_height / 2)

    Verified against the two populated items in the source collection to
    ~1e-6. Works for positions and for bound sizes alike.
    """
    half_h = CANVAS_H / 2.0
    return {
        "x": (x - CANVAS_W / 2.0) / half_h,
        "y": (y - CANVAS_H / 2.0) / half_h,
    }


def size_to_rel(w: float, h: float) -> dict:
    half_h = CANVAS_H / 2.0
    return {"x": w / half_h, "y": h / half_h}


def make_item(spec: dict, source_uuid: str, item_id: int) -> dict:
    pos = spec["pos"]
    scale = spec["scale"]
    bounds = spec["bounds"]

    bounds_type = 2 if bounds else 0  # 2 = OBS_BOUNDS_SCALE_INNER
    bounds_xy = {"x": bounds[0], "y": bounds[1]} if bounds else {"x": 0.0, "y": 0.0}

    return {
        "name": spec["source"],
        "source_uuid": source_uuid,
        "visible": True,
        "locked": False,
        "rot": 0.0,
        "scale_ref": {"x": float(CANVAS_W), "y": float(CANVAS_H)},
        "align": 5,
        "bounds_type": bounds_type,
        "bounds_align": 0,
        "bounds_crop": False,
        "crop_left": 0,
        "crop_top": 0,
        "crop_right": 0,
        "crop_bottom": 0,
        "id": item_id,
        "group_item_backup": False,
        "pos": {"x": float(pos[0]), "y": float(pos[1])},
        "pos_rel": to_rel(pos[0], pos[1]),
        "scale": {"x": float(scale[0]), "y": float(scale[1])},
        "scale_rel": {"x": float(scale[0]), "y": float(scale[1])},
        "bounds": bounds_xy,
        "bounds_rel": size_to_rel(bounds[0], bounds[1]) if bounds else {"x": 0.0, "y": 0.0},
        "scale_filter": "bicubic",
        "blend_method": "default",
        "blend_type": "normal",
        "show_transition": {"duration": 300},
        "hide_transition": {"duration": 300},
        "private_settings": {},
    }


def make_scene(name: str, items: list[dict], uuid_by_name: dict[str, str]) -> dict:
    built = [make_item(spec, uuid_by_name[spec["source"]], i + 1) for i, spec in enumerate(items)]
    return {
        "prev_ver": PREV_VER,
        "name": name,
        "uuid": str(uuid.uuid4()),
        "id": "scene",
        "versioned_id": "scene",
        "settings": {
            "id_counter": len(built),
            "custom_size": False,
            "items": built,
        },
        "mixers": 0,
        "sync": 0,
        "flags": 0,
        "volume": 1.0,
        "balance": 0.5,
        "enabled": True,
        "muted": False,
        "push-to-mute": False,
        "push-to-mute-delay": 0,
        "push-to-talk": False,
        "push-to-talk-delay": 0,
        "hotkeys": {
            "OBSBasic.SelectScene": [],
            **{f"libobs.show_scene_item.{i + 1}": [] for i in range(len(built))},
            **{f"libobs.hide_scene_item.{i + 1}": [] for i in range(len(built))},
        },
        "deinterlace_mode": 0,
        "deinterlace_field_order": 0,
        "monitoring_type": 0,
        "canvas_uuid": MAIN_CANVAS,
        "private_settings": {},
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def build() -> dict:
    src = json.loads(SOURCE_COLLECTION.read_text(encoding="utf-8"))

    # 1. Carry over inputs, renaming where mapped.
    inputs: list[dict] = []
    uuid_by_name: dict[str, str] = {}

    for entry in src["sources"]:
        if entry.get("id") == "scene":
            continue  # old scenes are discarded
        item = copy.deepcopy(entry)
        item["name"] = RENAME.get(item["name"], item["name"])
        uuid_by_name[item["name"]] = item["uuid"]
        inputs.append(item)

    # 2. Add new inputs.
    for name, spec in NEW_INPUTS.items():
        if name in uuid_by_name:
            continue
        entry = make_input(name, spec)
        uuid_by_name[name] = entry["uuid"]
        inputs.append(entry)

    # 3. Build scenes.
    scenes = [make_scene(s["name"], s["items"], uuid_by_name) for s in LAYOUT]

    # 4. Assemble collection.
    out = {
        "name": COLLECTION_NAME,
        "DesktopAudioDevice1": copy.deepcopy(src["DesktopAudioDevice1"]),
        "DesktopAudioDevice2": copy.deepcopy(src.get("DesktopAudioDevice2")),
        "AuxAudioDevice1": copy.deepcopy(src["AuxAudioDevice1"]),
        "AuxAudioDevice2": copy.deepcopy(src.get("AuxAudioDevice2")),
        "sources": inputs + scenes,
        "groups": [],
        "scene_order": [{"name": s["name"]} for s in scenes],
        "current_scene": DEFAULT_SCENE,
        "current_program_scene": DEFAULT_SCENE,
        "canvases": copy.deepcopy(src.get("canvases", [])),
        "current_transition": src.get("current_transition", "Fade"),
        "transition_duration": src.get("transition_duration", 300),
        "transitions": copy.deepcopy(src.get("transitions", [])),
        "quick_transitions": copy.deepcopy(src.get("quick_transitions", [])),
        "saved_projectors": [],
        "preview_locked": False,
        "scaling_enabled": False,
        "scaling_level": -10,
        "scaling_off_x": 0.0,
        "scaling_off_y": 0.0,
        "virtual-camera": copy.deepcopy(src.get("virtual-camera", {})),
        "modules": copy.deepcopy(src.get("modules", {})),
        "resolution": {"x": CANVAS_W, "y": CANVAS_H},
        "migration_resolution": {"x": CANVAS_W, "y": CANVAS_H},
        "version": SCENE_VERSION,
    }

    # Device IDs go stale every time the default audio device changes (new
    # headset, unplugged DAC, etc). Bind the global desktop/mic sources to the
    # Windows default so a regenerated collection can never reintroduce dead
    # device GUIDs.
    for key in ("DesktopAudioDevice1", "AuxAudioDevice1"):
        entry = out.get(key)
        if isinstance(entry, dict):
            entry.setdefault("settings", {})["device_id"] = "default"

    return out


def validate(collection: dict) -> list[str]:
    problems: list[str] = []
    sources = collection["sources"]
    by_uuid = {s["uuid"]: s for s in sources}
    names = {s["name"] for s in sources}

    if len(names) != len(sources):
        problems.append("duplicate source names present")

    for scene in (s for s in sources if s.get("id") == "scene"):
        seen_ids = set()
        for item in scene["settings"]["items"]:
            if item["source_uuid"] not in by_uuid:
                problems.append(
                    f"{scene['name']}: item '{item['name']}' -> missing uuid {item['source_uuid']}"
                )
            elif by_uuid[item["source_uuid"]]["name"] != item["name"]:
                problems.append(
                    f"{scene['name']}: item name '{item['name']}' != source name "
                    f"'{by_uuid[item['source_uuid']]['name']}'"
                )
            if item["id"] in seen_ids:
                problems.append(f"{scene['name']}: duplicate item id {item['id']}")
            seen_ids.add(item["id"])

    ordered = [o["name"] for o in collection["scene_order"]]
    scene_names = [s["name"] for s in sources if s.get("id") == "scene"]
    if sorted(ordered) != sorted(scene_names):
        problems.append("scene_order does not match scene list")

    for key in ("current_scene", "current_program_scene"):
        if collection[key] not in scene_names:
            problems.append(f"{key} '{collection[key]}' is not a scene")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SOURCE_COLLECTION.exists():
        print(f"ERROR: source collection not found: {SOURCE_COLLECTION}", file=sys.stderr)
        return 1

    collection = build()
    problems = validate(collection)

    scene_names = [s["name"] for s in collection["sources"] if s.get("id") == "scene"]
    input_names = [s["name"] for s in collection["sources"] if s.get("id") != "scene"]

    print(f"Collection : {COLLECTION_NAME}")
    print(f"Scenes     : {len(scene_names)}")
    for n in scene_names:
        print(f"  - {n}")
    print(f"Inputs     : {len(input_names)}")
    for n in sorted(input_names):
        print(f"  - {n}")

    if problems:
        print("\nVALIDATION FAILED:")
        for p in problems:
            print(f"  ! {p}")
        return 2

    print("\nValidation: OK")

    if args.dry_run:
        print("Dry run -- nothing written.")
        return 0

    payload = json.dumps(collection, indent=4, ensure_ascii=False)
    json.loads(payload)  # round-trip sanity check
    OUTPUT_COLLECTION.write_text(payload, encoding="utf-8")
    print(f"Wrote: {OUTPUT_COLLECTION}  ({len(payload):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
