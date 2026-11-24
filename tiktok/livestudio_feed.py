#!/usr/bin/env python3
"""Place the OBS->LIVE Studio video feed on one or both canvases.

Architecture (option A):
    OBS renders the canvas (camera + game + overlays already composited)
      -> publishes RTMP to the local relay (mediamtx)
      -> relay serves WebRTC (WHEP)
      -> shim proxies the player page + signalling over the tunnel
      -> LIVE Studio shows it as one Link source

So LIVE Studio needs no camera of its own and no game capture: one physical
device, owned by OBS, exactly as before.

LIVE Studio clamps every browser element to its own viewport (1080x1032), so the
player page fills that box (object-fit: fill) and this script scales the box back
out to the full canvas with a non-uniform scale - the two distortions cancel.

Usage:
    python livestudio_feed.py              # both canvases
    python livestudio_feed.py --track 1    # portrait only (TikTok vertical)
    python livestudio_feed.py --clean
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import lsclient as ls

S = ls.SERVICE
NAME = "DSH FEED"
PATHS = {0: "landscape", 1: "portrait"}
# names the sources carry once created (readable in the LIVE Studio source list)
FEED_NAMES = {0: "TikTok Landscape feed", 1: "TikTok Portrait feed"}


def run(body: str, t: float = 60.0):
    r = ls.run(body, t)
    return r.get("value") if isinstance(r, dict) else None


VIEWPORT_FILE = Path(__file__).resolve().parent / "viewport_state.json"


def reported_viewport(path: str) -> tuple[int, int] | None:
    """The page's real viewport inside LIVE Studio (reported by the page itself)."""
    try:
        d = json.loads(VIEWPORT_FILE.read_text(encoding="utf-8-sig"))
        entry = d.get(path) if isinstance(d, dict) and path in d else None
        if entry is None and isinstance(d, dict) and d.get("path") == path:
            entry = d
        if not entry:
            return None
        w, h = int(entry.get("w") or 0), int(entry.get("h") or 0)
        return (w, h) if w > 0 and h > 0 else None
    except Exception:  # noqa: BLE001
        return None


def preview_rects() -> dict[int, dict]:
    """Live preview element size per canvas, keyed by track."""
    expr = ("(() => { const out = [];"
            "for (const e of document.querySelectorAll('canvas')) {"
            "  const r = e.getBoundingClientRect();"
            "  if (r.width > 60 && r.height > 60) out.push({w: Math.round(r.width), h: Math.round(r.height)});"
            "} return out; })()")
    v = ls.eval_probe(expr).get("value")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = None
    rects = v if isinstance(v, list) else []
    portrait = next((r for r in rects if r["h"] > r["w"]), None)
    landscape = next((r for r in rects if r["w"] > r["h"]), None)
    out: dict[int, dict] = {}
    if landscape:
        out[0] = landscape
    if portrait:
        out[1] = portrait
    return out


def canvas_sizes() -> dict[int, tuple[int, int]]:
    v = run(f"const s = await {S}.streamSetting.getSettings();"
            f"return {{l: s.resolution, p: s.secondResolution}};") or {}
    return {0: tuple(v.get("l") or (1920, 1080)), 1: tuple(v.get("p") or (1080, 1920))}


def remove_named(name: str) -> int:
    r = run(f"""const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || [];
      const mine = s.filter(x => x.name === {json.dumps(name)});
      let n = 0;
      for (const m of mine) {{ try {{ await {S}.source.removeSource(m.id, m.track); n++; }} catch (e) {{}} }}
      return n;""")
    return int(r or 0)


def clean() -> int:
    r = run(f"""const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || [];
      const mine = s.filter(x => (x.name||'').indexOf({json.dumps(NAME)}) === 0);
      let n = 0;
      for (const m of mine) {{ try {{ await {S}.source.removeSource(m.id, m.track); n++; }} catch (e) {{}} }}
      return n;""")
    return int(r or 0)


def read_back(name: str) -> dict | None:
    v = run(f"""const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || [];
      const m = s.filter(x => x.name === {json.dumps(name)});
      return m.length ? m[0] : null;""")
    return v if isinstance(v, dict) else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", type=int, choices=[0, 1])
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--base", default=None, help="tunnel base url")
    ap.add_argument("--token", default=None)
    args = ap.parse_args(argv)

    try:
        ls.main_target()
    except Exception as exc:  # noqa: BLE001
        print(f"  no studio target: {exc}")
        return 2

    if args.clean:
        print(f"  removed {clean()} feed source(s)")
        return 0

    state: dict = {}
    try:
        from pathlib import Path
        state = json.loads(Path("tiktok-overlays.json").read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        pass
    base = args.base or state.get("url")
    token = args.token or state.get("token")
    if not base or not token:
        print("  need the tunnel state (tiktok-overlays.json) or --base/--token")
        return 2

    rects = preview_rects()
    canvas = canvas_sizes()
    tracks = [args.track] if args.track is not None else [0, 1]

    for track in tracks:
        pv = rects.get(track)
        if not pv:
            print(f"  track {track}: no preview element found, skipping")
            continue
        path = PATHS[track]
        url = f"{base.rstrip('/')}/o/{token}/feed?path={path}"
        name = f"{NAME} {path}"
        # replace either the legacy probe name or the readable name already there
        remove_named(name)
        remove_named(FEED_NAMES[track])
        desc = {"name": name, "type": "browser",
                "payload": {"url": url,
                            "cx": canvas[track][0], "cy": canvas[track][1],
                            # portrait carries the mixed OBS audio; landscape stays silent
                            "enableAudio": track == 1,
                            "enableCustomResolution": False, "zoom": 1},
                "track": track}
        added = run(f"const d={json.dumps(desc)};"
                    f"try{{const r=await {S}.source.addSource(d,{{recordHistory:false}});return r;}}"
                    f"catch(e){{return String(e&&e.message||e);}}")
        sid = (added or {}).get("id")
        if not sid or not (added or {}).get("result"):
            print(f"  track {track}: add failed: {json.dumps(added)[:140]}")
            continue
        # updateSourceName is what actually renames a source; updateSource({name})
        # fails with "reading 'url'"
        rename_to = FEED_NAMES[track]
        run(f"try{{ await {S}.source.updateSourceName({json.dumps(sid)}, {json.dumps(rename_to)});"
            f" return 1; }}catch(e){{ return String(e&&e.message||e); }}")
        cur = read_back(rename_to) or {}
        own = (cur.get("transform") or {}).get("size") or {}
        vp = reported_viewport(path)
        if vp:
            # the page can only paint inside its viewport; match the box to it and
            # let the non-uniform scale stretch that box over the whole canvas
            ow, oh = float(vp[0]), float(vp[1])
            print(f"    measured page viewport: {vp[0]}x{vp[1]}")
        else:
            ow = float(own.get("cx") or canvas[track][0])
            oh = float(own.get("cy") or canvas[track][1])
        # Cover a hair more than the canvas. Exact fitting left a 3px seam on
        # every edge (LIVE Studio rounds its internal preview), which the app
        # draws as a visible gap around the selected source.
        OVERS = 1.012
        sx = (pv["w"] * OVERS) / ow
        sy = (pv["h"] * OVERS) / oh
        obj = {"transform": {"translateX": -(pv["w"] * (OVERS - 1) / 2),
                             "translateY": -(pv["h"] * (OVERS - 1) / 2), "rotateAngle": 0,
                             "scaleX": sx, "scaleY": sy, "flipH": False, "flipV": False,
                             "size": {"cx": ow, "cy": oh},
                             "preview_cx": pv["w"], "preview_cy": pv["h"]},
               "visible": True, "clip": {"x": 0, "y": 0, "z": 0, "w": 0}}
        res = run(f"const id={json.dumps(sid)}; const o={json.dumps(obj)};"
                  f"try{{await {S}.source.updateSourceTransform(id,o,true,false);return 'ok';}}"
                  f"catch(e){{return String(e&&e.message||e);}}")
        print(f"  track {track} ({path}): box {ow:.0f}x{oh:.0f} "
              f"scaled ({sx:.4f},{sy:.4f}) -> {res}")
        print(f"    {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
