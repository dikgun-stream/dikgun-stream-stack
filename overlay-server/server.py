"""
DikGun overlay + control server.

    python -m uvicorn server:app --host 127.0.0.1 --port 7475
    (or: python server.py)

Responsibilities
  * serve overlay pages to OBS browser sources
  * push events to those overlays over WebSocket
  * expose a control API + the test dashboard
  * proxy live OBS control (scenes, visibility, stream/record, stats)

Everything is local-only: it binds 127.0.0.1 and talks to OBS on localhost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from obs_client import OBSClient
from twitch_irc import TwitchIRC
from chat_engine import ChatEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-10s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("app")

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
CONFIG_PATH = ROOT / "config.json"

ALERT_TYPES = {"follow", "sub", "resub", "giftsub", "cheer", "raid", "tip", "host", "test"}

DEFAULT_CONFIG: dict[str, Any] = {
    "alerts": {"duration_ms": 4500, "max_queue": 6, "sound": False},
    "starting_soon": {"minutes": 5, "headline": "Starting Soon", "subline": "Grab a drink."},
    "goals": {
        "followers": {"label": "Followers", "current": 591, "target": 1000, "color": "#4ade80"},
        "subs": {"label": "Subs", "current": 0, "target": 50, "color": "#facc15"},
    },
    "labels": {"last_follower": "—", "last_sub": "—", "last_tip": "—"},
    "chat": {"max_messages": 12},
    "obs": {"url": "ws://127.0.0.1:4455"},
    # Anonymous IRC read-only feed. Covers chat, subs, gifts, raids, bits.
    # Followers are NOT announced in IRC -- those need EventSub (Streamer.bot).
    "twitch": {"channel": "dikgun", "enabled": True},
    "stream": {"title": "", "game": ""},
    "topic": {"text": "", "label": "Now Playing"},
    "panic": {"scene": "SHOW_BRB", "muteSources": ["Mic/Aux"]},
    "commands": {"enabled": True},
    "hints": [],
    "counters": {"deaths": 0, "kills": 0},
    "poll": {"active": False, "question": "", "options": [], "votes": {}},
}


# --------------------------------------------------------------------------- config
def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return _deep_merge(DEFAULT_CONFIG, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("config.json unreadable (%s) - using defaults", exc)
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


CONFIG: dict[str, Any] = load_config()


def obs_password() -> str | None:
    """Pull the password straight out of OBS's own websocket config."""
    p = Path(os.environ["APPDATA"]) / "obs-studio/plugin_config/obs-websocket/config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("server_password")
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------ hub
class Hub:
    """Tracks overlay and dashboard sockets and fans events out."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.overlays: dict[str, set[WebSocket]] = {}
        self.controls: set[WebSocket] = set()

    async def add_overlay(self, ch: str, ws: WebSocket) -> None:
        async with self._lock:
            self.overlays.setdefault(ch, set()).add(ws)

    async def drop_overlay(self, ch: str, ws: WebSocket) -> None:
        async with self._lock:
            self.overlays.get(ch, set()).discard(ws)

    async def add_control(self, ws: WebSocket) -> None:
        async with self._lock:
            self.controls.add(ws)

    async def drop_control(self, ws: WebSocket) -> None:
        async with self._lock:
            self.controls.discard(ws)

    async def counts(self) -> dict[str, int]:
        async with self._lock:
            return {ch: len(v) for ch, v in self.overlays.items()}

    async def to_overlays(self, ch: str, payload: dict) -> int:
        async with self._lock:
            targets = list(self.overlays.get(ch, ()))
        dead = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.drop_overlay(ch, ws)
        return len(targets) - len(dead)

    async def to_controls(self, payload: dict) -> None:
        async with self._lock:
            targets = list(self.controls)
        dead = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.drop_control(ws)


HUB = Hub()

# ----------------------------------------------------------------------------- obs
OBS = OBSClient(url=CONFIG["obs"]["url"], password=obs_password())

OBS_EVENTS_TO_FORWARD = {
    "CurrentProgramSceneChanged",
    "StreamStateChanged",
    "RecordStateChanged",
    "SceneItemEnableStateChanged",
}


def _on_obs_event(event_type: str, data: dict) -> None:
    if event_type not in OBS_EVENTS_TO_FORWARD:
        return
    # called from the OBS reader loop, which is already on the event loop
    asyncio.ensure_future(
        HUB.to_controls({"kind": "obs_event", "event": event_type, "data": data})
    )


OBS.on_event = _on_obs_event


# --------------------------------------------------------------------------- twitch irc
CHAT = ChatEngine(CONFIG, lambda: save_config(CONFIG))


async def _irc_emit(event: dict) -> None:
    """Fan an IRC-derived event out to overlays and the dashboard."""
    channel = event.get("channel", "alerts")
    event.setdefault("id", f"irc-{int(time.time() * 1000)}")
    event.setdefault("ts", time.time())
    delivered = await HUB.to_overlays(channel, event)
    await HUB.to_controls({**event, "delivered": delivered, "source": "twitch"})

    # chat commands react to the same lines
    if event.get("type") == "chat" and CONFIG.get("commands", {}).get("enabled", True):
        for out in CHAT.handle(event.get("user", ""), event.get("message", ""),
                               bool(event.get("mod"))):
            ch = out.get("channel", "chat")
            n = await HUB.to_overlays(ch, out)
            await HUB.to_controls({**out, "delivered": n, "source": "command"})


IRC: TwitchIRC | None = (
    TwitchIRC(CONFIG["twitch"]["channel"], _irc_emit)
    if CONFIG.get("twitch", {}).get("enabled")
    else None
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(OBS.run(), name="obs-client")]
    if IRC:
        tasks.append(asyncio.create_task(IRC.run(), name="twitch-irc"))
    log.info("overlay server ready on http://127.0.0.1:7475")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="DikGun overlays", lifespan=lifespan)


# --------------------------------------------------------------------------- models
class EventIn(BaseModel):
    type: str = "test"
    user: str = "TestViewer"
    amount: str | int | float | None = None
    message: str = ""
    channel: str | None = None


class ConfigIn(BaseModel):
    config: dict


class SceneIn(BaseModel):
    name: str


class ActionIn(BaseModel):
    action: str = Field(pattern="^(start|stop|toggle)$")


class VisibilityIn(BaseModel):
    scene: str
    source: str
    visible: bool


# ---------------------------------------------------------------------------- pages
@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(PUBLIC / "dashboard.html")


@app.get("/chatroom")
async def chatroom() -> FileResponse:
    """Standalone chat window for a second monitor (not an overlay)."""
    return FileResponse(PUBLIC / "chatroom.html")


@app.get("/overlay/{name}")
async def overlay(name: str) -> FileResponse:
    path = PUBLIC / "overlays" / f"{name}.html"
    if not path.is_file():
        raise HTTPException(404, f"no overlay named '{name}'")
    return FileResponse(path)


# ------------------------------------------------------------------------- REST api
@app.get("/api/status")
async def status() -> dict:
    counts = await HUB.counts()
    return {
        "ok": True,
        "serverTime": time.time(),
        "overlays": counts,
        "controls": len(HUB.controls),
        "obs": {
            "connected": OBS.connected,
            "url": OBS.url,
            "lastError": OBS.last_error,
        },
        "twitch": {
            "enabled": IRC is not None,
            "channel": IRC.channel if IRC else None,
            "connected": IRC.connected if IRC else False,
            "lastError": IRC.last_error if IRC else None,
        },
    }


@app.get("/api/config")
async def get_config() -> dict:
    return CONFIG


@app.put("/api/config")
async def put_config(body: ConfigIn) -> dict:
    # Mutate in place: ChatEngine / the IRC hook hold a reference to this dict,
    # so rebinding the global would leave them pointing at a stale copy.
    merged = _deep_merge(CONFIG, body.config)
    CONFIG.clear()
    CONFIG.update(merged)
    save_config(CONFIG)
    for ch in ("alerts", "goals", "labels", "starting-soon", "chat"):
        await HUB.to_overlays(ch, {"kind": "config", "config": CONFIG})
    return {"ok": True, "config": CONFIG}


async def _dispatch(etype: str, user: str, amount, message: str, channel: str | None) -> dict:
    """Shared event fan-out used by both the POST and GET entry points."""
    channel = channel or ("chat" if etype == "chat" else "alerts")

    # Guard: if a caller's variable substitution failed, the literal placeholder
    # (e.g. "%user%") arrives intact. Never put that on stream.
    if not user or "%" in user:
        log.warning("unresolved placeholder in user field: %r", user)
        user = "Someone"

    event = {
        "kind": "event",
        "id": f"{int(time.time() * 1000)}",
        "type": etype,
        "user": user or "unknown",
        "amount": amount,
        "message": message or "",
        "ts": time.time(),
    }
    delivered = await HUB.to_overlays(channel, event)
    await HUB.to_controls({**event, "delivered": delivered})
    return {"ok": True, "channel": channel, "delivered": delivered, "event": event}


@app.post("/api/event")
async def post_event(body: EventIn) -> dict:
    return await _dispatch(
        body.type.lower(), body.user, body.amount, body.message, body.channel
    )


@app.get("/api/hook")
async def get_hook(
    type: str = "test",
    user: str = "unknown",
    amount: str | None = None,
    message: str = "",
    channel: str | None = None,
) -> dict:
    """
    GET entry point.

    Exists because Streamer.bot's native `Core -> Network -> Fetch URL` sub-action
    only supports GET. That lets the follow alert be wired with no C#, no
    assembly references, and no POST support required.

        /api/hook?type=follow&user=%user%
    """
    amt: Any = amount
    if isinstance(amount, str) and amount.isdigit():
        amt = int(amount)
    return await _dispatch(type.lower(), user, amt, message, channel)


# ------------------------------------------------------------------------ OBS proxy
@app.get("/api/obs/scenes")
async def obs_scenes() -> dict:
    try:
        return await OBS.scenes()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/obs/scene")
async def obs_set_scene(body: SceneIn) -> dict:
    try:
        await OBS.set_scene(body.name)
        return {"ok": True, "scene": body.name}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/obs/stats")
async def obs_stats() -> dict:
    if not OBS.connected:
        raise HTTPException(503, "OBS not connected")
    return await OBS.stats()


@app.post("/api/obs/stream")
async def obs_stream(body: ActionIn) -> dict:
    mapping = {"start": "StartStream", "stop": "StopStream", "toggle": "ToggleStream"}
    try:
        await OBS.request(mapping[body.action])
        return {"ok": True, "action": body.action}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/obs/record")
async def obs_record(body: ActionIn) -> dict:
    mapping = {"start": "StartRecord", "stop": "StopRecord", "toggle": "ToggleRecord"}
    try:
        await OBS.request(mapping[body.action])
        return {"ok": True, "action": body.action}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/obs/sources")
async def obs_sources(scene: str) -> dict:
    try:
        data = await OBS.request("GetSceneItemList", {"sceneName": scene})
        items = [
            {
                "name": i.get("sourceName"),
                "id": i.get("sceneItemId"),
                "visible": bool(i.get("sceneItemEnabled")),
            }
            for i in data.get("sceneItems", [])
        ]
        return {"scene": scene, "sources": items}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/obs/source_visibility")
async def obs_visibility(body: VisibilityIn) -> dict:
    try:
        item = await OBS.request("GetSceneItemId", {"sceneName": body.scene, "sourceName": body.source})
        await OBS.request(
            "SetSceneItemEnabled",
            {"sceneName": body.scene, "sceneItemId": item["sceneItemId"], "sceneItemEnabled": body.visible},
        )
        return {"ok": True, "scene": body.scene, "source": body.source, "visible": body.visible}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


# ---------------------------------------------------------------------- admin API
class StreamInfoIn(BaseModel):
    title: str | None = None
    game: str | None = None


class AudioSetIn(BaseModel):
    source: str
    volume_db: float | None = None
    muted: bool | None = None


class TopicIn(BaseModel):
    text: str = ""
    label: str = "Now Playing"


STREAMERBOT_HTTP = "http://127.0.0.1:7474"
STREAMERBOT_ACTION = "Set Stream Info"


def _sb_reachable() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 7474), timeout=0.6):
            return True
    except OSError:
        return False


def _post_json_sync(url: str, payload: dict, timeout: float = 4.0) -> tuple[bool, str]:
    import urllib.request
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, str(resp.status)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


@app.get("/api/stream/info")
async def get_stream_info() -> dict:
    return {
        "title": CONFIG["stream"]["title"],
        "game": CONFIG["stream"]["game"],
        "topic": CONFIG["topic"]["text"],
        "streamerbot": _sb_reachable(),
    }


@app.post("/api/stream/info")
async def set_stream_info(body: StreamInfoIn) -> dict:
    """
    Update title / category.

    Both platforms are driven through Streamer.bot rather than by us holding
    Twitch and YouTube OAuth tokens: it already has authorised accounts, and its
    HTTP server exposes DoAction. That keeps credentials in one place.
    """
    if body.title is not None:
        CONFIG["stream"]["title"] = body.title
    if body.game is not None:
        CONFIG["stream"]["game"] = body.game
    save_config(CONFIG)

    result: dict = {"ok": True, "title": CONFIG["stream"]["title"],
                    "game": CONFIG["stream"]["game"], "delivered": []}

    if not _sb_reachable():
        result["ok"] = False
        result["hint"] = (
            "Streamer.bot HTTP server not reachable on 127.0.0.1:7474. "
            "In Streamer.bot: Servers/Clients -> HTTP Server -> Start. "
            f"Then create an action named '{STREAMERBOT_ACTION}' that sets the "
            "Twitch title/game (and YouTube title) from %title% and %game%."
        )
        return result

    payload = {
        "action": {"name": STREAMERBOT_ACTION},
        "args": {"title": CONFIG["stream"]["title"], "game": CONFIG["stream"]["game"]},
    }
    ok, info = await asyncio.to_thread(
        _post_json_sync, f"{STREAMERBOT_HTTP}/DoAction", payload
    )
    result["streamerbot"] = {"ok": ok, "detail": info}
    if not ok:
        result["ok"] = False
        result["hint"] = (
            f"DoAction failed ({info}). The action must be named exactly "
            f"'{STREAMERBOT_ACTION}'."
        )
    return result


@app.get("/api/obs/audio")
async def obs_audio() -> dict:
    if not OBS.connected:
        raise HTTPException(503, "OBS not connected")
    # GetInputVolume answers for any input, so filter by kind or we list cameras
    # and captures as if they were faders.
    AUDIO_KINDS = (
        "wasapi_output_capture", "wasapi_input_capture", "wasapi_process_output_capture",
        "coreaudio_input_capture", "coreaudio_output_capture",
        "ffmpeg_source", "vlc_source", "dshow_input",
    )
    inputs = (await OBS.request("GetInputList")).get("inputs", [])
    out = []
    for inp in inputs:
        name = inp.get("inputName")
        kind = inp.get("inputKind", "")
        if not kind.startswith(AUDIO_KINDS):
            continue
        try:
            vol = await OBS.request("GetInputVolume", {"inputName": name})
            mute = await OBS.request("GetInputMute", {"inputName": name})
        except RuntimeError:
            continue          # not an audio-capable source
        db = vol.get("inputVolumeDb", 0.0)
        out.append({
            "name": name,
            "volumeDb": -60.0 if db < -60 else round(db, 1),
            "muted": bool(mute.get("inputMuted")),
        })
    out.sort(key=lambda x: x["name"])
    return {"sources": out}


@app.post("/api/obs/audio")
async def obs_audio_set(body: AudioSetIn) -> dict:
    if not OBS.connected:
        raise HTTPException(503, "OBS not connected")
    try:
        if body.volume_db is not None:
            await OBS.request("SetInputVolume", {
                "inputName": body.source, "inputVolumeDb": float(body.volume_db)
            })
        if body.muted is not None:
            await OBS.request("SetInputMute", {
                "inputName": body.source, "inputMuted": bool(body.muted)
            })
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True, "source": body.source}


@app.post("/api/panic")
async def panic() -> dict:
    """Mute the mic and cut to the BRB scene. One button, no thinking required."""
    if not OBS.connected:
        raise HTTPException(503, "OBS not connected")
    did = []
    for src in CONFIG["panic"]["muteSources"]:
        try:
            await OBS.request("SetInputMute", {"inputName": src, "inputMuted": True})
            did.append(f"muted {src}")
        except RuntimeError:
            pass
    try:
        await OBS.set_scene(CONFIG["panic"]["scene"])
        did.append(f"scene -> {CONFIG['panic']['scene']}")
    except RuntimeError as exc:
        did.append(f"scene failed: {exc}")
    await HUB.to_controls({"kind": "panic", "detail": did})
    return {"ok": True, "did": did}


@app.post("/api/topic")
async def set_topic(body: TopicIn) -> dict:
    CONFIG["topic"]["text"] = body.text
    CONFIG["topic"]["label"] = body.label
    save_config(CONFIG)
    event = {
        "kind": "event", "type": "topic", "channel": "topic",
        "user": body.label, "message": body.text, "ts": time.time(),
    }
    delivered = await HUB.to_overlays("topic", event)
    await HUB.to_controls({**event, "delivered": delivered})
    return {"ok": True, "delivered": delivered}


class LiveIn(BaseModel):
    action: str = "start"          # start | stop
    dryRun: bool = False


@app.post("/api/live")
async def live_control(body: LiveIn) -> dict:
    """
    One-button go-live / go-off across every output.

    Both destinations are fully programmatic:
      * Twitch            -> OBS native stream output
      * YouTube vertical  -> Aitum Vertical hotkey

    The horizontal YouTube output was removed from Aitum Multistream because that
    plugin exposes no start/stop hotkey, so it could never have been part of a
    single-button flow.
    """
    if not OBS.connected:
        raise HTTPException(503, "OBS not connected")

    start = body.action.lower() != "stop"
    vertical_hotkey = "VerticalCanvasDockStartStreaming" if start else "VerticalCanvasDockStopStreaming"

    hotkeys = (await OBS.request("GetHotkeyList")).get("hotkeys", [])
    have_vertical = vertical_hotkey in hotkeys

    plan = [
        f"OBS native stream {'START' if start else 'STOP'}  (Twitch)",
    ]
    plan.append(
        f"trigger {vertical_hotkey}  (YouTube vertical)" if have_vertical
        else "vertical hotkey NOT available"
    )

    if body.dryRun:
        return {"ok": True, "dryRun": True, "action": body.action,
                "verticalHotkeyAvailable": have_vertical, "plan": plan}

    results = []
    try:
        await OBS.request("StartStream" if start else "StopStream")
        results.append("native stream ok")
    except RuntimeError as exc:
        results.append(f"native stream failed: {exc}")

    if have_vertical:
        try:
            await OBS.request("TriggerHotkeyByName", {"hotkeyName": vertical_hotkey})
            results.append("vertical ok")
        except RuntimeError as exc:
            results.append(f"vertical failed: {exc}")

    await HUB.to_controls({"kind": "live", "action": body.action, "results": results})
    return {"ok": True, "action": body.action, "results": results, "plan": plan}


class HintRevealIn(BaseModel):
    id: str | None = None


class PollIn(BaseModel):
    question: str
    options: list[str]


class ChatSimIn(BaseModel):
    user: str = "TestViewer"
    message: str
    mod: bool = False


@app.post("/api/chat/simulate")
async def chat_simulate(body: ChatSimIn) -> dict:
    """Feed a synthetic chat line through the exact same path as real IRC."""
    await _irc_emit({
        "kind": "event", "type": "chat", "channel": "chat",
        "user": body.user, "message": body.message, "mod": body.mod,
    })
    return {"ok": True, "hints": len(CHAT.unrevealed())}


@app.get("/api/chat/state")
async def chat_state() -> dict:
    return {
        "hints": CHAT.unrevealed(),
        "hintsTotal": len(CONFIG.get("hints", [])),
        "counters": CONFIG.get("counters", {}),
        "poll": CHAT.poll_results(),
    }


@app.post("/api/hints/reveal")
async def hint_reveal(body: HintRevealIn) -> dict:
    """Pop a hint and put it on the topic card so only it appears on stream."""
    hint = CHAT.reveal(body.id)
    if not hint:
        raise HTTPException(404, "no hints in the jar")

    CONFIG["topic"] = {"label": f"Hint from {hint['user']}", "text": hint["text"]}
    save_config(CONFIG)
    event = {"kind": "event", "type": "topic", "channel": "topic",
             "user": CONFIG["topic"]["label"], "message": hint["text"], "ts": time.time()}
    delivered = await HUB.to_overlays("topic", event)
    await HUB.to_controls({**event, "delivered": delivered, "source": "hint"})
    return {"ok": True, "hint": hint, "delivered": delivered}


@app.post("/api/hints/clear")
async def hint_clear(onlyRevealed: bool = False) -> dict:
    n = CHAT.clear_hints(only_revealed=onlyRevealed)
    return {"ok": True, "removed": n, "remaining": len(CHAT.unrevealed())}


@app.post("/api/poll/start")
async def poll_start(body: PollIn) -> dict:
    p = CHAT.start_poll(body.question, body.options)
    ev = {"kind": "event", "type": "poll", "channel": "poll",
          "user": body.question, "message": "|".join(p["options"]), "ts": time.time()}
    await HUB.to_overlays("poll", ev)
    await HUB.to_controls({**ev, "source": "poll"})
    return {"ok": True, "poll": p}


@app.post("/api/poll/end")
async def poll_end() -> dict:
    CHAT.end_poll()
    return {"ok": True, "results": CHAT.poll_results()}


# ------------------------------------------------------------------------ websocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    role = ws.query_params.get("role", "control")
    ch = ws.query_params.get("ch", "alerts")

    if role == "overlay":
        await HUB.add_overlay(ch, ws)
        log.info("overlay connected: %s", ch)
        try:
            await ws.send_json({"kind": "config", "config": CONFIG})
            while True:
                # overlays can send acks/pings; we mostly ignore them
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            await HUB.drop_overlay(ch, ws)
            log.info("overlay disconnected: %s", ch)
        return

    await HUB.add_control(ws)
    log.info("dashboard connected")
    try:
        await ws.send_json({"kind": "hello", "config": CONFIG, "obs": {"connected": OBS.connected}})
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("kind") == "ping":
                await ws.send_json({"kind": "pong", "ts": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        await HUB.drop_control(ws)
        log.info("dashboard disconnected")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=7475, log_level="info")
