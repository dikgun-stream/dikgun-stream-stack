#!/usr/bin/env python3
"""Calibrate LIVE Studio's source transform against real canvas pixels.

TL stores a source position as
    transform {translateX, translateY, scaleX, scaleY, size{cx,cy},
               preview_cx, preview_cy, ...}
on a canvas of known size (1920x1080 landscape / 1080x1920 portrait). OBS gives
us plain pixel rects. To convert one to the other we need the two scale factors,
and the only reliable way is to place a known marker and look at the canvas.

Method:
  1. create a Link source pointing at the shim's /solid/<hex> page (flat colour)
  2. set a known transform, screenshot the canvas preview, locate the colour blob
  3. repeat with a second transform
  4. derive px-per-unit from the two measurements and print the mapping

Run:  python lscalibrate.py            (creates and deletes one source)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import lsclient as ls

S = ls.SERVICE
MARK = "FF00FF"          # magenta, nothing else in the UI uses it
MARK_RGB = (255, 0, 255)
PROBE_NAME = "DSH_CALIB"


def _run(body: str, timeout: float = 60.0):
    r = ls.run(body, timeout)
    return r.get("value") if isinstance(r, dict) else None


def canvas_size() -> dict:
    v = _run(f"const st = await {S}.streamSetting.getSettings();"
             f"return {{resolution: st.resolution, second: st.secondResolution}};")
    return v if isinstance(v, dict) else {}


def find_probe() -> dict | None:
    v = _run(f"const s = await {S}.source.getCurrentSceneSourceParamsSnapshot() || [];"
             f"const p = s.filter(x => x.name === {json.dumps(PROBE_NAME)});"
             f"return p.length ? p[0] : null;")
    return v if isinstance(v, dict) else None


def create_probe(url: str, track: int) -> dict | None:
    desc = {"name": PROBE_NAME, "type": "browser",
            "payload": {"url": url, "cx": 800, "cy": 600,
                        "enableAudio": False, "enableCustomResolution": False,
                        "zoom": 3.8017840169239308},
            "track": track}
    _run(f"const d = {json.dumps(desc)};"
         f"try {{ const r = await {S}.source.addSource(d, {{recordHistory:false}});"
         f" return {{ok: !!(r && r.result), raw: r}}; }}"
         f"catch (e) {{ return {{ok:false, error:String(e&&e.message||e)}}; }}")
    return find_probe()


def set_transform(source_id: str, transform: dict) -> None:
    _run(f"const id = {json.dumps(source_id)}; const t = {json.dumps(transform)};"
         f"try {{ await {S}.source.updateSourceTransform(id, t, true, false); return 1; }}"
         f"catch (e) {{ return String(e && e.message || e); }}")


def remove_probe() -> None:
    p = find_probe()
    if not p:
        return
    track = p.get("track") or 0
    _run(f"try {{ await {S}.source.removeSource({json.dumps(p['id'])}, {track}); return 1; }}"
         f"catch (e) {{ return String(e && e.message || e); }}")


def shoot(out: Path) -> dict | None:
    import lsshot
    rect = lsshot.preview_rect()
    if not rect:
        print("  no preview rect")
        return None
    ok = asyncio.run(lsshot._capture(rect, out))
    if not ok:
        return None
    blob = lsshot.measure_colour(out, MARK_RGB, tol=60)
    return {"preview": rect, "blob": blob}


def main() -> int:
    try:
        ls.main_target()
    except Exception as exc:  # noqa: BLE001
        print(f"  no studio target: {exc}")
        return 2

    sizes = canvas_size()
    print(f"  canvas sizes: {json.dumps(sizes)}")

    tunnel = "https://spelling-long-asia-contract.trycloudflare.com"
    url = f"{tunnel}/solid/{MARK}"
    print(f"  marker url: {url}")

    remove_probe()                      # clean slate
    probe = create_probe(url, 0)
    if not probe:
        print("  could not create the probe source")
        return 1
    print(f"  probe created: {probe.get('id')}  track={probe.get('track')}")
    print(f"  default transform: {json.dumps(probe.get('transform'))[:300]}")

    base = dict(probe.get("transform") or {})
    size = base.get("size") or {"cx": 800, "cy": 600}

    def tf(tx, ty, sx, sy=None):
        t = dict(base)
        t.update({"translateX": tx, "translateY": ty,
                  "scaleX": sx, "scaleY": sy if sy is not None else sx,
                  "rotateAngle": 0, "flipH": False, "flipV": False,
                  "size": {"cx": size.get("cx", 800), "cy": size.get("cy", 600)}})
        return t

    measurements = {}
    for label, (tx, ty, sx) in {"A": (0, 0, 0.5), "B": (100, 50, 0.25)}.items():
        set_transform(probe["id"], tf(tx, ty, sx))
        import time
        time.sleep(1.6)                 # let the canvas repaint
        shot = shoot(Path(f"calib_{label}.png"))
        print(f"  {label}: transform tx={tx} ty={ty} scale={sx} -> {json.dumps(shot)[:260]}")
        measurements[label] = {"transform": (tx, ty, sx), "shot": shot}

    # derive px per transform unit from the two points
    try:
        a, b = measurements["A"]["shot"]["blob"], measurements["B"]["shot"]["blob"]
        px_per_unit_x = (b["x0"] - a["x0"]) / 100.0
        px_per_unit_y = (b["y0"] - a["y0"]) / 50.0
        w_a = a["w"] / 0.5               # blob width at scale 1.0
        w_b = b["w"] / 0.25
        print("\n  === derived mapping ===")
        print(f"    px per translate unit: x={px_per_unit_x:.3f}  y={px_per_unit_y:.3f}")
        print(f"    blob width at scale 1.0: {w_a:.1f} / {w_b:.1f}  (should match)")
        print(f"    => canvas_px = translate * {px_per_unit_x:.3f}")
        print(f"    => px_per_scale_unit = {w_a:.1f}  (for cx={size.get('cx')})")
    except Exception as exc:  # noqa: BLE001
        print(f"  could not derive mapping: {exc}")

    remove_probe()
    print("  probe removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
