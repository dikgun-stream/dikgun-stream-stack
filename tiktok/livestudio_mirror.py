#!/usr/bin/env python3
"""Mirror OBS scenes into TikTok LIVE Studio.

Reads the OBS scene collection, and for one scene recreates it inside LIVE Studio:
game capture, camera, and the overlay pages as Link sources, each placed with the
geometry measured during calibration.

Mapping (measured, see README):
    transform.size      = the item's OBS bounds (w, h)
    transform.scaleX/Y  = 1                       -> displayed size == bounds
    transform.translate = the item's OBS pos (x, y)
    transform.preview_cx/cy = the canvas size     -> values are canvas pixels
    clip {x,y,z,w}      = OBS crop (left, top, right, bottom)

Source kinds:
    game_capture   -> TL "game"    (auto-binds to the running game)
    dshow_input    -> TL "camera"
    browser_source -> TL "browser" (Link) pointing at the shim over the tunnel,
                      with the OBS query string preserved (?game=tarkov, ?bottom=470)

Usage:
    python livestudio_mirror.py --scene V_GAME_Tarkov                 # dry run
    python livestudio_mirror.py --scene V_GAME_Tarkov --apply
    python livestudio_mirror.py --clean                              # remove ours
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

import lsclient as ls

S = ls.SERVICE
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "mirror_manifest.json"
OBS_COLLECTION = Path(os.environ["APPDATA"]) / "obs-studio" / "basic" / "scenes" / "DikGun.json"
AITUM_CANVAS = "c70dca39-1325-4243-9e11-14d60cb38f29"
NAME_PREFIX = "DSH "          # marks everything this tool created

KIND_TO_TL = {"game_capture": "game", "dshow_input": "camera", "browser_source": "browser"}


def state() -> dict:
    p = HERE / "tiktok-overlays.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def load_obs_scene(scene_name: str) -> dict:
    data = json.loads(OBS_COLLECTION.read_text(encoding="utf-8"))
    by_uuid = {s.get("uuid"): s for s in data["sources"]}
    scene = next((s for s in data["sources"]
                  if s.get("id") == "scene" and s.get("name") == scene_name), None)
    if not scene:
        raise SystemExit(f"scene not found in OBS: {scene_name}")
    items = []
    for it in scene["settings"].get("items", []):
        src = by_uuid.get(it.get("source_uuid")) or {}
        bw, bh = float(it["bounds"]["x"]), float(it["bounds"]["y"])
        if bw == 0 and bh == 0:
            # OBS: bounds 0x0 means "source native size"
            if src.get("id") == "game_capture":
                bw, bh = 1920, 1080
            elif src.get("id") == "browser_source":
                bw = float((src.get("settings") or {}).get("width") or 1920)
                bh = float((src.get("settings") or {}).get("height") or 1080)
        items.append({
            "name": it.get("name"),
            "kind": src.get("id"),
            "settings": src.get("settings") or {},
            "pos": (float(it["pos"]["x"]), float(it["pos"]["y"])),
            "bounds": (bw, bh),
            "crop": (int(it.get("crop_left", 0)), int(it.get("crop_top", 0)),
                     int(it.get("crop_right", 0)), int(it.get("crop_bottom", 0))),
        })
    return {"name": scene_name, "items": items}


def track_for(scene_name: str) -> int:
    """V_* scenes live on the portrait canvas (track 1); GAME_* on landscape (0)."""
    return 1 if scene_name.startswith("V_") else 0


def rewrite_url(obs_url: str, base: str, token: str) -> str:
    u = urlparse(obs_url)
    parts = [p for p in u.path.split("/") if p]
    page = parts[-1] if parts else "chat"
    qs = urlencode({k: v[0] for k, v in parse_qs(u.query).items() if k != "cb"})
    out = f"{base.rstrip('/')}/o/{token}/{page}"
    return out + (f"?{qs}" if qs else "")


def build_descriptor(item: dict, base: str, token: str) -> dict | None:
    tl_type = KIND_TO_TL.get(item["kind"])
    if not tl_type:
        return None
    payload: dict = {}
    if tl_type == "camera":
        # An empty camera payload makes TL pick a default device, and on the
        # landscape track it chose "OBS Virtual Camera" - which only emits frames
        # while OBS's virtual cam is running, so the corner stayed black. Pin the
        # same physical device OBS uses instead.
        want_name = str(item["settings"].get("video_device_id") or "").split(":")[0].strip()
        r = ls.run(f"const d = await {S}.source.enumCameraDevices() || []; return d;")
        devs = r.get("value") if isinstance(r, dict) else []
        chosen = None
        for d in (devs or []):
            if d.get("isVirtualCamera"):
                continue
            if want_name and want_name.lower() in str(d.get("name", "")).lower():
                chosen = d
                break
        if not chosen:
            for d in (devs or []):
                if not d.get("isVirtualCamera"):
                    chosen = d
                    break
        if chosen:
            payload = {"deviceId": chosen.get("id"), "name": chosen.get("name"),
                       "width": 1280, "height": 720, "rate": 30,
                       "videoRange": 0, "colorSpace1": 1}
    if tl_type == "game":
        # Field names recovered from a working source the user added by hand in
        # the app UI: captureMethod "custom", captureType "Texture", and
        # windowClass/exe/handle/pid (NOT CLASS/EXE/WINDOW/PID as enumGames
        # reports them - that mapping is what made our captures stay black).
        want = str(item["settings"].get("window") or "").lower()
        exe_hint = want.split(":")[-1] if want else ""
        r = ls.run(f"const g = await {S}.source.enumGames() || [];"
                   f"return g.filter(x => String(x.EXE||'').toLowerCase().includes({json.dumps(exe_hint.lower())}));")
        matches = r.get("value") if isinstance(r, dict) else None
        payload = {"captureMethod": "custom", "cx": 0, "cy": 0,
                   "cpuFocus": False, "cursor": False, "fpsLimit": True,
                   "enableCompatiable": False, "errorReportTime": 5,
                   "gamecaptureImplVersion": 2, "captureType": "Texture"}
        if isinstance(matches, list) and matches:
            g = matches[0]
            payload.update({
                "windowClass": g.get("CLASS") or g.get("windowClass") or "",
                "exe": g.get("EXE") or g.get("exe") or "",
                "handle": g.get("WINDOW") or g.get("handle") or 0,
                "pid": g.get("PID") or g.get("pid") or 0,
            })
    if tl_type == "browser":
        page_w = int(item["settings"].get("width") or 1920)
        page_h = int(item["settings"].get("height") or 1080)
        payload = {"url": rewrite_url(item["settings"].get("url", ""), base, token),
                   "cx": page_w, "cy": page_h,
                   "enableAudio": False, "enableCustomResolution": False,
                   "zoom": 1}
    return {"name": NAME_PREFIX + str(item["name"]), "type": tl_type, "payload": payload}


def read_source(src_id: str) -> dict | None:
    r = ls.run(f"const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || [];"
               f"const m = s.filter(x => x.id === {json.dumps(src_id)});"
               f"return m.length ? m[0] : null;")
    v = r.get("value")
    return v if isinstance(v, dict) else None


def live_preview_rect() -> dict | None:
    """Size of the preview canvas element TL is currently rendering."""
    expr = ("(() => { const out = [];"
            "for (const e of document.querySelectorAll('canvas')) {"
            "  const r = e.getBoundingClientRect();"
            "  if (r.width > 60 && r.height > 60) out.push({w: Math.round(r.width), h: Math.round(r.height)});"
            "} return out; })()")
    r = ls.eval_probe(expr)
    v = r.get("value")
    if isinstance(v, str):
        import json as _json
        try:
            v = _json.loads(v)
        except Exception:
            v = None
    if not isinstance(v, list) or not v:
        return None
    # portrait previews are taller than wide; pick by the canvas aspect
    is_portrait = canvas_w < canvas_h
    want = (lambda o: o["h"] > o["w"]) if is_portrait else (lambda o: o["w"] > o["h"])
    for o in v:
        if want(o):
            return o
    return v[0]


def apply_geometry(src_id: str, item: dict, canvas: tuple[int, int]) -> dict:
    """Place a source at its OBS rect, in LIVE Studio's own coordinate space.

    The rules were reverse-engineered from a working source the user added by
    hand in the app UI, plus measurement:
      * TL stores size as the source's NATIVE resolution (game: 1920x1080).
      * clip is in NATIVE-source pixels (an OBS crop of 420 on a 1920-wide
        capture is clip.x=420, unchanged).
      * translate/scale are in the space declared by preview_cx/preview_cy; if
        you declare the LIVE preview element size, values pass through 1:1.
    So everything is converted from OBS canvas px into preview px via the
    canvas->preview ratio.
    """
    canvas_w, canvas_h = canvas
    w, h = item["bounds"]
    x, y = item["pos"]
    cl, ct, cr, cb = item["crop"]

    pv = live_preview_rect() or {"w": int(canvas_w * 0.2), "h": int(canvas_h * 0.2)}
    pw, ph = pv["w"], pv["h"]

    cur = read_source(src_id) or {}
    tf = cur.get("transform") or {}
    own = tf.get("size") or {}
    native_w = float(own.get("cx") or 1920)
    native_h = float(own.get("cy") or 1080)

    def to_preview(px, py, pw_, ph_, nw, nh):
        tx = px / canvas_w * pw
        ty = py / canvas_h * ph
        sx = (pw_ / canvas_w * pw) / nw if nw else 0
        sy = (ph_ / canvas_h * ph) / nh if nh else 0
        return tx, ty, sx, sy

    tx, ty, sx, sy = to_preview(x, y, w, h, native_w, native_h)
    obj = {
        "transform": {
            "translateX": tx, "translateY": ty, "rotateAngle": 0,
            "scaleX": sx, "scaleY": sy, "flipH": False, "flipV": False,
            "size": {"cx": native_w, "cy": native_h},
            "preview_cx": pw, "preview_cy": ph,
        },
        "visible": True,
        "clip": {"x": cl, "y": ct, "z": cr, "w": cb},
    }
    body = (f"const id={json.dumps(src_id)}; const o={json.dumps(obj)};"
            f"try{{ await {S}.source.updateSourceTransform(id, o, true, false);"
            f" return 'ok'; }}catch(e){{ return String(e&&e.message||e); }}")
    r = ls.run(body)
    return r.get("value") if isinstance(r, dict) else r


def existing_ids() -> set[str]:
    r = ls.run(f"const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || [];"
               f"return s.map(x => x.id);")
    v = r.get("value")
    return set(v) if isinstance(v, list) else set()


canvas_w, canvas_h = 1920, 1080   # module-level so apply_geometry sees the active canvas


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="V_GAME_Tarkov")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args(argv)

    try:
        ls.main_target()
    except Exception as exc:  # noqa: BLE001
        print(f"  no studio target: {exc}")
        return 2

    st = state()
    base, token = st.get("url") or "", st.get("token") or ""
    if not base or not token:
        print("  no tunnel state - run start-tiktok-overlays.ps1 first")
        return 2

    # canvas size per track
    r = ls.run(f"const s = await {S}.streamSetting.getSettings();"
               f"return {{landscape: s.resolution, portrait: s.secondResolution}};")
    sizes = r.get("value") or {}
    canvas = {0: tuple(sizes.get("landscape") or (1920, 1080)),
              1: tuple(sizes.get("portrait") or (1080, 1920))}

    if args.clean:
        before = existing_ids()
        r = ls.run(f"""const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || [];
          const mine = s.filter(x => (x.name||'').startsWith({json.dumps(NAME_PREFIX)}));
          let n = 0;
          for (const m of mine) {{
            try {{ await {S}.source.removeSource(m.id, m.track); n++; }} catch (e) {{}}
          }}
          return n;""")
        print(f"  removed {r.get('value')} mirrored source(s)")
        return 0

    scene = load_obs_scene(args.scene)
    track = track_for(args.scene)
    cnv = canvas[track]
    global canvas_w, canvas_h
    canvas_w, canvas_h = cnv
    print(f"  scene {scene['name']}  -> track {track} ({'portrait' if track else 'landscape'} {cnv[0]}x{cnv[1]})")
    print(f"  tunnel: {base}")

    skipped = []
    for item in scene["items"]:
        desc = build_descriptor(item, base, token)
        if not desc:
            skipped.append(item["name"])
            continue
        info = (f"{desc['type']:7} {item['name'][:22]:24} "
                f"pos=({item['pos'][0]:.0f},{item['pos'][1]:.0f}) "
                f"size=({item['bounds'][0]:.0f}x{item['bounds'][1]:.0f})")
        if item["crop"] != (0, 0, 0, 0):
            info += f" crop={item['crop']}"
        print(f"    {info}")
        if not args.apply:
            continue
        desc["track"] = track
        w, h = item["bounds"]
        cl, ct, cr, cb = item["crop"]
        desc["transform"] = {
            "translateX": item["pos"][0], "translateY": item["pos"][1],
            "scaleX": 1, "scaleY": 1, "rotateAngle": 0,
            "flipH": False, "flipV": False,
            "size": {"cx": w, "cy": h},
            "preview_cx": cnv[0], "preview_cy": cnv[1],
        }
        desc["clip"] = {"x": cl, "y": ct, "z": cr, "w": cb}
        body = (f"const d={json.dumps(desc)};"
                f"try{{ const r = await {S}.source.addSource(d, {{recordHistory:false}});"
                f" return r; }}catch(e){{ return {{error:String(e&&e.message||e)}}; }}")
        added = ls.run(body)
        av = added.get("value") if isinstance(added, dict) else None
        sid = (av or {}).get("id")
        if not sid or not (av or {}).get("result"):
            print(f"      FAILED: {json.dumps(av)[:160]}")
            continue
        setres = apply_geometry(sid, item, cnv)
        print(f"      added id={sid[:8]} geometry={setres}")
        time.sleep(0.5)

    if skipped:
        print(f"  skipped (unsupported kinds): {', '.join(skipped)}")
    if not args.apply:
        print("\n  dry run - nothing created (pass --apply)")
    MANIFEST.write_text(json.dumps({"scene": args.scene, "track": track,
                                    "tunnel": base, "applied": args.apply},
                                   indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
