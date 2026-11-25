#!/usr/bin/env python3
"""Rebuild the LIVE Studio scene as a standalone clone of OBS (one command).

This is the "you manage TikTok yourself" path: instead of the relay feed, LIVE
Studio gets real sources cloned from OBS - game capture bound to the running
game, and the overlay pages as Link sources through the tunnel. You press Go LIVE
in LIVE Studio yourself and own its audio settings.

Camera is NOT cloned by default: the webcam is single-access and OBS holds it
while streaming, so grabbing it here would black out OBS's camera. Pass
--with-camera when OBS is NOT streaming (or add the camera by hand in LIVE
Studio later).

Usage (with LIVE Studio running via launch-debug.ps1 and the game running):
    python livestudio_rebuild.py --game apex
    python livestudio_rebuild.py --game deadlock
    python livestudio_rebuild.py --game tarkov
"""

from __future__ import annotations

import argparse
import subprocess
import sys

GAME_SCENES = {
    "apex": ("GAME_Apex", "V_GAME_Apex"),
    "deadlock": ("GAME_Deadlock", "V_GAME_Deadlock"),
    "tarkov": ("GAME_Tarkov", "V_GAME_Tarkov"),
}


def run(cmd: list[str]) -> int:
    print(f"\n  $ {' '.join(cmd)}")
    return subprocess.call([sys.executable, *cmd])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild LIVE Studio as an OBS clone")
    ap.add_argument("--game", choices=sorted(GAME_SCENES), default="apex")
    ap.add_argument("--with-camera", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="show the plan without creating")
    args = ap.parse_args(argv)

    landscape, portrait = GAME_SCENES[args.game]
    extra = [] if not args.with_camera else []
    if args.with_camera:
        extra.append("--with-camera")

    # LIVE Studio starts scene-less after a restart; addSource needs a scene.
    # createScene(name, orientation='dual', select=true) creates + activates it
    # (the select flag is the second argument - without it the scene is created
    # but never becomes current). This is done ONCE here, not per mirror run.
    import time
    import lsclient as _ls
    _S = _ls.SERVICE
    made = _ls.run(f"try{{ const r = await {_S}.source.createScene("
                   f"{{name: 'TikTok {args.game.title()}', orientation: 'dual'}}, true);"
                   f" return 'ok ' + JSON.stringify(r); }}catch(e){{ return 'ERR ' + e.message; }}").get("value")
    print(f"\n  scene: create+activate -> {made}")
    time.sleep(3)

    rc = run(["livestudio_mirror.py", "--clean"])
    if rc:
        return rc
    rc = run(["livestudio_mirror.py", "--scene", landscape, "--apply", *extra])
    if rc:
        return rc
    rc = run(["livestudio_mirror.py", "--scene", portrait, "--apply", *extra])
    if rc:
        return rc

    print("\n  done. LIVE Studio now has:")
    print(f"    landscape canvas: {landscape}  (game + overlays)")
    print(f"    portrait canvas : {portrait}  (game + overlays)")
    print("  You manage the rest in LIVE Studio itself:")
    print("    - audio (mic / volume) in its Go Live panel")
    print("    - camera: add by hand, or re-run with --with-camera when OBS is not streaming")
    print("    - Go LIVE: its own button")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
