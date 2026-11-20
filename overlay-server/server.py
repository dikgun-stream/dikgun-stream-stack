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
from input_capture import InputWatcher
from deadlock_hud import (read_scoreboard, find_badge, read_damage, read_items,
                          KILL_THRESHOLD)

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
    "deadlock": {"boon": 0, "souls": 0, "spirit": 0, "gun": 0, "vitality": 0,
                 "kills": 0, "assists": 0, "damage": 0, "items": [],
                 "account_id": 0, "session_start": 0, "wins": 0, "losses": 0,
                 "last_result": None, "player_name": ""},
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


CAPTURES = Path(os.environ.get("LOCALAPPDATA", "C:\\")) / "StreamerMode" / "captures"
GAME_SOURCE = "SRC_Capture_Deadlock"


async def grab_game_frame(label: str = "frame", w: int = 1920, h: int = 1080) -> str | None:
    """Save one frame of the OBS game capture to disk.

    This is the *only* way we look at the game: OBS's own captured output, which
    the streamer is already broadcasting. No memory access, no injection, no
    process inspection.
    """
    import base64

    if not OBS.connected:
        return None
    try:
        res = await OBS.request("GetSourceScreenshot", {
            "sourceName": GAME_SOURCE, "imageFormat": "png",
            "imageWidth": w, "imageHeight": h,
        })
    except (RuntimeError, Exception):  # noqa: BLE001
        return None
    data = (res or {}).get("imageData", "")
    if "," not in data:
        return None
    CAPTURES.mkdir(parents=True, exist_ok=True)
    out = CAPTURES / f"{label}-{int(time.time() * 1000)}.png"
    try:
        out.write_bytes(base64.b64decode(data.split(",", 1)[1]))
    except Exception:  # noqa: BLE001
        return None
    return str(out)


# --------------------------------------------------------------- input overlay
# Streams global key/mouse transitions to the `input` overlay. The watcher runs
# on its own thread and hands work to the event loop thread-safely; the queue is
# bounded so a stalled consumer can never grow memory without limit.
_INPUT_QUEUE: asyncio.Queue | None = None
_INPUT_LOOP: asyncio.AbstractEventLoop | None = None
_INPUT: InputWatcher | None = None


def _on_input_change(kind: str, name: str, down: bool) -> None:
    loop, queue = _INPUT_LOOP, _INPUT_QUEUE
    if loop is None or queue is None:
        return
    try:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"kind": "input", "type": kind, "key": name, "down": down,
             "ts": time.time()},
        )
    except (RuntimeError, asyncio.QueueFull):
        pass          # loop closed or consumer behind; dropping a frame is fine


_TAB_TASK: asyncio.Task | None = None


async def _tab_monitor() -> None:
    """While Tab is held, keep watching for the scoreboard.

    This is the only window where it is on screen. Parsing K/D/A on release (as
    an earlier version did) captured a frame 0.45s after the key came up, by
    which time the scoreboard is gone and the parse silently fails.
    """
    state = _dl()
    name = state.get("player_name") or ""
    for _ in range(14):                      # ~11 seconds of holding Tab
        await asyncio.sleep(0.8)
        path = await grab_game_frame("tabmon", 1920, 1080)
        if not path:
            continue
        kda = None
        if name:
            try:
                kda = await asyncio.to_thread(read_scoreboard, path, name)
            except Exception:  # noqa: BLE001
                kda = None
        # the frame is only needed until it has been read
        try:
            os.remove(path)
        except OSError:
            pass
        if kda and kda.complete:
            if _maybe_new_match(state, kda):
                log.info("new match detected (K/D/A 0/0/0) - per-match stats cleared")
            else:
                state["kills"] = kda.kills
                state["deaths"] = kda.deaths
                state["assists"] = kda.assists
                _apply_deaths(state, kda.deaths)
                log.info("scoreboard parsed: K/D/A %d/%d/%d",
                         kda.kills, kda.deaths, kda.assists)
            save_config(CONFIG)
            await _dl_broadcast()
            return
    log.info("tab monitor finished without a readable scoreboard")


def _apply_life_damage(state: dict, life_value: int) -> None:
    """Track the current life's damage without ever double-counting.

    The panel's "Total Damage" is scoped to your CURRENT LIFE and resets on
    respawn. Two rules keep the session total honest:

      * within a life the value only goes up, so we keep the max -- that
        survives a reset we have not seen yet instead of losing the total
      * the bank happens ONLY when a death is confirmed via the scoreboard,
        so the same life can never be rolled in twice
    """
    state["damage_life"] = max(state.get("damage_life", 0), max(0, int(life_value)))
    state["damage"] = state.get("damage_total", 0) + state["damage_life"]


def _apply_deaths(state: dict, deaths: int) -> None:
    """A confirmed death ends the life: bank it and start the next one at zero."""
    prev = state.get("last_deaths")
    if prev is not None and deaths > prev and state.get("damage_life", 0) > 0:
        state["damage_total"] = state.get("damage_total", 0) + state["damage_life"]
        state["damage_life"] = 0
        state["damage"] = state.get("damage_total", 0)
    state["last_deaths"] = deaths


def _maybe_new_match(state: dict, kda) -> bool:
    """Detect a match boundary from the scoreboard.

    The public API only knows about a match after it ends, and bot matches never
    appear in it at all, so the HUD is the reliable signal: a fresh match always
    shows 0/0/0. Mid-match that cannot legitimately happen once you have any
    kills, deaths or assists, so "back to 0/0/0" means the next game started.
    """
    prev = [int(state.get(k) or 0) for k in ("kills", "deaths", "assists")]
    if any(prev) and kda.complete and (kda.kills, kda.deaths, kda.assists) == (0, 0, 0):
        state.update({
            "kills": 0, "deaths": 0, "assists": 0,
            "damage": 0, "damage_life": 0, "damage_total": 0, "damage_in": 0,
            "last_deaths": 0,
            "items": [],
            "match_started": int(time.time()),
        })
        return True
    return False


async def _capture_scoreboard() -> None:
    """Grab the scoreboard once it has settled, then read the player's K/D/A."""
    await asyncio.sleep(0.45)
    path = await grab_game_frame("scoreboard", 1920, 1080)
    if not path:
        return
    state = _dl()
    name = state.get("player_name") or ""
    if not name:
        return
    try:
        kda = await asyncio.to_thread(read_scoreboard, path, name)
    except Exception:  # noqa: BLE001
        kda = None
    if kda and kda.complete:
        if _maybe_new_match(state, kda):
            log.info("new match detected (K/D/A back to 0/0/0) - per-match stats cleared")
            save_config(CONFIG)
            await _dl_broadcast()
        else:
            state["kills"] = kda.kills
            state["deaths"] = kda.deaths
            state["assists"] = kda.assists
            _apply_deaths(state, kda.deaths)
            save_config(CONFIG)
            await _dl_broadcast()
    # Damage readout removed: the panel's layout changes between modes and
    # could not be read reliably, and it was more trouble than it was worth.
    save_config(CONFIG)
    await _dl_broadcast()


async def _item_watch_loop() -> None:
    """Keep the item tower in sync with the bottom-left grid.

    Purchases are visible in the HUD at all times, so a slow poll is enough and
    avoids competing with the badge watcher for capture bandwidth.
    """
    while True:
        await asyncio.sleep(2.5)
        if int(_dl().get("account_id") or 0) == 0:
            continue
        path = await grab_game_frame("itemwatch", 1920, 1080)
        if not path:
            continue
        try:
            names = await asyncio.to_thread(read_items, path)
        except Exception:  # noqa: BLE001
            names = None
        try:
            os.remove(path)
        except OSError:
            pass
        if names is None:
            continue          # unreadable frame (e.g. shop screen) - keep last known
        state = _dl()
        current = [i.get("name") for i in state.get("items", [])]
        new_names = [d["name"] for d in names]
        if new_names != current:
            # carry the card art through so the tower shows real icons
            state["items"] = [
                {"name": d["name"],
                 "icon": f"/items/{d['file']}" if d.get("file") else None}
                for d in names
            ]
            save_config(CONFIG)
            await _dl_broadcast()
            log.info("items updated: %s", new_names)


async def _capture_janitor() -> None:
    """Delete capture frames after an hour.

    Tab holds produce a PNG every 0.8s, and the badge watcher runs constantly,
    so without this the captures folder grows without bound.
    """
    while True:
        await asyncio.sleep(600)
        try:
            cutoff = time.time() - 3600
            removed = 0
            for f in CAPTURES.glob("*.png"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                        removed += 1
                except OSError:
                    pass
            if removed:
                log.info("capture janitor removed %d old frame(s)", removed)
        except Exception:  # noqa: BLE001
            pass


async def _kill_watch_loop() -> None:
    """Watch for the KILL / ASSIST badge and count each appearance once."""
    last_hit = 0.0
    while True:
        await asyncio.sleep(1.5)
        if int(_dl().get("account_id") or 0) == 0:
            continue
        path = await grab_game_frame("badgewatch", 1280, 720)
        if not path:
            continue
        try:
            kind, score = await asyncio.to_thread(find_badge, path)
        except Exception:  # noqa: BLE001
            kind, score = None, 0.0
        try:
            os.remove(path)          # keep the captures folder from filling up
        except OSError:
            pass
        if kind and (time.time() - last_hit) > 4.0:
            last_hit = time.time()
            state = _dl()
            field = "kills" if kind == "kill" else "assists"
            state[field] = int(state.get(field, 0)) + 1
            save_config(CONFIG)
            await _dl_broadcast()
            log.info("%s badge detected (%.2f) -> %s=%s", kind, score, field, state[field])


async def _input_pump() -> None:
    assert _INPUT_QUEUE is not None
    while True:
        event = await _INPUT_QUEUE.get()
        await HUB.to_overlays("input", event)
        # Tab: on press start passively watching for the damage panel; on
        # release parse the K/D/A scoreboard from one clean frame.
        if event.get("type") == "key" and event.get("key") == "TAB":
            global _TAB_TASK
            if event.get("down"):
                if _TAB_TASK and not _TAB_TASK.done():
                    _TAB_TASK.cancel()
                _TAB_TASK = asyncio.create_task(_tab_monitor())
            else:
                if _TAB_TASK and not _TAB_TASK.done():
                    _TAB_TASK.cancel()
                asyncio.create_task(_capture_scoreboard())
        # F9 = "capture that!" — grab whatever is on screen right now. Handy for
        # one-off frames like an assist badge we do not have a template for yet.
        if (event.get("type") == "key" and event.get("key") == "F9"
                and event.get("down")):
            asyncio.create_task(grab_game_frame("hotkey", 1920, 1080))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _INPUT_QUEUE, _INPUT_LOOP, _INPUT

    _INPUT_QUEUE = asyncio.Queue(maxsize=512)
    _INPUT_LOOP = asyncio.get_running_loop()
    try:
        _INPUT = InputWatcher(_on_input_change)
        _INPUT.start()
        log.info("input watcher started (polling, no hooks)")
    except Exception as exc:  # noqa: BLE001 - overlay is optional
        log.warning("input watcher unavailable: %s", exc)
        _INPUT = None

    tasks = [asyncio.create_task(OBS.run(), name="obs-client")]
    if IRC:
        tasks.append(asyncio.create_task(IRC.run(), name="twitch-irc"))
    tasks.append(asyncio.create_task(_input_pump(), name="input-pump"))
    tasks.append(asyncio.create_task(_dl_poll_loop(), name="deadlock-poll"))
    tasks.append(asyncio.create_task(_kill_watch_loop(), name="kill-watch"))
    tasks.append(asyncio.create_task(_item_watch_loop(), name="item-watch"))
    tasks.append(asyncio.create_task(_capture_janitor(), name="capture-janitor"))
    # probe clip durations once in the background; the player works without them
    try:
        clip_lib.start_duration_fill(CLIPS_ROOT, CLIPS_CACHE)
    except Exception:  # noqa: BLE001
        pass
    log.info("overlay server ready on http://127.0.0.1:7475")
    try:
        yield
    finally:
        if _INPUT:
            _INPUT.stop()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass




# How much context a moment gets. Five seconds of lead-in is roughly one
# engagement's warning; three seconds of tail is enough to see the result without
# sitting through the reload.
PLAY_PAD_BEFORE = 5.0
PLAY_PAD_AFTER = 3.0
# Moments closer together than this play straight through rather than cutting
# away and back, which would feel choppier than leaving the gap in.
PLAY_MERGE_GAP = 30.0


def _play_windows(spans, duration=None, pad_before=PLAY_PAD_BEFORE,
                  pad_after=PLAY_PAD_AFTER, merge_gap=PLAY_MERGE_GAP):
    """Padded, merged play spans from raw [start, end] moment spans.

    Overlapping or near-adjacent spans are joined so the player never cuts out
    of something interesting only to cut back in a second later.
    """
    if not spans:
        return []
    padded = []
    for a, b in spans:
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if b < a:
            a, b = b, a
        x = max(0.0, a - pad_before)
        y = b + pad_after
        if duration:
            y = min(y, float(duration))
        if y - x >= 1.0:
            padded.append([x, y])
    if not padded:
        return []
    padded.sort(key=lambda r: r[0])
    merged = [padded[0]]
    for a, b in padded[1:]:
        if a - merged[-1][1] <= merge_gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [[round(a, 2), round(b, 2)] for a, b in merged]



# --------------------------------------------------------------------- checklists
# Pre-flight and post-flight lists. Deliberately exhaustive: the cost of reading
# a few extra lines is nothing next to going live with the wrong game selected
# or a bank tab on the second monitor.
CHECKLISTS_DEFAULT = {
    "startup": [
        "Dashboard category set - this sets Twitch AND YouTube",
        "Stream title set",
        "Scene collection is DikGun",
        "Horizontal scene matches the game (GAME_*)",
        "Vertical canvas scene matches the game (V_GAME_*)",
        "Game capture shows the game, not a black screen",
        "Camera framing ok - nothing embarrassing behind me",
        "Mic unmuted and levels moving",
        "Desktop/game audio audible in the mixer",
        "Mic is on AUDIO TRACK 1 - a stream only sends one track, so a mic left on track 2 is silent",
        "Recording tracks: game=1, mic=2, overlays=3 (local split only - this does NOT affect the stream)",
        "Streamer.bot running (alerts will not fire without it)",
        "Chat overlay connected and scrolling",
        "Clip player has content (Starting Soon shows footage)",
        "Windows Focus Assist / Do Not Disturb on",
        "Discord set to Do Not Disturb, DMs hidden from capture",
        "Phone silenced",
        "Closed: bank, email, personal tabs, private Discord channels",
        "Second monitor checked for anything that would show on stream",
        "Twitch output armed",
        "YouTube horizontal output armed",
        "YouTube vertical second stream key set (Encoder mode)",
        "Follower/sub goal values current",
        "BRB and Ending scenes ready",
    ],
    "shutdown": [
        "Switched to SHOW_Ending BEFORE stopping (so the VOD has an outro)",
        "Stopped the stream on Twitch",
        "Stopped the YouTube horizontal output",
        "Stopped the YouTube vertical output",
        "Stopped recording",
        "Mic muted",
        "Camera off",
        "Game closed",
        "Streamer.bot: no actions left running",
        "OBS closed gracefully (do not kill it - leaves a crash sentinel)",
        "Stream Deck / Stream Dock closed",
        "Ran restore to un-tile windows",
        "Checked the VOD is processing on Twitch",
        "Saved/exported any clips worth keeping",
        "Dashboard reset: title back to default",
        "Deadlock state cleared (K/D/A, items) ready for next session",
        "No private windows left open during the stream",
        "Deleting any accidental personal info captured on screen",
    ],
}


def _checklists() -> dict:
    """Ensure the checklist state exists and is well formed."""
    lists = CONFIG.setdefault("checklists", {})
    for key, defaults in CHECKLISTS_DEFAULT.items():
        current = lists.get(key)
        if not isinstance(current, list) or not current:
            lists[key] = [{"id": f"{key}-{i}", "text": t, "done": False}
                          for i, t in enumerate(defaults)]
            continue
        # keep stored "done" flags, but adopt any new default items
        have = {item.get("text") for item in current if isinstance(item, dict)}
        for i, t in enumerate(defaults):
            if t not in have:
                current.append({"id": f"{key}-new-{i}", "text": t, "done": False})
    return lists


app = FastAPI(title="DikGun overlays", lifespan=lifespan)

# Item card art for the tower, served straight from the web root.
_ITEMS_DIR = PUBLIC / "items"
_ITEMS_DIR.mkdir(parents=True, exist_ok=True)
from fastapi.staticfiles import StaticFiles  # noqa: E402

app.mount("/items", StaticFiles(directory=str(_ITEMS_DIR)), name="items")

# Clip library. OBS DVR recordings live in per-game folders; StaticFiles serves
# them with HTTP range support, which is what <video> needs to seek.
import clips as clip_lib  # noqa: E402

# Several folders hold recordings. Each gets its own mount so a clip's `rel`
# stays relative to its own root, and the player builds /<mount>/<rel>.
_CLIP_CANDIDATES = [
    ("videos", Path(os.environ.get("DIKGUN_CLIPS", r"C:\Users\JR\Videos"))),
    ("videosq", Path(os.environ.get("DIKGUN_CLIPS_Q", r"Q:\Game Clips"))),
]
CLIP_MOUNTS: list[tuple[str, Path]] = []
for _label, _path in _CLIP_CANDIDATES:
    if _path.is_dir():
        app.mount(f"/{_label}", StaticFiles(directory=str(_path)), name=f"mount-{_label}")
        CLIP_MOUNTS.append((_label, _path))
CLIPS_ROOT = CLIP_MOUNTS[0][1] if CLIP_MOUNTS else _CLIP_CANDIDATES[0][1]
CLIPS_CACHE = Path(__file__).with_name("clips_cache.json")

# Shared scene stylesheet/script. The /overlay/{name} route appends ".html", so
# asset files cannot live under /overlay and need their own mount.
_ASSETS_DIR = PUBLIC / "assets"
_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")


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


async def _all_clips() -> list[dict]:
    """Index every mounted clip folder and tag each clip with its mount."""
    out: list[dict] = []
    for label, path in CLIP_MOUNTS:
        try:
            found = await asyncio.to_thread(clip_lib.scan, path, CLIPS_CACHE)
        except Exception:  # noqa: BLE001
            found = []
        for c in found:
            c = dict(c)
            c["mount"] = label
            c["rel"] = c["rel"].replace("\\", "/")
            out.append(c)
    return out


@app.get("/api/clips")
async def api_clips(game: str | None = None, limit: int = 400) -> dict:
    """Index of local recordings, grouped by the game folder they came from."""
    items = await _all_clips()
    total = len(items)
    if game:
        items = [c for c in items if c["game"].lower() == game.lower()]
    return {
        "root": str(CLIPS_ROOT),
        "mounts": [{"name": l, "path": str(p)} for l, p in CLIP_MOUNTS],
        "available": bool(CLIP_MOUNTS),
        "total": total,
        "count": len(items),
        "games": clip_lib.games(items),
        "clips": items[: max(1, limit)],
    }


@app.get("/api/clips/highlights")
async def api_clip_highlights(source: str = "all", limit: int = 400) -> dict:
    """Playable highlight windows, from laughter, kills, or both.

    Each entry is a window inside a full recording: the player seeks to `start`
    and plays for `len` seconds. Records are never cut on disk -- the source
    files are hundreds of MB to several GB and copying them is not worth it.

    Kills and laughter are different shapes of moment (a kill wants ~20s of
    context, a laugh wants ~70s), so the windows arrive already sized by their
    own scanner and the player simply shuffles them together.
    """
    want = (source or "all").lower()
    clips = await _all_clips()
    by_rel = {c["rel"]: c for c in clips}

    def load(name: str) -> dict:
        path = Path(__file__).with_name(name)
        if not path.is_file():
            return {"files": {}}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"files": {}}

    out: list[dict] = []
    sources = []
    if want in ("all", "laughter"):
        sources.append(("laughter", load("laughter_index.json"), None))
    if want in ("all", "kills", "kill"):
        sources.append(("kill", load("kill_index.json"), None))
    if want in ("all", "markers"):
        # generic marker scan (Deadlock's red ring). Only high-confidence hits:
        # the 2024 Deadlock clips peak ~0.93 while the 2025 ones peak ~0.60,
        # i.e. the marker changed between versions, so a low bar admits noise.
        markers = load("marker_index.json")
        filtered = {"files": {}}
        for rel, res in (markers.get("files") or {}).items():
            if not isinstance(res, dict):
                continue
            if (res.get("peak") or 0) < 0.75:
                continue
            filtered["files"][rel] = res
        sources.append(("marker", filtered, None))
    if want in ("all", "curated"):
        sources.append(("curated", load("curated_highlights.json"), None))

    scanned = 0
    for label, data, _ in sources:
        if label == "curated":
            # hand-labelled moments use a flat list, not the per-file index shape
            entries = data.get("items") or []
            scanned += len(entries)
            for it in entries:
                rel = str(it.get("rel", "")).replace("\\", "/")
                meta = by_rel.get(rel)
                try:
                    start = float(it["start"])
                    end = float(it["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                if end - start < 5:
                    continue
                out.append({
                    "rel": rel,
                    "mount": (meta or {}).get("mount", "videos"),
                    "game": (meta or {}).get("game") or it.get("game", ""),
                    "title": (meta or {}).get("title") or rel.split("/")[-1],
                    "start": round(start, 2),
                    "len": round(end - start, 2),
                    "kills": None,
                    "laugh_at": None,
                    "peak": None,
                    "source": "curated",
                    "windows": [[round(start, 2), round(end, 2)]],
                    "label": it.get("label"),
                })
            continue

        files = data.get("files") or {}
        scanned += len(files)
        for rel, res in files.items():
            meta = by_rel.get(rel)
            if not meta or not isinstance(res, dict) or not res.get("ok"):
                continue
            for seg in res.get("segments") or []:
                try:
                    start = float(seg["start"])
                    end = float(seg["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                if end - start < 5:
                    continue

                # The moment itself, not the padded segment: the player seeks
                # here so a clip opens on the action rather than on whatever
                # was on screen when the recording started.
                #
                # The indexes only persist the first and last hit of a segment,
                # not each one, so a dense stretch (Deadlock with 30 hits) is
                # covered by one continuous span rather than invented interior
                # timestamps.
                if label == "laughter":
                    moment_a = seg.get("laugh_at")
                    moment_b = max(float(seg.get("laugh_at") or 0),
                                     float(seg.get("laugh_until") or 0)) or None
                else:
                    moment_a = seg.get("first_at")
                    moment_b = seg.get("last_at")

                spans = []
                if moment_a is not None:
                    spans.append((moment_a, moment_b if moment_b is not None
                                           else moment_a))
                windows = _play_windows(spans, res.get("duration"))

                out.append({
                    "rel": rel,
                    "mount": meta.get("mount", "videos"),
                    "game": meta["game"],
                    "title": meta["title"],
                    "start": round(start, 2),
                    "len": round(end - start, 2),
                    "kills": seg.get("kills") if label != "marker" else seg.get("hits"),
                    "laugh_at": seg.get("laugh_at"),
                    "peak": res.get("peak") or res.get("peak_score"),
                    "source": label,
                    "windows": windows,
                })

    out.sort(key=lambda x: -(x.get("kills") or 0) * 10 - (x.get("laugh_at") or 0))
    by_source = {}
    for e in out:
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1
    return {
        "count": len(out),
        "source": want,
        "scanned": scanned,
        "bySource": by_source,
        "highlights": out[: max(1, limit)],
    }




@app.get("/api/checklists")
async def get_checklists() -> dict:
    lists = _checklists()
    summary = {}
    for key, items in lists.items():
        done = sum(1 for i in items if i.get("done"))
        summary[key] = {"done": done, "total": len(items)}
    return {"lists": lists, "summary": summary}


@app.post("/api/checklists/toggle")
async def toggle_checklist(body: dict) -> dict:
    """body: {"list": "startup", "id": "startup-3", "done": true}"""
    key = str(body.get("list", ""))
    item_id = str(body.get("id", ""))
    lists = _checklists()
    if key not in lists:
        raise HTTPException(400, f"unknown list '{key}'")
    for item in lists[key]:
        if item.get("id") == item_id:
            item["done"] = bool(body.get("done", not item.get("done")))
            save_config(CONFIG)
            return {"ok": True, "list": key, "id": item_id, "done": item["done"]}
    raise HTTPException(404, f"no item '{item_id}' in '{key}'")


@app.post("/api/checklists/reset")
async def reset_checklist(body: dict) -> dict:
    """body: {"list": "startup"} - unticks everything for the next session."""
    key = str(body.get("list", ""))
    lists = _checklists()
    if key not in lists:
        raise HTTPException(400, f"unknown list '{key}'")
    for item in lists[key]:
        item["done"] = False
    save_config(CONFIG)
    return {"ok": True, "list": key, "count": len(lists[key])}


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


@app.get("/api/live/status")
async def live_status() -> dict:
    """Is the stream actually up, and what is it showing?

    Exists so other processes -- the Discord bot, monitors -- can tell whether a
    stream is live without talking to OBS themselves and without holding the
    websocket password.
    """
    out = {
        "ok": True,
        "live": False,
        "recording": False,
        "scene": None,
        "title": CONFIG["stream"]["title"],
        "game": CONFIG["stream"]["game"],
        "twitch": "https://twitch.tv/" + str(CONFIG.get("twitch", {}).get("channel", "dikgun")),
        "obs_connected": False,
    }
    try:
        stream = await OBS.request("GetStreamStatus")
        out["live"] = bool(stream.get("outputActive"))
        out["obs_connected"] = True
        out["uptime_ms"] = stream.get("outputDuration", 0)
    except Exception:  # noqa: BLE001
        return out
    try:
        rec = await OBS.request("GetRecordStatus")
        out["recording"] = bool(rec.get("outputActive"))
    except Exception:  # noqa: BLE001
        pass
    try:
        out["scene"] = (await OBS.request("GetCurrentProgramScene")).get("currentProgramSceneName")
    except Exception:  # noqa: BLE001
        pass
    return out


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
    await _fanout_config()

    result: dict = {"ok": True, "title": CONFIG["stream"]["title"],
                    "game": CONFIG["stream"]["game"], "delivered": []}

    if not _sb_reachable():
        result["ok"] = False
        result["hint"] = (
            "Streamer.bot HTTP server not reachable on 127.0.0.1:7474. "
            "In Streamer.bot: Servers/Clients -> HTTP Server -> Start "
            "(or set Settings -> HTTP Server -> Auto Start so it survives restarts). "
            f"Then create an action named '{STREAMERBOT_ACTION}' containing: "
            "Twitch -> Channel -> Set Title with %dashTitle%, and "
            "Twitch -> Channel -> Set Game with %dashGame%. "
            "Do NOT use %title% / %game% - those are Streamer.bot built-ins and "
            "shadow the arguments we send."
        )
        return result

    payload = {
        "action": {"name": STREAMERBOT_ACTION},
        "args": {"dashTitle": CONFIG["stream"]["title"], "dashGame": CONFIG["stream"]["game"]},
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


# ------------------------------------------------------------------ deadlock panel
# A presentation layer only. Nothing here reads the game: no memory access, no
# injection, no screen sampling. Values arrive from the dashboard or a local
# caller and are pushed to the overlay.
MAX_ITEMS = 12


def _dl() -> dict:
    state = CONFIG.setdefault("deadlock", {})
    for field in ("boon", "souls", "spirit", "gun", "vitality",
                  "kills", "assists", "damage", "damage_total", "damage_life",
                  "damage_in"):
        state.setdefault(field, 0)
    state.setdefault("items", [])
    state.setdefault("player_name", "")
    state.setdefault("match_started", 0)
    return state


async def _fanout_config() -> None:
    """Push the current config to every connected overlay.

    Scenes show the live game/category, so a change made on the dashboard has to
    reach already-open browser sources -- otherwise a scene keeps showing the
    game it was loaded with until OBS refreshes it.
    """
    async with HUB._lock:
        channels = list(HUB.overlays.keys())
    for ch in channels:
        try:
            await HUB.to_overlays(ch, {"kind": "config", "config": CONFIG})
        except Exception:  # noqa: BLE001
            pass


async def _dl_broadcast() -> None:
    await HUB.to_overlays("deadlock", {"kind": "deadlock", "state": _dl(), "ts": time.time()})


@app.post("/api/deadlock/grab")
async def deadlock_grab() -> dict:
    path = await grab_game_frame("manual")
    return {"ok": bool(path), "path": path}


@app.get("/api/deadlock/state")
async def deadlock_state() -> dict:
    return {"ok": True, "state": _dl()}


DL_FIELDS = ("boon", "souls", "spirit", "gun", "vitality",
             "kills", "assists", "damage")


class DeadlockStatsIn(BaseModel):
    boon: int | None = None
    souls: int | None = None
    spirit: int | None = None
    gun: int | None = None
    vitality: int | None = None
    kills: int | None = None
    assists: int | None = None
    damage: int | None = None


class DeadlockIncrementIn(BaseModel):
    field: str
    by: int = 1


@app.post("/api/deadlock/state")
async def deadlock_set(body: DeadlockStatsIn) -> dict:
    state = _dl()
    for field in DL_FIELDS:
        value = getattr(body, field, None)
        if value is not None:
            state[field] = max(0, int(value))
    # boon / souls are the only ones worth showing in config-driven places
    save_config(CONFIG)
    await _dl_broadcast()
    return {"ok": True, "state": state}


@app.post("/api/deadlock/increment")
async def deadlock_increment(body: DeadlockIncrementIn) -> dict:
    """Bump a stat -- this is the deck button path (kill, assist, ...)."""
    if body.field not in DL_FIELDS:
        raise HTTPException(400, f"field must be one of {', '.join(DL_FIELDS)}")
    state = _dl()
    state[body.field] = max(0, int(state.get(body.field, 0)) + int(body.by))
    save_config(CONFIG)
    await _dl_broadcast()
    return {"ok": True, "field": body.field, "value": state[body.field], "state": state}


class DeadlockItemIn(BaseModel):
    name: str
    icon: str | None = None


@app.post("/api/deadlock/item")
async def deadlock_add_item(body: DeadlockItemIn) -> dict:
    state = _dl()
    items = state["items"]
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "item name required")
    items.append({"name": name[:48], "icon": body.icon})
    if len(items) > MAX_ITEMS:
        state["items"] = items[-MAX_ITEMS:]
    save_config(CONFIG)
    await _dl_broadcast()
    return {"ok": True, "items": state["items"]}


@app.delete("/api/deadlock/item")
async def deadlock_pop_item() -> dict:
    state = _dl()
    if state["items"]:
        state["items"].pop()
        save_config(CONFIG)
        await _dl_broadcast()
    return {"ok": True, "items": state["items"]}


@app.post("/api/deadlock/reset")
async def deadlock_reset() -> dict:
    prev = _dl()
    CONFIG["deadlock"] = {"boon": 0, "souls": 0, "spirit": 0, "gun": 0,
                          "vitality": 0, "kills": 0, "assists": 0, "damage": 0,
                          "damage_total": 0, "damage_life": 0, "damage_in": 0,
                          "last_deaths": None,
                          "items": [],
                          # keep identity, start a fresh win/loss session now
                          "account_id": prev.get("account_id", 0),
                          "session_start": int(time.time()),
                          "wins": 0, "losses": 0, "last_result": None}
    save_config(CONFIG)
    await _dl_broadcast()
    return {"ok": True, "state": CONFIG["deadlock"]}


# --- session win/loss via the public Deadlock API -------------------------
# The game exposes nothing locally, so this uses deadlock-api.com's match
# history. It is a read of PUBLIC match records -- no memory, no injection,
# no screen sampling. Win is encoded as match_result == player_team (verified
# against player_match_outcome across 732 matches).
DEADLOCK_API = "https://api.deadlock-api.com"


def _fetch_match_history(account_id: int) -> list[dict]:
    import urllib.request

    req = urllib.request.Request(
        f"{DEADLOCK_API}/v1/players/{account_id}/match-history",
        headers={"User-Agent": "dikgun-overlay/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def _fetch_steam_profile(account_id: int) -> dict | None:
    """Persona name from the Deadlock API (no Steam Web API key needed)."""
    import urllib.request

    req = urllib.request.Request(
        f"{DEADLOCK_API}/v1/players/steam?account_ids={account_id}",
        headers={"User-Agent": "dikgun-overlay/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    return data[0] if isinstance(data, list) and data else None


def _tally_session(history: list[dict], session_start: float) -> dict:
    wins = losses = 0
    last = None
    for m in sorted(history, key=lambda x: x.get("start_time", 0)):
        if float(m.get("start_time") or 0) < session_start:
            continue
        won = m.get("match_result") == m.get("player_team")
        if won:
            wins += 1
        else:
            losses += 1
        last = {
            "match_id": m.get("match_id"),
            "won": won,
            "kills": m.get("player_kills"),
            "deaths": m.get("player_deaths"),
            "assists": m.get("player_assists"),
            "start_time": m.get("start_time"),
        }
    return {"wins": wins, "losses": losses, "last_result": last}


async def dl_sync() -> dict:
    state = _dl()
    account_id = int(state.get("account_id") or 0)
    if not account_id:
        return {"ok": False, "error": "no account_id set"}
    try:
        history = await asyncio.to_thread(_fetch_match_history, account_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    # The scoreboard shows the Steam persona name, so resolve it from the API
    # rather than making anyone type it in.
    if not state.get("player_name"):
        try:
            profile = await asyncio.to_thread(_fetch_steam_profile, account_id)
            if profile and profile.get("personaname"):
                state["player_name"] = profile["personaname"]
        except Exception:  # noqa: BLE001
            pass

    start = float(state.get("session_start") or 0)
    tally = _tally_session(history, start)
    state.update(tally)
    save_config(CONFIG)
    await _dl_broadcast()
    return {"ok": True, "state": state, "matches_total": len(history)}


class DeadlockSyncIn(BaseModel):
    account_id: int | None = None
    reset_session: bool = False


@app.post("/api/deadlock/sync")
async def deadlock_sync(body: DeadlockSyncIn) -> dict:
    state = _dl()
    if body.account_id is not None:
        state["account_id"] = int(body.account_id)
    if body.reset_session or not state.get("session_start"):
        state["session_start"] = int(time.time())
    return await dl_sync()


async def _dl_poll_loop() -> None:
    """Refresh session win/loss in the background; match history only changes
    when a game ends, so a slow poll is plenty."""
    await asyncio.sleep(8)
    while True:
        try:
            if int(_dl().get("account_id") or 0):
                await dl_sync()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(120)


# ------------------------------------------------------------------ overlay clear
class ClearIn(BaseModel):
    channel: str = "all"          # chat | topic | alerts | all


@app.post("/api/overlay/clear")
async def overlay_clear(body: ClearIn) -> dict:
    """Wipe stale content from live overlays without reloading OBS sources.

    Matters for the vertical canvases: their browser sources cannot be reached
    through obs-websocket, so broadcasting is the only way to clear them.
    """
    channels = ["chat", "topic", "alerts"] if body.channel == "all" else [body.channel]
    if "topic" in channels:
        CONFIG["topic"] = {"label": "Now Playing", "text": ""}
        save_config(CONFIG)

    delivered: dict[str, int] = {}
    for ch in channels:
        delivered[ch] = await HUB.to_overlays(ch, {"kind": "clear", "channel": ch,
                                                   "ts": time.time()})
    # Overlay pages that were already open before the 'clear' handler existed will
    # ignore that message, so also re-push config -- every overlay already knows
    # how to apply it, and for topic an empty text hides the card.
    for ch in channels:
        await HUB.to_overlays(ch, {"kind": "config", "config": CONFIG})
    return {"ok": True, "cleared": channels, "delivered": delivered}


# ------------------------------------------------------------------ input overlay
@app.get("/api/input/state")
async def input_state() -> dict:
    """Current held keys, used by the overlay to seed itself on load."""
    return {"available": _INPUT is not None,
            "pressed": _INPUT.snapshot() if _INPUT else {}}


class InputSimIn(BaseModel):
    key: str
    down: bool = True


@app.post("/api/input/simulate")
async def input_simulate(body: InputSimIn) -> dict:
    """Push a synthetic key event, for testing the overlay without a keyboard."""
    await HUB.to_overlays("input", {"kind": "input", "type": "key",
                                    "key": body.key, "down": body.down, "ts": time.time()})
    return {"ok": True, "key": body.key, "down": body.down}


# --------------------------------------------------------------- twitch catalog
# Twitch's own web GQL endpoint. Needs no user token and no app registration, so
# the dashboard can search categories with zero setup -- unlike Helix, which
# would require a client id/secret and an app access token.
# Trade-off: unofficial. If Twitch reshapes the schema, only this query changes.
_GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_GQL_URL = "https://gql.twitch.tv/gql"
_CATEGORY_QUERY = """
query SearchCategories($query: String!) {
  searchFor(userQuery: $query, platform: "web", target: null) {
    games { edges { item { ... on Game { id name displayName boxArtURL } } } }
  }
}
"""


def _category_search_sync(query: str, limit: int = 12) -> list[dict]:
    import urllib.request

    payload = json.dumps({
        "operationName": "SearchCategories",
        "variables": {"query": query},
        "query": _CATEGORY_QUERY,
    }).encode()
    req = urllib.request.Request(
        _GQL_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json", "Client-ID": _GQL_CLIENT_ID,
                 "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())

    edges = (((data.get("data") or {}).get("searchFor") or {}).get("games") or {}).get("edges") or []
    out: list[dict] = []
    for edge in edges[:limit]:
        item = (edge or {}).get("item") or {}
        if not item.get("displayName"):
            continue
        art = (item.get("boxArtURL") or "")
        art = art.replace("{width}", "72").replace("{height}", "96")
        out.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "display": item.get("displayName"),
            "boxArt": art,
        })
    return out


# ------------------------------------------------------------------- viewers
_VIEWERS_QUERY = """
query($login:String!) {
  user(login: $login) {
    displayName
    stream { viewersCount game { name } }
  }
}
"""


def _twitch_viewers_sync(login: str) -> dict:
    """Live viewer count straight from Twitch's public GraphQL.

    No OAuth: this is the same unauthenticated endpoint the category search uses,
    and it is what the web player itself calls.
    """
    import urllib.request

    payload = json.dumps({
        "query": _VIEWERS_QUERY,
        "variables": {"login": login},
    }).encode()
    req = urllib.request.Request(
        _GQL_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Client-ID": _GQL_CLIENT_ID,
                 "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    user = ((data.get("data") or {}).get("user") or {})
    stream = user.get("stream") or {}
    if not stream:
        return {"live": False, "viewers": 0, "game": None}
    return {
        "live": True,
        "viewers": stream.get("viewersCount"),
        "game": ((stream.get("game") or {}).get("name")),
    }


def _youtube_viewers_sync(video_id: str, api_key: str) -> dict:
    """Concurrent viewers for a YouTube live video.

    Needs `youtube.videoId` and `viewers.youtubeApiKey` in the config. Without
    them there is no unauthenticated way to read this - the public page does not
    include the numbers for an unlisted live stream.
    """
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode({
        "part": "liveStreamingDetails,snippet",
        "id": video_id,
        "key": api_key,
    })
    url = f"https://www.googleapis.com/youtube/v3/videos?{params}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode())

    items = data.get("items") or []
    if not items:
        return {"live": False, "viewers": 0, "title": None}
    details = items[0].get("liveStreamingDetails") or {}
    viewers = details.get("concurrentViewers")
    return {
        "live": viewers is not None,
        "viewers": int(viewers) if viewers is not None else None,
        "title": (items[0].get("snippet") or {}).get("title"),
    }


@app.get("/api/viewers")
async def api_viewers() -> dict:
    """How many people are watching, by source.

    Each entry carries a `state` so the UI can tell "nobody is watching" apart
    from "we cannot know": a monitor that shows 0 when it simply failed is worse
    than one that admits it does not know.
    """
    cfg = CONFIG.get("viewers") or {}
    twitch_cfg = CONFIG.get("twitch") or {}
    yt_cfg = CONFIG.get("youtube") or {}

    sources = []

    # ---- Twitch
    login = str(twitch_cfg.get("channel") or "").strip()
    if login:
        try:
            tw = await asyncio.to_thread(_twitch_viewers_sync, login)
            sources.append({
                "id": "twitch",
                "label": "TWITCH",
                "live": tw["live"],
                "viewers": tw["viewers"],
                "state": "ok" if tw["live"] else "offline",
                "game": tw["game"],
            })
        except Exception as exc:  # noqa: BLE001
            sources.append({"id": "twitch", "label": "TWITCH", "live": None,
                            "viewers": None, "state": "error",
                            "detail": str(exc)[:120]})
    else:
        sources.append({"id": "twitch", "label": "TWITCH", "live": None,
                        "viewers": None, "state": "unconfigured"})

    # ---- YouTube (horizontal + vertical)
    api_key = str(cfg.get("youtubeApiKey") or "").strip()
    videos = {
        "youtube": ("YOUTUBE", yt_cfg.get("videoId")),
        "vertical": ("VERTICAL", yt_cfg.get("verticalVideoId")),
    }
    for key, (label, vid) in videos.items():
        vid = str(vid or "").strip()
        if not api_key:
            sources.append({"id": key, "label": label, "live": None,
                            "viewers": None, "state": "no-api-key"})
        elif not vid:
            sources.append({"id": key, "label": label, "live": None,
                            "viewers": None, "state": "no-video-id"})
        else:
            try:
                yt = await asyncio.to_thread(_youtube_viewers_sync, vid, api_key)
                sources.append({
                    "id": key, "label": label, "live": yt["live"],
                    "viewers": yt["viewers"],
                    "state": "ok" if yt["live"] else "offline",
                })
            except Exception as exc:  # noqa: BLE001
                sources.append({"id": key, "label": label, "live": None,
                                "viewers": None, "state": "error",
                                "detail": str(exc)[:120]})

    known = [x["viewers"] for x in sources
             if x["state"] == "ok" and isinstance(x.get("viewers"), int)]
    return {
        "ok": True,
        "sources": sources,
        "total": sum(known) if known else None,
        "known": len(known),
    }


@app.get("/api/twitch/categories")
async def twitch_categories(q: str = "") -> dict:
    """Search Twitch categories. The name returned is what Twitch itself calls it,
    which is what makes the category actually land in the right bucket."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"ok": True, "query": q, "results": []}
    try:
        results = await asyncio.to_thread(_category_search_sync, q)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"category search failed: {exc}") from exc
    return {"ok": True, "query": q, "results": results}


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
