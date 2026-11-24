#!/usr/bin/env python3
"""Minimal client for driving TikTok LIVE Studio over CDP.

The app exposes its own service layer on the *main window* renderer:

    __studio_runtime__.features.studio.service.<namespace>.<method>(...)

Calls are proxied over ServiceCore IPC to the native libobs binding, so this
module never touches the app's transport - it just evaluates JavaScript in the
page that already has the client.

Requires the app to be running with --remote-debugging-port (launch-debug.ps1).
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

CDP_PORT = 9222
MAIN_URL_HINT = "/html/main/index.html"
SERVICE = "__studio_runtime__.features.studio.service"


def _targets() -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=10) as r:
        return json.loads(r.read().decode())


def main_target() -> dict:
    """The studio window (not a popup, chat float or browser-source host)."""
    pages = [t for t in _targets() if t.get("type") == "page"]
    for t in pages:
        if MAIN_URL_HINT in (t.get("url") or ""):
            return t
    raise RuntimeError("studio main window not found - is the app running with CDP?")


async def _evaluate(ws_url: str, expression: str, timeout: float = 60.0):
    import websockets
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024,
                                  open_timeout=timeout) as ws:
        await ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True,
                       "awaitPromise": True, "timeout": int(timeout * 1000)},
        }))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if msg.get("id") != 1:
                continue
            res = msg.get("result", {})
            if "exceptionDetails" in res:
                det = res["exceptionDetails"]
                desc = ""
                try:
                    desc = det.get("exception", {}).get("description", "")
                except Exception:
                    pass
                return {"ok": False, "stage": "js",
                        "error": f"{det.get('text')} {desc}"[:400]}
            r = res.get("result", {})
            return {"ok": True, "value": r.get("value", r.get("description"))}


def js_async(body: str) -> str:
    """Wrap a statement body (use `return`) in an async IIFE with error capture."""
    return ("(async()=>{try{const r=await (async()=>{" + body + "})();"
            "return JSON.stringify({ok:true,v:r===undefined?null:r});"
            "}catch(e){return JSON.stringify({ok:false,error:String(e&&e.message||e).slice(0,400)})}})()")


async def call(body: str, timeout: float = 60.0) -> dict:
    """Run a JS body against the studio window; returns {"ok", "value"|"error"}."""
    t = main_target()
    raw = await _evaluate(t["webSocketDebuggerUrl"], js_async(body), timeout)
    if not raw.get("ok"):
        return raw
    val = raw.get("value")
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError:
            return {"ok": True, "value": val}
        # the injected wrapper answers {ok, v}; normalise to {ok, value}
        if isinstance(parsed, dict) and "v" in parsed and "value" not in parsed:
            parsed["value"] = parsed.pop("v")
        return parsed
    return {"ok": True, "value": val}


def run(body: str, timeout: float = 60.0) -> dict:
    return asyncio.run(call(body, timeout))


# ---------------------------------------------------------------- convenience
def snapshot_scenes() -> dict:
    return run(f"return await {SERVICE}.source.getCurrentSceneSourceParamsSnapshot()")


def current_scene() -> dict:
    return run(f"const s = await {SERVICE}.source.getCurrentScene();"
               f"return s ? JSON.parse(JSON.stringify(s)) : null")


def create_scene(name: str) -> dict:
    return run(f"return await {SERVICE}.source.createScene({{name: {json.dumps(name)}}})")


def delete_scene(scene_id: str) -> dict:
    return run(f"return await {SERVICE}.source.deleteScene({json.dumps(scene_id)})")


def eval_probe(expression: str) -> dict:
    """Arbitrary expression in studio context (read-only by convention)."""
    return run(f"return ({expression})")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "scenes":
        print(json.dumps(snapshot_scenes(), indent=2)[:2000])
    elif len(sys.argv) > 1 and sys.argv[1] == "scene":
        print(json.dumps(current_scene(), indent=2)[:2000])
    else:
        print(f"target: {main_target().get('id')}")
        print(json.dumps(eval_probe("document.body.innerText.replace(/\\s+/g,' ').slice(0,200)"),
                         indent=2))
