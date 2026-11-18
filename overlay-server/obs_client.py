"""
Async obs-websocket v5 client.

Deliberately hand-rolled on the `websockets` library rather than a wrapper: the
protocol is small, and this keeps reconnect + auth behaviour under our control.

Protocol summary
    op 0  Hello        <- server, carries auth challenge
    op 1  Identify     -> we send rpcVersion + auth hash
    op 2  Identified   <- server
    op 5  Event        <- server pushes
    op 6  Request      -> we send
    op 7  RequestResponse <- server
    op 9  RequestBatchResponse
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from typing import Any, Callable

import websockets

log = logging.getLogger("obs")

# bitmask of event categories we want pushed: general, config, scenes, inputs, outputs, ...
DEFAULT_EVENT_SUBS = (
    1 << 0   # General
    | 1 << 3  # Scenes
    | 1 << 6  # Outputs
    | 1 << 8  # InputVolumeMeters (unused yet, harmless)
)


class OBSClient:
    def __init__(
        self,
        url: str = "ws://127.0.0.1:4455",
        password: str | None = None,
        on_event: Callable[[str, dict], Any] | None = None,
    ) -> None:
        self.url = url
        self.password = password
        self.on_event = on_event

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._seq = 0
        self._identify_timeout = 10.0

        self.connected = False
        self.last_error: str | None = None

    # ------------------------------------------------------------------ setup
    def _build_auth(self, auth: dict) -> str | None:
        if not auth:
            return None
        if not self.password:
            raise RuntimeError("OBS requires authentication but no password is configured")
        secret = base64.b64encode(
            hashlib.sha256((self.password + auth["salt"]).encode()).digest()
        ).decode()
        return base64.b64encode(
            hashlib.sha256((secret + auth["challenge"]).encode()).digest()
        ).decode()

    # ------------------------------------------------------------------- run
    async def run(self) -> None:
        """Maintain a connection forever. Cancellation-safe."""
        backoff = 2.0
        while True:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure means retry
                self.last_error = str(exc)
                log.warning("OBS connection failed: %s", exc)
            finally:
                self.connected = False
                self._ws = None
                # fail any in-flight requests so callers don't hang
                for fut in list(self._pending.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("OBS disconnected"))
                self._pending.clear()

            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.6, 15.0)  # gentle ramp, capped

    async def _connect_once(self) -> None:
        async with websockets.connect(self.url, max_size=32 * 1024 * 1024) as ws:
            self._ws = ws
            hello = json.loads(await ws.recv())
            if hello.get("op") != 0:
                raise RuntimeError(f"expected Hello, got op {hello.get('op')}")

            auth_hash = self._build_auth(hello.get("d", {}).get("authentication"))
            identify: dict[str, Any] = {
                "rpcVersion": 1,
                "eventSubscriptions": DEFAULT_EVENT_SUBS,
            }
            if auth_hash:
                identify["authentication"] = auth_hash
            await ws.send(json.dumps({"op": 1, "d": identify}))

            log.info("OBS: connecting to %s", self.url)
            await self._read_loop(ws)

    async def _read_loop(self, ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            op = msg.get("op")
            d = msg.get("d") or {}

            if op == 2:  # Identified
                self.connected = True
                self.last_error = None
                log.info("OBS: connected (obs-websocket %s)", d.get("negotiatedRpcVersion"))
            elif op == 7:  # RequestResponse
                fut = self._pending.pop(d.get("requestId"), None)
                if fut and not fut.done():
                    fut.set_result(d)
            elif op == 5:  # Event
                if self.on_event:
                    try:
                        self.on_event(d.get("eventType"), d.get("eventData") or {})
                    except Exception:  # noqa: BLE001
                        log.exception("OBS: event handler raised")
            elif op == 9:  # auth/request failure
                self.last_error = json.dumps(d.get("error", d))
                log.error("OBS: request failed - %s", self.last_error)

    # -------------------------------------------------------------- requests
    async def request(self, req_type: str, data: dict | None = None, timeout: float = 10.0) -> dict:
        if not self._ws or not self.connected:
            raise RuntimeError("OBS is not connected")

        self._seq += 1
        rid = f"r{self._seq}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut

        payload: dict[str, Any] = {"op": 6, "d": {"requestId": rid, "requestType": req_type}}
        if data:
            payload["d"]["requestData"] = data
        await self._ws.send(json.dumps(payload))

        try:
            resp = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

        status = resp.get("requestStatus") or {}
        if not status.get("result"):
            raise RuntimeError(f"{req_type} failed: code {status.get('code')} {status.get('comment') or ''}".strip())
        return resp.get("responseData") or {}

    # ---------------------------------------------------------------- sugar
    async def scenes(self) -> dict:
        data = await self.request("GetSceneList")
        return {
            "current": data.get("currentProgramSceneName"),
            "scenes": [s["sceneName"] for s in data.get("scenes", [])],
        }

    async def set_scene(self, name: str) -> None:
        await self.request("SetCurrentProgramScene", {"sceneName": name})

    async def stats(self) -> dict:
        try:
            data = await self.request("GetStats")
        except Exception:  # noqa: BLE001
            data = {}
        out: dict[str, Any] = {
            "fps": round(data.get("activeFps", 0), 2),
            "cpu": round(data.get("cpuUsage", 0), 1),
            "memoryMB": round(data.get("memoryUsage", 0), 0),
            "renderLag": data.get("renderSkippedFrames", 0),
            "encodeLag": data.get("outputSkippedFrames", 0),
        }
        try:
            st = await self.request("GetStreamStatus")
            out["streaming"] = st.get("outputActive", False)
            out["streamTime"] = st.get("outputTimecode", "")
        except Exception:  # noqa: BLE001
            out["streaming"] = False
        try:
            rec = await self.request("GetRecordStatus")
            out["recording"] = rec.get("outputActive", False)
        except Exception:  # noqa: BLE001
            out["recording"] = False
        return out
