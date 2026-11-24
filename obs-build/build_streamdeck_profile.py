#!/usr/bin/env python3
"""
Regenerate the DikGun StreamDock profile as TWO pages.

Page 1 - scenes + OBS output toggles (unchanged), plus a page-change key.
Page 2 - admin panel: go live, panic, mic, hint jar, dashboard/chat windows,
          streamer mode / dev mode, restore.

StreamDock's page model (learned from the existing profiles):
    <parent>.sdProfile/manifest.json          -> page 1 actions + Pages list
    <parent>.sdProfile/profiles/<p2>.sdProfile/manifest.json -> page 2 actions
    Pages.Current  = parent id
    Pages.Pages    = [parent, p2, ...]
    "page.change" cycles to the next page; "page.indicator" shows position.

The profile GUID is fixed so the entry already registered in DataCache.db stays
valid. Close Stream Dock AJAZZ before running, or it will overwrite the files.
"""

from __future__ import annotations

import json
import os
import random
import string
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(os.environ["APPDATA"]) / "HotSpot" / "StreamDock" / "profiles"
PROFILE_ID = "MRX4M90L-GH25-1C1J-QU4E-HBQZCSII3GQA"
PROFILE_NAME = "DikGun Stream"
DEVICE_MODEL = "20GBA9901"
DEVICE_UUID = "AKP153E"
DEVICE_SERIAL = "355499441494"

KEY = 128
BG = (18, 22, 30)
FG = (235, 242, 250)

HERE = Path(__file__).resolve().parent
DECK = str(HERE.parent / "streamer-mode" / "deck")
SM = str(HERE.parent / "streamer-mode")

GREEN = (74, 222, 128)
BLUE = (56, 189, 248)
RED = (248, 113, 113)
AMBER = (251, 191, 36)
VIOLET = (167, 139, 250)
PINK = (244, 114, 182)

VK_F13 = 124


def rid(alphabet: str, parts: list[int]) -> str:
    return "-".join("".join(random.choice(alphabet) for _ in range(n)) for n in parts)


def profile_guid() -> str:
    return rid(string.ascii_uppercase + string.digits, [8, 4, 4, 4, 12])


def action_id() -> str:
    return rid(string.ascii_lowercase + string.digits, [8, 4, 4, 4, 12])


def icon(path: Path, label: str, sub: str, colour) -> None:
    img = Image.new("RGB", (KEY, KEY), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3, 3, KEY - 4, KEY - 4], radius=16, outline=colour, width=3)
    d.rectangle([3, 3, KEY - 4, 10], fill=colour)
    try:
        big = ImageFont.truetype("arialbd.ttf", 24)
        small = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        big = small = ImageFont.load_default()

    lines = label.split("\n")
    y = KEY // 2 - (len(lines) * 27) // 2 - (7 if sub else 0)
    for line in lines:
        w = d.textlength(line, font=big)
        d.text(((KEY - w) / 2, y), line, font=big, fill=FG)
        y += 27
    if sub:
        w = d.textlength(sub, font=small)
        d.text(((KEY - w) / 2, y + 2), sub, font=small, fill=colour)
    img.save(path, "PNG")


def hotkey_action(vk: int) -> dict:
    return {"Coalesce": True, "Hotkeys": [{
        "KeyCmd": False, "KeyCtrl": False, "KeyModifiers": 65536, "KeyOption": False,
        "KeyShift": False, "NativeCode": -1, "QTKeyCode": -1,
        "RKeyCmd": False, "RKeyCtrl": False, "RKeyOption": False, "RKeyShift": False,
        "VKeyCode": vk}]}


def act(name: str, uuid_str: str, image: str, settings: dict, states: int = 1) -> dict:
    return {
        "ActionID": action_id(), "Controller": "", "Name": name, "Settings": settings,
        "State": 0,
        "States": [{"FontFamily": "Arial", "FontSize": 10, "Image": image,
                    "Title": "", "TitleAlignment": "middle", "TitleColor": "#ffffff"}
                   for _ in range(states)],
        "UUID": uuid_str,
    }


SCENES = ["GAME_Tarkov", "GAME_Deadlock", "GAME_Apex", "CODE_Live", "JC_Talking",
          "SCREEN_Work", "SHOW_StartingSoon", "SHOW_BRB", "SHOW_Ending"]

# Explicit labels: deriving from the scene name made CODE_Live collide with the
# stream-toggle "LIVE" key.
SCENE_LABELS = {
    "GAME_Tarkov": "TARKOV",
    "GAME_Deadlock": "DEADLOCK",
    "GAME_Apex": "APEX",
    "CODE_Live": "CODE",
    "JC_Talking": "TALKING",
    "SCREEN_Work": "SCREEN",
    "SHOW_StartingSoon": "STARTING",
    "SHOW_BRB": "BRB",
    "SHOW_Ending": "ENDING",
}

# page 2: (row, col) -> (label, sublabel, colour, script)
ADMIN = [
    ("GO\nLIVE",     "all+tt",   RED,    rf"{DECK}\go-live.bat"),
    ("STOP",         "live",     RED,    rf"{DECK}\stop-live.bat"),
    ("PANIC",        "mic+brb",  RED,    rf"{DECK}\panic.bat"),
    ("MIC",          "toggle",   AMBER,  rf"{DECK}\mic-toggle.bat"),
    ("HINT",         "reveal",   GREEN,  rf"{DECK}\hint.bat"),
    ("DASH",         "open",     BLUE,   rf"{DECK}\dashboard.bat"),
    ("CHAT",         "open",     BLUE,   rf"{DECK}\chat.bat"),
    ("STREAM\nMODE", "clean",    VIOLET, rf"{DECK}\stream-mode.bat"),
    ("DEV\nMODE",    "tiled",    VIOLET, rf"{DECK}\dev-mode.bat"),
    ("RESTORE",      "desktop",  PINK,   rf"{DECK}\restore.bat"),
    # TikTok delivery: PREP brings up the relay + tunnel + LIVE Studio feeds
    # without going live; TIKTOK arms the pre-live panel; TIKTOK GO broadcasts.
    ("PREP",         "pipeline", VIOLET, rf"{DECK}\prep.bat"),
    ("TIKTOK",       "arm",      BLUE,   rf"{DECK}\tiktok.bat"),
    ("TIKTOK\nGO",   "broadcast", RED,   rf"{DECK}\tiktok-go.bat"),
    ("TIKTOK\nSTOP", "end",      RED,    rf"{DECK}\tiktok-stop.bat"),
]


def build_page1(images: Path, page_id: str, page2_id: str) -> dict:
    actions = {}
    idx = 0

    def place(img, label, sub, colour, uuid_str, settings, states=1, key=None):
        nonlocal idx
        if key is None:
            r, c = divmod(idx, 3)
            key = f"{r},{c}"
            idx += 1
        icon(images / img, label, sub, colour)
        actions[key] = act(label.replace("\n", " "), uuid_str, img, settings, states)

    for i, scene in enumerate(SCENES):
        label = SCENE_LABELS[scene]
        place(f"scene_{i}.png", label, "scene",
              GREEN, "com.hotspot.streamdock.system.hotkey", hotkey_action(VK_F13 + i))

    place("obs_stream.png", "LIVE", "toggle", RED, "com.gabilan.obs.studio.action3", {}, 2)
    place("obs_record.png", "REC", "toggle", RED, "com.gabilan.obs.studio.action1", {}, 2)
    place("obs_replay.png", "CLIP", "replay", AMBER, "com.gabilan.obs.studio.action26", {}, 2)
    place("obs_vcam.png", "VCAM", "toggle", BLUE, "com.gabilan.obs.studio.action17", {}, 2)
    place("obs_next.png", "NEXT", "scene", BLUE, "com.gabilan.obs.studio.action22", {}, 2)
    place("obs_prev.png", "PREV", "scene", BLUE, "com.gabilan.obs.studio.action23", {}, 2)
    place("obs_stats.png", "STATS", "obs", VIOLET, "com.gabilan.obs.studio.action34", {}, 2)

    # page-change to the admin page
    place("page_next.png", "ADMIN", "page 2", AMBER,
          "com.hotspot.streamdock.page.change", {}, key="5,1")

    return {
        "Actions": actions,
        "AppIdentifier": "", "AppIdentifier0": "", "AppIdentifier1": "", "AppIdentifier2": "",
        "DeviceModel": DEVICE_MODEL, "DeviceSerialNumber": DEVICE_SERIAL, "DeviceUUID": DEVICE_UUID,
        "Name": PROFILE_NAME,
        "Pages": {"Current": page_id, "Pages": [page_id, page2_id]},
        "Version": "1.0",
    }


def build_page2(images: Path) -> dict:
    actions = {}
    for i, (label, sub, colour, script) in enumerate(ADMIN):
        r, c = divmod(i, 3)
        img = f"admin_{i}.png"
        icon(images / img, label, sub, colour)
        actions[f"{r},{c}"] = act(label.replace("\n", " "),
                                  "com.hotspot.streamdock.system.open",
                                  img, {"path": script})

    icon(images / "page_ind.png", "2/2", "", BLUE)
    actions["5,0"] = act("2/2", "com.hotspot.streamdock.page.indicator", "page_ind.png", {})

    icon(images / "page_prev.png", "BACK", "page 1", AMBER)
    actions["5,1"] = act("BACK", "com.hotspot.streamdock.page.change", "page_prev.png", {})

    return {
        "Actions": actions,
        "DeviceModel": DEVICE_MODEL, "DeviceSerialNumber": DEVICE_SERIAL, "DeviceUUID": DEVICE_UUID,
        "Name": f"{PROFILE_NAME} Admin",
        "Version": "1.0",
    }


def main() -> int:
    parent = ROOT / f"{PROFILE_ID}.sdProfile"
    if not parent.parent.exists():
        print(f"ERROR: profiles dir missing: {ROOT}")
        return 1

    page2_id = profile_guid()
    page1_images = parent / "Images"
    page2_dir = parent / "profiles" / f"{page2_id}.sdProfile"
    page2_images = page2_dir / "Images"
    page1_images.mkdir(parents=True, exist_ok=True)
    page2_images.mkdir(parents=True, exist_ok=True)

    p1 = build_page1(page1_images, f"{PROFILE_ID}.sdProfile", f"{page2_id}.sdProfile")
    (parent / "manifest.json").write_text(json.dumps(p1, indent=2, ensure_ascii=False), encoding="utf-8")

    p2 = build_page2(page2_images)
    (page2_dir / "manifest.json").write_text(json.dumps(p2, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"page 1 : {len(p1['Actions'])} keys -> {parent}")
    print(f"page 2 : {len(p2['Actions'])} keys -> {page2_dir}")
    print()
    print("page 1:")
    for k in sorted(p1["Actions"], key=lambda s: (int(s.split(',')[0]), int(s.split(',')[1]))):
        print(f"   {k:5} {p1['Actions'][k]['Name']}")
    print("page 2:")
    for k in sorted(p2["Actions"], key=lambda s: (int(s.split(',')[0]), int(s.split(',')[1]))):
        print(f"   {k:5} {p2['Actions'][k]['Name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
