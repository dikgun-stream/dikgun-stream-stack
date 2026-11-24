#!/usr/bin/env python3
"""Point OBS's two canvases at the local relay, so LIVE Studio can show them.

Option A architecture: OBS stays the only compositor and the only owner of the
camera/game. It publishes both canvases to the local relay (mediamtx), and LIVE
Studio displays them as two WebRTC feeds - so TikTok gets exactly what OBS shows
without any second capture of the camera or the game.

This adds two publishing targets:
  * vertical canvas  -> rtmp://127.0.0.1:1935  key "portrait"
  * Aitum Multistream -> rtmp://127.0.0.1:1935  key "landscape"

OBS must be CLOSED when this runs: it rewrites its config files on exit and reads
them at startup, so an edit made while it runs is lost.

Idempotent: existing entries with the same names are replaced, not duplicated.
Backs up both configs first.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

APPDATA = os.environ["APPDATA"]
VC_CFG = os.path.join(APPDATA, "obs-studio", "plugin_config", "vertical-canvas", "config.json")
MS_CFG = os.path.join(APPDATA, "obs-studio", "plugin_config", "aitum-multistream", "config.json")

RELAY_SERVER = "rtmp://127.0.0.1:1935"
PORTRAIT_KEY = "portrait"
LANDSCAPE_KEY = "landscape"
PORTRAIT_NAME = "Relay Portrait"
LANDSCAPE_NAME = "Relay Landscape"


def obs_running() -> bool:
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe"],
                             capture_output=True, text=True, timeout=10).stdout
        return "obs64.exe" in out.lower()
    except Exception:  # noqa: BLE001
        return False


def backup(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    dst = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, dst)
    return dst


def add_vertical() -> str:
    c = json.load(open(VC_CFG, encoding="utf-8"))
    canvases = c.get("canvas") or []
    if not canvases:
        return "vertical canvas config has no canvas entry"
    cv = canvases[0]
    outs = cv.setdefault("stream_outputs", [])
    template = dict(outs[0]) if outs else {}
    entry = {
        "name": PORTRAIT_NAME,
        "stream_server": RELAY_SERVER,
        "stream_key": PORTRAIT_KEY,
        "enabled": True,
        "scale_type": template.get("scale_type", 0),
        "audio_encoder": template.get("audio_encoder", "ffmpeg_aac"),
        "audio_encoder_settings": template.get("audio_encoder_settings", {"bitrate": 128}),
    }
    outs[:] = [o for o in outs if o.get("name") != PORTRAIT_NAME] + [entry]
    json.dump(c, open(VC_CFG, "w", encoding="utf-8"), indent=4)
    return f"vertical canvas: {len(outs)} output(s) - added '{PORTRAIT_NAME}' -> {RELAY_SERVER}/{PORTRAIT_KEY}"


def add_landscape() -> str:
    if not os.path.isfile(MS_CFG):
        return "no aitum multistream config found (landscape needs another route)"
    c = json.load(open(MS_CFG, encoding="utf-8"))
    profiles = c.get("profiles") or []
    if not profiles:
        return "multistream has no profiles"
    changed = []
    for prof in profiles:
        outs = prof.setdefault("outputs", [])
        template = dict(outs[0]) if outs else {}
        entry = {
            "name": LANDSCAPE_NAME,
            "stream_server": RELAY_SERVER,
            "stream_key": LANDSCAPE_KEY,
            "scale_type": template.get("scale_type", 3),
            "audio_encoder": template.get("audio_encoder", "ffmpeg_aac"),
            "audio_encoder_settings": template.get("audio_encoder_settings", {"bitrate": 128}),
            "expanded": False,
        }
        outs[:] = [o for o in outs if o.get("name") != LANDSCAPE_NAME] + [entry]
        changed.append(f"{prof.get('name')}: {len(outs)} output(s)")
    json.dump(c, open(MS_CFG, "w", encoding="utf-8"), indent=4)
    return f"multistream: added '{LANDSCAPE_NAME}' -> {RELAY_SERVER}/{LANDSCAPE_KEY}  ({', '.join(changed)})"


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    force = "--force" in argv
    if obs_running() and not force:
        print("  OBS is running - close it first (it rewrites these files on exit)")
        print("  re-run with --force only if you accept the edit may be overwritten")
        return 2

    for f in (VC_CFG, MS_CFG):
        b = backup(f)
        if b:
            print(f"  backed up {os.path.basename(f)} -> {os.path.basename(b)}")

    print("  " + add_vertical())
    print("  " + add_landscape())

    # verify the files still parse
    for f in (VC_CFG, MS_CFG):
        try:
            json.load(open(f, encoding="utf-8"))
            print(f"  ok: {os.path.basename(f)} parses")
        except Exception as exc:  # noqa: BLE001
            print(f"  BROKEN: {f}: {exc}")
            return 1
    print("\n  next: open OBS and start streaming as usual. The relay targets publish")
    print("  alongside Twitch/YouTube; mediamtx turns them into portrait/landscape feeds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
