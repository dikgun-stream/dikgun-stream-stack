#!/usr/bin/env python3
"""Manage our overlay pages as Link sources inside TikTok LIVE Studio.

Background
----------
LIVE Studio's "Add link" source rejects http://127.0.0.1 (verified: it reports
"Enter correct URL"), so overlays are served through the read-only public shim
and a tunnel, e.g.

    https://<random>.trycloudflare.com/o/<token>/chat

This tool drives the app's own service layer over CDP (see lsclient.py) to
create / repoint / remove those Link sources on both canvases:

    track 0 = Landscape  1920x1080
    track 1 = Portrait   1080x1920

Because cloudflared quick tunnels get a NEW random hostname on every run, the
normal session flow is:

    python livestudio_sync.py --base https://new-host.trycloudflare.com --token abc
    python livestudio_sync.py --update-url https://new-host.trycloudflare.com --token abc

`--update-url` repoints every existing Link source without re-creating anything,
which is what makes ephemeral tunnels practical.

The real source type is "browser" (not "link"), payload shape verified from a
source the UI created:

    {url, cx: 800, cy: 600, enableAudio: true,
     enableCustomResolution: false, zoom: 3.8017840169239308}
"""

from __future__ import annotations

import argparse
import json
import sys

import lsclient as ls

S = ls.SERVICE
PAGES = ["chat", "alerts", "topic", "goals", "input"]
TRACKS = {0: "landscape", 1: "portrait"}
# exactly what the UI produced for a fresh Link source
DEFAULT_PAYLOAD = {
    "cx": 800, "cy": 600,
    "enableAudio": True, "enableCustomResolution": False,
    "zoom": 3.8017840169239308,
}


def _snapshot() -> list[dict]:
    r = ls.run(f"const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || [];"
               f"return s;")
    v = r.get("value")
    return v if isinstance(v, list) else []


def link_sources() -> list[dict]:
    return [s for s in _snapshot() if (s.get("type") == "browser")]


def url_of(src: dict) -> str:
    return str((src.get("payload") or {}).get("url") or "")


def base_url(base: str, token: str, page: str) -> str:
    base = base.rstrip("/")
    return f"{base}/o/{token}/{page}"


# --------------------------------------------------------------------- list
def cmd_list(_args) -> int:
    srcs = link_sources()
    if not srcs:
        print("  no Link (browser) sources in the current scene")
        return 0
    print(f"  {len(srcs)} Link source(s):")
    for s in srcs:
        track = s.get("track")
        name = TRACKS.get(track, f"track {track}")
        print(f"    {str(s.get('id'))[:8]}  {name:9}  visible={s.get('visible')}  {url_of(s)}")
    return 0


# ---------------------------------------------------------------------- add
def cmd_add(args) -> int:
    pages = [p.strip() for p in args.pages.split(",") if p.strip()]
    tracks = [int(t) for t in args.tracks.split(",") if t.strip()]
    existing = {(url_of(s), s.get("track")) for s in link_sources()}

    made = 0
    for track in tracks:
        for page in pages:
            url = base_url(args.base, args.token, page)
            if (url, track) in existing:
                print(f"  skip  {TRACKS.get(track,track):9} {page:6} (already present)")
                continue
            payload = dict(DEFAULT_PAYLOAD, url=url)
            descriptor = {
                "name": f"Link {page}",
                "type": "browser",
                "payload": payload,
                "track": track,
            }
            body = (f"const d = {json.dumps(descriptor)};"
                    f"try {{ const r = await {S}.source.addSource(d, {{recordHistory: true}});"
                    f" return {{ok: !!(r && r.result), raw: r}}; }}"
                    f"catch (e) {{ return {{ok:false, error: String(e&&e.message||e)}}; }}")
            r = ls.run(body)
            v = r.get("value") if isinstance(r, dict) else None
            ok = bool(isinstance(v, dict) and v.get("ok"))
            print(f"  {'add ' if ok else 'FAIL'}  {TRACKS.get(track,track):9} {page:6} -> {json.dumps(v)[:150]}")
            made += 1 if ok else 0
    print(f"\n  created {made} source(s)")
    return 0 if made or not pages else 1


# --------------------------------------------------------------- update-url
def cmd_update_url(args) -> int:
    """Repoint existing Link sources at a new tunnel host (ephemeral tunnels)."""
    srcs = link_sources()
    if not srcs:
        print("  no Link sources to update")
        return 1
    new_base = args.update_url.rstrip("/")
    changed = 0
    for s in srcs:
        old = url_of(s)
        if not old:
            continue
        # keep the same page path (/o/<token>/<page>), swap host+token
        try:
            tail = old.split("/o/", 1)[1]
            page = tail.rsplit("/", 1)[1]
        except IndexError:
            print(f"  skip {old} (unexpected shape)")
            continue
        url = base_url(new_base, args.token, page)
        if url == old:
            print(f"  ok    {page:6} already {url}")
            continue
        body = (f"const id = {json.dumps(s['id'])}; const url = {json.dumps(url)};"
                f"try {{ const r = await {S}.source.updateSource(id, {{payload: {{url}}}}, {{}});"
                f" return {{ok: true, raw: r}}; }}"
                f"catch (e) {{ return {{ok:false, error: String(e&&e.message||e)}}; }}")
        r = ls.run(body)
        v = r.get("value") if isinstance(r, dict) else None
        ok = bool(isinstance(v, dict) and v.get("ok"))
        print(f"  {'upd ' if ok else 'FAIL'} {page:6} -> {url}  {json.dumps(v)[:120]}")
        changed += 1 if ok else 0
    print(f"\n  updated {changed}/{len(srcs)}")
    return 0


# ------------------------------------------------------------------- remove
def cmd_remove(args) -> int:
    srcs = link_sources()
    if args.page:
        srcs = [s for s in srcs if args.page in url_of(s)]
    if not srcs:
        print("  nothing to remove")
        return 0
    removed = 0
    for s in srcs:
        sid, track = s["id"], s.get("track")
        attempts = [
            f"return await {S}.source.removeSource({json.dumps(sid)}, {track})",
            f"return await {S}.source.removeSource({json.dumps(sid)})",
            f"return await {S}.source.removeSource({{id:{json.dumps(sid)}, track:{track}}})",
        ]
        before = len(link_sources())
        for body in attempts:
            ls.run(body)
            if len(link_sources()) < before:
                removed += 1
                print(f"  removed {str(sid)[:8]} ({TRACKS.get(track, track)})")
                break
    print(f"\n  removed {removed}/{len(srcs)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LIVE Studio overlay Link sources")
    ap.add_argument("--base", default="", help="tunnel base, e.g. https://host.trycloudflare.com")
    ap.add_argument("--token", default="", help="shim token (path segment)")
    ap.add_argument("--pages", default=",".join(PAGES), help=f"pages to add (default {','.join(PAGES)})")
    ap.add_argument("--tracks", default="0,1", help="canvas tracks: 0=landscape 1=portrait")
    ap.add_argument("--list", action="store_true", help="list Link sources")
    ap.add_argument("--add", action="store_true", help="add missing Link sources")
    ap.add_argument("--update-url", default="", help="repoint existing sources at this tunnel base")
    ap.add_argument("--remove", action="store_true", help="remove Link sources")
    ap.add_argument("--page", default="", help="with --remove: only sources whose URL contains this")
    args = ap.parse_args(argv)

    try:
        ls.main_target()
    except Exception as exc:  # noqa: BLE001
        print(f"  LIVE Studio not reachable over CDP: {exc}")
        print("  run launch-debug.ps1 first")
        return 2

    if args.list or not any([args.add, args.update_url, args.remove]):
        return cmd_list(args)
    if args.add:
        if not (args.base and args.token):
            print("  --add needs --base and --token")
            return 2
        return cmd_add(args)
    if args.update_url:
        if not args.token:
            print("  --update-url needs --token")
            return 2
        return cmd_update_url(args)
    if args.remove:
        return cmd_remove(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
