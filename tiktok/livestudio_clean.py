#!/usr/bin/env python3
"""Tidy LIVE Studio down to the relay design.

In the homogenised workflow LIVE Studio only needs TWO sources, one per canvas:
the WebRTC feeds of OBS's rendered canvases. Camera, game capture and every
overlay are composited by OBS and arrive inside those feeds, so any other source
in TL is either a leftover from the abandoned per-source mirroring or a test
probe.

What this does:
  * keeps exactly one feed per track and renames it to something readable
  * removes every other source this project created (DSH ... probes, mirrored
    camera/game/inset sources, and the per-overlay web sources pointing at
    /o/<token>/... on the tunnel)
  * leaves sources it does not recognise alone, and lists them

Usage:
    python livestudio_clean.py            # show what would change
    python livestudio_clean.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys

import lsclient as ls

S = ls.SERVICE
FEED_MARK = "DSH FEED"
FEED_NAMES = {0: "TikTok Landscape feed", 1: "TikTok Portrait feed"}
TUNNEL_HINT = "/o/"          # overlay shim pages: /o/<token>/<page>


def run(body: str, t: float = 60.0):
    r = ls.run(body, t)
    return r.get("value") if isinstance(r, dict) else None


def snapshot() -> list[dict]:
    v = run(f"const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || []; return s;")
    return v if isinstance(v, list) else []


def classify(src: dict) -> str:
    name = str(src.get("name") or "")
    url = str((src.get("payload") or {}).get("url") or "")
    if name.startswith(FEED_MARK) or name in FEED_NAMES.values():
        return "feed"
    if name.startswith("DSH "):
        return "probe"
    if src.get("type") == "browser" and TUNNEL_HINT in url:
        return "overlay-link"
    return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    try:
        ls.main_target()
    except Exception as exc:  # noqa: BLE001
        print(f"  LIVE Studio not reachable over CDP: {exc}")
        return 2

    items = snapshot()
    groups: dict[str, list[dict]] = {"feed": [], "probe": [], "overlay-link": [], "unknown": []}
    for it in items:
        groups[classify(it)].append(it)

    print(f"  sources in the current scene: {len(items)}")
    for kind in ("feed", "probe", "overlay-link", "unknown"):
        if not groups[kind]:
            continue
        print(f"\n  {kind} ({len(groups[kind])}):")
        for it in groups[kind]:
            url = str((it.get("payload") or {}).get("url") or "")
            tail = url.rsplit("/", 1)[-1][:28] if url else ""
            print(f"    track {it.get('track')}  {str(it.get('type')):8} {str(it.get('name'))[:34]:36} {tail}")

    # one feed per track wins; extra feeds on the same track are stale
    keep: dict[int, dict] = {}
    for it in groups["feed"]:
        track = int(it.get("track") or 0)
        keep.setdefault(track, it)

    doomed = []
    for it in groups["probe"] + groups["overlay-link"]:
        doomed.append(it)
    for it in groups["feed"]:
        if keep.get(int(it.get("track") or 0)) is not it:
            doomed.append(it)

    print(f"\n  plan: keep {len(keep)} feed(s), remove {len(doomed)} source(s), "
          f"leave {len(groups['unknown'])} unrecognised alone")
    for track, it in sorted(keep.items()):
        print(f"    keep track {track}: {it.get('name')} -> '{FEED_NAMES.get(track, it.get('name'))}'")

    if not args.apply:
        print("\n  dry run - pass --apply to make the changes")
        return 0

    removed = 0
    for it in doomed:
        r = run(f"try{{ await {S}.source.removeSource({json.dumps(it['id'])}, {int(it.get('track') or 0)});"
                f" return 1; }}catch(e){{ return String(e&&e.message||e); }}")
        if r == 1:
            removed += 1
        else:
            print(f"    could not remove {it.get('name')}: {str(r)[:90]}")
    print(f"  removed {removed}/{len(doomed)}")

    for track, it in sorted(keep.items()):
        want = FEED_NAMES.get(track)
        if not want or it.get("name") == want:
            continue
        # updateSourceName is the call that renames; updateSource({name}) fails
        run(f"try{{ await {S}.source.updateSourceName({json.dumps(it['id'])}, {json.dumps(want)});"
            f" return 1; }}catch(e){{ return String(e&&e.message||e); }}")
        now = run(f"const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || [];"
                  f"const m = s.filter(x => x.id === {json.dumps(it['id'])});"
                  f"return m.length ? m[0].name : null;")
        print(f"  track {track} renamed -> {now!r} {'ok' if now == want else 'FAILED'}")

    after = snapshot()
    print(f"\n  sources now: {len(after)}")
    for it in after:
        print(f"    track {it.get('track')}  {str(it.get('type')):8} {it.get('name')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
