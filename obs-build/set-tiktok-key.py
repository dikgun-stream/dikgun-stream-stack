#!/usr/bin/env python3
"""Put the current TikTok live key into the Aitum Vertical canvas.

Why this exists
---------------
TikTok does not give a permanent stream key: you fetch one per session from the
TikTok LIVE Center (or a key generator), and it is dead as soon as the session
ends. Retyping it into the Aitum dock every time is the friction this removes.

Where the key goes, and why there
---------------------------------
`%APPDATA%\\obs-studio\\plugin_config\\vertical-canvas\\config.json` holds the
vertical canvas, and its `stream_outputs` array can carry SEVERAL destinations.
It already has "Youtube Vertical". Adding "TikTok Vertical" there means the same
`VerticalCanvasDockStartStreaming` hotkey that `/api/live` already triggers will
start the TikTok output too - so TikTok joins the one-button go-live instead of
being a separate manual click in Aitum Multistream.

The canvas is 1080x1920, which is exactly what TikTok ingests, so no scaling
trickery is needed; only the bitrate is raised.

OBS must be CLOSED
------------------
The plugin reads this file when it loads. Editing it while OBS runs is ignored
and then overwritten on exit, the same trap as the scene collection. The script
refuses to run if obs64 is up.

Examples
--------
    # normal session
    python set-tiktok-key.py --key "abc123..."

    # non-default ingest (use whatever the generator gave you)
    python set-tiktok-key.py --server "rtmp://push.example.com/live" --key "abc..."

    # remove it again (e.g. TikTok rights lapsed)
    python set-tiktok-key.py --remove
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# TikTok's usual ingest. The key generators usually print their own server URL -
# pass --server when it differs, because regional ingests do exist.
DEFAULT_SERVER = "rtmp://push.tiktokv.com/live"
OUTPUT_NAME = "TikTok Vertical"
# Aitum's published TikTok guidance: H264, 1080x1920, 60fps, CBR 8000kbps.
# The canvas bitrate applies to every output on it, so this is a compromise
# between YouTube-vertical (4000 was fine) and TikTok (wants more).
DEFAULT_BITRATE = 6000


def config_path() -> Path:
    return (Path(os.environ["APPDATA"]) / "obs-studio" / "plugin_config"
            / "vertical-canvas" / "config.json")


def obs_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return False
    return "obs64.exe" in out.lower()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write the current TikTok live key into the Aitum Vertical canvas")
    ap.add_argument("--key", default="", help="TikTok stream key for this session")
    ap.add_argument("--server", default=DEFAULT_SERVER,
                    help=f"TikTok ingest URL (default {DEFAULT_SERVER})")
    ap.add_argument("--bitrate", type=int, default=DEFAULT_BITRATE,
                    help="vertical canvas streaming bitrate in kbps")
    ap.add_argument("--name", default=OUTPUT_NAME, help="output entry name")
    ap.add_argument("--remove", action="store_true",
                    help="remove the TikTok output instead of setting it")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not args.remove and not args.key.strip():
        print("ERROR: --key is required (or pass --remove)", file=sys.stderr)
        return 1

    p = config_path()
    if not p.is_file():
        print(f"ERROR: {p} not found - is Aitum Vertical installed?", file=sys.stderr)
        return 1

    if obs_running():
        print("ERROR: OBS is running. Close it first - the plugin reads this file "
              "at startup and will overwrite your change on exit.", file=sys.stderr)
        return 2

    data = json.loads(p.read_text(encoding="utf-8"))
    canvases = data.get("canvas") or []
    if not canvases:
        print("ERROR: no canvas in the Aitum config", file=sys.stderr)
        return 1
    canvas = canvases[0]

    outputs = canvas.setdefault("stream_outputs", [])
    existing = next((o for o in outputs if o.get("name") == args.name), None)

    if args.remove:
        if not existing:
            print(f"  no '{args.name}' output present - nothing to remove")
            return 0
        outputs.remove(existing)
    else:
        if existing is None:
            # copy the shape of a working output so nothing the plugin reads is missing
            proto = outputs[0] if outputs else {}
            existing = json.loads(json.dumps(proto)) if proto else {}
            existing["name"] = args.name
            existing.setdefault("scale_type", 3)
            existing.setdefault("audio_encoder", "ffmpeg_aac")
            existing.setdefault("audio_encoder_settings", {"bitrate": 128})
            outputs.append(existing)
            print(f"  added output '{args.name}'")
        else:
            print(f"  updating output '{args.name}'")
        existing["enabled"] = True
        existing["stream_server"] = args.server
        existing["stream_key"] = args.key.strip()

    old_bitrate = canvas.get("streaming_video_bitrate")
    if not args.remove:
        canvas["streaming_video_bitrate"] = int(args.bitrate)

    print(f"  canvas            : {canvas.get('width')}x{canvas.get('height')}")
    print(f"  outputs           : {[o.get('name') for o in outputs]}")
    if not args.remove:
        print(f"  tiktok server     : {args.server}")
        print(f"  tiktok key        : {args.key[:6]}...{args.key[-4:]}")
        print(f"  canvas bitrate    : {old_bitrate} -> {args.bitrate} kbps "
              f"(applies to every output on this canvas)")

    if args.dry_run:
        print("  dry run - nothing written")
        return 0

    bak = p.with_name(p.name + f".bak-tiktok-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(p, bak)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    print(f"  written: {p}")
    print(f"  backup : {bak.name}")
    print()
    print("  Next: open OBS, then /api/live (or the deck's LIVE key) starts Twitch +")
    print("  the vertical canvas outputs (YouTube vertical AND TikTok) together.")
    print("  TikTok keys die every session - re-run this before each stream.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
