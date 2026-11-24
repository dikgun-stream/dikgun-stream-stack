#!/usr/bin/env python3
"""Read-only public shim for the overlay pages.

Why this exists
---------------
TikTok LIVE Studio's "Add link" source rejects http://127.0.0.1 (verified: the
dialog reports "Enter correct URL"), so an overlay can only be linked in over a
public HTTPS URL. Tunneling the real overlay server would expose its control
surface too - /api/panic, /api/obs/*, /api/event - which would let anyone who
found the tunnel drive the stream.

So this sits in front and forwards ONLY what a browser source needs:

    GET /o/<token>/<page>     an overlay page (token required)
    GET /assets/**            shared css/js
    GET /items/**             item art
    WS  /ws?role=overlay&ch=  overlay data feed (allowlisted channels)

Everything else - the entire /api tree - returns 404. No mutation is reachable
through the tunnel.

Run:  python public_shim.py --port 7480 --token <secret>
"""

from __future__ import annotations

import argparse
import asyncio
import os
import urllib.parse
import urllib.request

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

UP_HTTP = os.environ.get("SHIM_UPSTREAM", "http://127.0.0.1:7475")
UP_WS = UP_HTTP.replace("https://", "wss://").replace("http://", "ws://")

PAGES = {
    "chat", "alerts", "topic", "goals", "input", "deadlock", "clips",
    "starting-soon", "brb", "ending",
}
# channels an overlay page is allowed to subscribe to
CHANNELS = {"alerts", "chat", "topic", "counters", "scenes", "deadlock"}

TOKEN = "changeme"
VIEWPORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewport_state.json")


def _fetch(path: str, timeout: float = 15.0) -> tuple[int, str, bytes]:
    req = urllib.request.Request(f"{UP_HTTP}{path}",
                                 headers={"User-Agent": "dsh-overlay-shim/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, "text/plain", b"upstream error"
    except Exception:  # noqa: BLE001
        return 502, "text/plain", b"upstream unreachable"


# ---------------------------------------------------------------- live feed player
# LIVE Studio cannot publish or receive RTMP by itself, and it refuses localhost
# URLs - so the OBS feed arrives as WebRTC from the local relay (mediamtx), played
# by this page, which is served over the public tunnel like the overlays are.
# The signalling POST is proxied below so the page can stay same-origin.
FEED_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;padding:0;height:100%;background:#000;overflow:hidden}
video{width:100%;height:100%;object-fit:fill;display:block}
#err{position:fixed;left:0;top:0;color:#f66;font:12px monospace;padding:6px;background:#0008}</style>
</head><body><video id="v" autoplay playsinline></video>
<script src="/hlsjs"></script>
<script>
// HLS, not WebRTC: mediamtx refuses H264 with B-frames (OBS NVENC emits them) and
// WebRTC drops the AAC audio track. HLS carries both, and its muxers run anyway.
var P = new URLSearchParams(location.search).get('path') || 'portrait';
var v = document.getElementById('v');
function beacon(extra) {
  var q = 'path=' + encodeURIComponent(P) + '&w=' + innerWidth + '&h=' + innerHeight +
          '&rtc=' + (typeof RTCPeerConnection === 'function' ? 1 : 0) +
          '&mse=' + (typeof MediaSource === 'function' ? 1 : 0) +
          '&st=' + encodeURIComponent(extra || 'ok');
  try { fetch('/viewport?' + q, {cache:'no-store'}); } catch (e) {}
}
function fail(m) {
  var e = document.getElementById('err');
  if (!e) { e = document.createElement('div'); e.id = 'err'; document.body.appendChild(e); }
  e.textContent = 'feed: ' + m;
  beacon('err:' + m);
}
beacon('boot');

function playAudio() {
  v.muted = false;
  var pr = v.play();
  if (pr && pr.catch) pr.catch(function () { v.muted = true; v.play().catch(function () {}); });
}

if (!window.Hls) {
  fail('hls.js did not load');
} else if (Hls.isSupported()) {
  var hls = new Hls({ liveDurationInfinity: true, lowLatencyMode: true, enableWorker: false });
  hls.loadSource('/hls/' + P + '/index.m3u8');
  hls.attachMedia(v);
  hls.on(Hls.Events.MANIFEST_PARSED, function () { beacon('manifest'); playAudio(); });
  hls.on(Hls.Events.FRAG_PLAYING, function () { beacon('playing'); });
  hls.on(Hls.Events.ERROR, function (evt, data) {
    if (data && data.fatal) {
      beacon('hls:' + data.type + ':' + (data.details || ''));
      try { hls.destroy(); } catch (e) {}
      setTimeout(function () { location.reload(); }, 4000);
    }
  });
} else if (v.canPlayType('application/vnd.apple.mpegurl')) {
  v.src = '/hls/' + P + '/index.m3u8';
  playAudio();
} else {
  fail('no HLS support');
}

// supervisor: reload if playback freezes (publisher restart, tunnel blip)
var lastT = -1, stalled = 0;
setInterval(function () {
  var t = v.currentTime;
  if (t === lastT) { stalled++; } else { stalled = 0; }
  lastT = t;
  if (stalled >= 6) { beacon('frozen-reload'); location.reload(); }
}, 2000);
</script></body></html>"""



def _access(line: str) -> None:
    try:
        import time as _t
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "shim_access.log"),
                  "a", encoding="utf-8") as fh:
            fh.write(f"{_t.strftime('%H:%M:%S')}  {line}\n")
    except Exception:  # noqa: BLE001
        pass

def _proxy_whep(path: str, body: bytes) -> tuple[int, str, bytes]:
    """Forward a WHEP offer to the local relay and return its answer."""
    req = urllib.request.Request(f"http://127.0.0.1:8889/{path}/whep", data=body,
                                 headers={"Content-Type": "application/sdp"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", "application/sdp"), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, "text/plain", exc.read()[:400]
    except Exception as exc:  # noqa: BLE001
        return 502, "text/plain", str(exc).encode()[:200]


def make_app() -> FastAPI:
    app = FastAPI(title="overlay public shim", docs_url=None, redoc_url=None)

    @app.get("/o/{token}/feed")
    async def feed_page(token: str):
        """WebRTC player for a relay path (?path=portrait|landscape)."""
        if token != TOKEN:
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(content=FEED_HTML, media_type="text/html")

    @app.get("/viewport")
    async def viewport(path: str = "", w: int = 0, h: int = 0, rtc: int = -1, mse: int = -1, st: str = ""):
        _access(f"VIEWPORT path={path} rtc={rtc} mse={mse} st={st[:60]}")
        """The player page reports its real browser viewport here.

        LIVE Studio renders Link sources in a CEF viewport that is smaller than
        the element it draws into (1080x1032 for a 1080x1920 canvas). The page
        cannot paint outside its viewport, so the placement tool needs those
        numbers to stretch the right box.
        """
        try:
            import json as _json, time as _time
            data = {}
            try:
                with open(VIEWPORT_FILE, encoding="utf-8") as fh:
                    data = _json.load(fh)
                if not isinstance(data, dict) or "portrait" not in data:
                    data = {}
            except Exception:  # noqa: BLE001
                data = {}
            data[path or "unknown"] = {"w": w, "h": h, "rtc": rtc, "mse": mse,
                                       "st": (st or "")[:200], "at": _time.time()}
            with open(VIEWPORT_FILE, "w", encoding="utf-8") as fh:
                _json.dump(data, fh)
        except Exception:  # noqa: BLE001
            pass
        return Response(status_code=204)

    @app.post("/whep/{path}/whep")
    async def whep(path: str, request: Request):
        body = await request.body()
        _access(f"WHEP POST path={path} from={request.client.host if request.client else '?'} len={len(body)}")
        status, ctype, data = await asyncio.to_thread(_proxy_whep, path, body)
        return Response(content=data, media_type=ctype, status_code=status,
                        headers={"Access-Control-Allow-Origin": "*"})

    @app.delete("/whep/{path}/whep/{session}")
    async def whep_close(path: str, session: str):
        def do():
            req = urllib.request.Request(f"http://127.0.0.1:8889/{path}/whep/{session}",
                                         method="DELETE")
            try:
                with urllib.request.urlopen(req, timeout=6) as r:
                    return r.status
            except Exception:  # noqa: BLE001
                return 204
        status = await asyncio.to_thread(do)
        return Response(status_code=status)

    @app.get("/hlsjs")
    async def hlsjs():
        """hls.js, served from the shim so the feed page has no CDN dependency."""
        import os as _os
        path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "hls.min.js")
        try:
            with open(path, "rb") as fh:
                return Response(content=fh.read(), media_type="text/javascript")
        except Exception:  # noqa: BLE001
            return Response(content=b"/* hls.js missing */", media_type="text/javascript")

    @app.get("/hls/{path}/{name}")
    async def hls(path: str, name: str):
        """HLS fallback for the same feed."""
        def do():
            url = f"http://127.0.0.1:8888/{path}/{name}"
            try:
                with urllib.request.urlopen(url, timeout=8) as r:
                    return r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read()
            except Exception:  # noqa: BLE001
                return 404, "text/plain", b"no feed"
        status, ctype, data = await asyncio.to_thread(do)
        return Response(content=data, media_type=ctype, status_code=status)

    @app.get("/o/{token}/{page}")
    async def overlay_page(token: str, page: str, request: Request):
        if token != TOKEN:
            return JSONResponse({"error": "not found"}, status_code=404)
        if page not in PAGES:
            return JSONResponse({"error": "not found"}, status_code=404)
        # forward the query string verbatim: the overlays take real parameters
        # (?game=tarkov, ?bottom=470&width=520, ?v=1) and the vertical variants
        # only exist through them
        qs = request.url.query
        target = f"/overlay/{page}" + (f"?{qs}" if qs else "")
        import asyncio
        status, ctype, body = await asyncio.to_thread(_fetch, target)
        return Response(content=body, media_type=ctype, status_code=status)

    @app.get("/assets/{path:path}")
    async def assets(path: str):
        import asyncio
        status, ctype, body = await asyncio.to_thread(_fetch, f"/assets/{path}")
        return Response(content=body, media_type=ctype, status_code=status)

    @app.get("/items/{path:path}")
    async def items(path: str):
        import asyncio
        status, ctype, body = await asyncio.to_thread(_fetch, f"/items/{path}")
        return Response(content=body, media_type=ctype, status_code=status)

    @app.get("/solid/{colour}.png")
    async def solid(colour: str):
        # Calibration marker: a flat colour page with no websocket, so geometry can
        # be measured by locating the blob in a screenshot.
        try:
            rgb = tuple(int(colour[i:i+2], 16) for i in (0, 2, 4))
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad colour"}, status_code=400)
        import struct, zlib
        w = h = 64
        raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
        def chunk(tag, data):
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw))
               + chunk(b"IEND", b""))
        return Response(content=png, media_type="image/png")

    @app.get("/solid/{colour}")
    async def solid_page(colour: str):
        html = ("<!doctype html><html><head><meta charset=utf-8>"
                "<style>html,body{margin:0;padding:0;height:100%;background:#" + colour +
                ";}</style></head><body></body></html>")
        return Response(content=html, media_type="text/html")

    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204)

    @app.websocket("/ws")
    async def ws_proxy(client: WebSocket):
        role = client.query_params.get("role", "")
        ch = client.query_params.get("ch", "")
        if role != "overlay" or ch not in CHANNELS:
            await client.close(code=1008)
            return
        import websockets
        upstream_url = f"{UP_WS}/ws?" + urllib.parse.urlencode({"role": role, "ch": ch})
        try:
            async with websockets.connect(upstream_url, open_timeout=10) as upstream:
                await client.accept()

                async def pump_down():
                    async for msg in upstream:
                        await client.send_text(msg if isinstance(msg, str) else msg.decode())

                async def pump_up():
                    while True:
                        msg = await client.receive_text()
                        await upstream.send(msg)

                import asyncio
                done, pending = await asyncio.wait(
                    [asyncio.create_task(pump_down()), asyncio.create_task(pump_up())],
                    return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001
            try:
                await client.close(code=1011)
            except Exception:  # noqa: BLE001
                pass

    @app.get("/{full_path:path}")
    async def deny(full_path: str):
        # everything else - including the whole /api tree - is not reachable
        return JSONResponse({"error": "not found"}, status_code=404)

    return app


app = make_app()


def main() -> int:
    import uvicorn
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7480)
    ap.add_argument("--token", default=os.environ.get("SHIM_TOKEN", ""))
    args = ap.parse_args()
    TOKEN = args.token or os.urandom(9).hex()
    print(f"  shim on http://127.0.0.1:{args.port}/o/<token>/chat")
    print(f"  TOKEN = {TOKEN}")
    print(f"  upstream = {UP_HTTP}  (only overlays/assets/ws are forwarded)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
