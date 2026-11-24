"""Automatic clip capture driven by the kill identifiers we already have.

The problem it solves
---------------------
Saving the replay buffer by hand either catches nothing or produces a 60 second
file with one kill buried in it. This watches the game capture, notices kills,
groups them into events, and saves only when an event closes - then trims the
saved file down to the event.

Grouping rule (the point of the whole thing)
-------------------------------------------
Kills are clustered by GAP, not by count:

    3 kills inside 10s   -> one event  -> one clip
    3 kills 2 minutes apart -> three events -> three clips

A cluster stays open while kills keep arriving within `cluster_gap` seconds of
each other, and closes once that much silence passes. It also closes early at
`max_cluster` seconds so a long fight cannot produce an unwatchable clip.

Why the trim happens after the save
-----------------------------------
OBS writes the whole buffer, and the buffer length is whatever OBS is configured
for (60s here). The event's kill times are mapped back onto that file, then
ffmpeg keeps `pre` seconds before the first kill and `post` seconds after the
last - deliberately more lead-in than tail, so the clip shows the approach and
does not sit on a menu afterwards.

Detection per game, reusing what exists
---------------------------------------
    apex, bf6, finals  ->  marker_scan.ocr_hit()  (region.json + text.json + OCR)
    deadlock           ->  deadlock_hud.find_kill_badge()  (template match)
    anything else      ->  no detection; the watcher stays idle

The game comes from the OBS program scene (`GAME_Apex` -> apex), so switching
scenes switches the logic with no extra input.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import logging

log = logging.getLogger("autoclip")

FFMPEG = "ffmpeg"
AUTO_CLIP_DIRNAME = "AutoClips"

# Live detectors are TEMPLATE based, not OCR. Measured on this machine:
#   ocr_hit()            800-2700 ms per frame   -> cannot poll
#   template match          2.2 ms per frame     -> 400x faster
#   GetSourceScreenshot    22 ms at 1920x1080, 43 KB payload
# so a full poll is ~25 ms and 4-10 Hz is comfortable.
#
# Each entry: (ref png, mask png, search_x, search_y, search_w, search_h).
# The SEARCH band is wider than the template on purpose: Apex centres the kill
# banner, so the "ELIMINATED" word slides horizontally with the player name. A
# tight box around the template only matched names of one length - measured 6 of
# 15 kills detected. Searching the whole band lets matchTemplate slide.
LIVE_TEMPLATES = {
    "apex": ("live_ref.png", "live_mask.png", 520, 730, 900, 90),
}

# program scene name -> detector key, matched case-insensitively by substring
SCENE_GAMES = {
    "game_apex": "apex",
    "game_deadlock": "deadlock",
    "game_bf6": "bf6",
    "game_finals": "finals",
}

DEFAULTS = {
    "enabled": False,
    "poll_ms": 300,            # ~3 polls/sec; a 2s banner gets ~6 looks
    "shot_width": 1920,        # source resolution, so crop coords stay exact
    "hit_debounce": 1.4,       # ignore repeat detections of the same banner
    "cluster_gap": 6.0,        # measured: 12s chained 18 kills into one 75s blob
    "max_cluster": 15.0,       # hard cap on a single event
    "pre": 4.0,                # seconds kept before the first kill
    "post": 1.5,               # seconds kept after the last kill
    "min_hits": 1,             # kills required before an event is worth a clip
    "keep_raw": False,         # keep the untrimmed buffer save
    "out_dir": "",             # default: <Videos>/AutoClips
    "threshold": 0.45,         # template gate: kill frames measured 0.59-0.98
    "confirm_ocr": True,       # OCR-confirm a candidate before counting the kill
    "confirm_threshold": 0.5,  # ocr_hit() is 1.0 or 0.0 in practice
}


@dataclass
class Event:
    hits: list[float] = field(default_factory=list)     # monotonic timestamps
    started: float = 0.0

    @property
    def closed_at(self) -> float:
        return self.hits[-1] if self.hits else self.started


def scene_to_game(scene_name: str) -> str | None:
    """`GAME_Apex` -> 'apex'. Returns None for non-game scenes."""
    low = (scene_name or "").lower()
    for key, game in SCENE_GAMES.items():
        if key in low:
            return game
    return None


def trim_window(hits: list[float], saved_at: float, buffer_secs: float,
                pre: float, post: float, file_dur: float) -> tuple[float, float]:
    """Map monotonic kill times onto the saved file and return (start, end).

    The saved buffer covers [saved_at - buffer_secs, saved_at], so a kill at `k`
    sits at `buffer_secs - (saved_at - k)` seconds into the file. Clamped to the
    file, because OBS often writes slightly less than the configured buffer.
    """
    starts = [buffer_secs - (saved_at - k) for k in hits]
    first = max(0.0, min(starts) - pre)
    last = min(file_dur, max(starts) + post)
    if last - first < 0.8:                       # degenerate, keep something
        last = min(file_dur, first + 0.8)
    return round(first, 3), round(last, 3)


class AutoClipper:
    """Watches OBS, detects kills, saves and trims clips."""

    def __init__(self, cfg: dict, obs, on_event=None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.obs = obs
        self.on_event = on_event
        self.event: Event | None = None
        self.last_hit = 0.0
        self.hits_total = 0
        self.clips: list[dict] = []
        self.status = "idle"
        self.last_error: str | None = None
        self._pending_save: dict | None = None
        self._confirming = False
        self._task: asyncio.Task | None = None

    # ---------------------------------------------------------------- status
    def state(self) -> dict:
        return {
            "enabled": bool(self.cfg.get("enabled")),
            "status": self.status,
            "game": getattr(self, "game", None),
            "hits_total": self.hits_total,
            "event_hits": len(self.event.hits) if self.event else 0,
            "clips": self.clips[-10:],
            "last_error": self.last_error,
            "config": {k: self.cfg.get(k) for k in
                       ("poll_ms", "cluster_gap", "max_cluster", "pre", "post",
                        "min_hits", "keep_raw", "threshold")},
        }

    def _emit(self) -> None:
        if self.on_event:
            try:
                self.on_event(self.state())
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- detection
    async def _screenshot(self, source: str) -> bytes | None:
        try:
            r = await self.obs.request("GetSourceScreenshot", {
                "sourceName": source,
                "imageFormat": "jpg",
                "imageWidth": int(self.cfg["shot_width"]),
                "imageCompressionQuality": 85,
            })
            data = r.get("imageData") or ""
            if "," in data:
                data = data.split(",", 1)[1]
            import base64
            return base64.b64decode(data)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"screenshot: {exc}"
            return None

    def _live_template(self, game: str):
        """Load and cache (ref, mask, search band) for a game, or None."""
        if not hasattr(self, "_tpl_cache"):
            self._tpl_cache = {}
        if game in self._tpl_cache:
            return self._tpl_cache[game]
        spec = LIVE_TEMPLATES.get(game)
        if not spec:
            self._tpl_cache[game] = None
            return None
        import cv2
        import numpy as np
        ref_name, mask_name, sx, sy, sw, sh = spec
        base = Path(os.path.dirname(os.path.abspath(__file__))).parent
        root = base / "streamer-mode" / "assets" / "kill" / game
        ref_p, mask_p = root / ref_name, root / mask_name
        if not (ref_p.is_file() and mask_p.is_file()):
            log.warning("live template missing for %s: %s", game, ref_p)
            self._tpl_cache[game] = None
            return None
        ref = cv2.imread(str(ref_p), cv2.IMREAD_COLOR)
        mask = cv2.imread(str(mask_p), cv2.IMREAD_GRAYSCALE)
        self._tpl_cache[game] = (ref, mask, sx, sy, sw, sh)
        return self._tpl_cache[game]

    def _detect_template(self, frame, game: str) -> float:
        """Score by masked template match. ~2ms, which is the whole point."""
        import cv2
        spec = self._live_template(game)
        if spec is None:
            return 0.0
        ref, mask, sx, sy, sw, sh = spec
        y0, y1 = max(0, sy), min(frame.shape[0], sy + sh)
        x0, x1 = max(0, sx), min(frame.shape[1], sx + sw)
        region = frame[y0:y1, x0:x1]
        if region.shape[0] < ref.shape[0] or region.shape[1] < ref.shape[1]:
            return 0.0
        r = cv2.matchTemplate(region, ref, cv2.TM_CCOEFF_NORMED, mask=mask)
        return float(r.max())

    def _detect(self, game: str, raw: bytes) -> float:
        """Score one frame. Reuses the offline scanners rather than new code."""
        import numpy as np
        import cv2

        arr = np.frombuffer(raw, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return 0.0

        # fast path first: template games never touch OCR
        if game in LIVE_TEMPLATES:
            return self._detect_template(frame, game)

        if game == "deadlock":
            try:
                from deadlock_hud import find_kill_badge
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t:
                    tmp = t.name
                cv2.imwrite(tmp, frame)
                try:
                    return float(find_kill_badge(tmp))
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"deadlock detect: {exc}"
                return 0.0

        try:
            import marker_scan
            return float(marker_scan.ocr_hit(frame))
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{game} detect: {exc}"
            return 0.0

    # -------------------------------------------------------------- the loop
    async def run(self) -> None:
        self.status = "running"
        while True:
            try:
                if not self.cfg.get("enabled"):
                    self.status = "disabled"
                    await asyncio.sleep(1.0)
                    continue
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                self.status = "error"
                log.warning("autoclip tick failed: %s", exc)
                await asyncio.sleep(2.0)
            await asyncio.sleep(max(0.15, int(self.cfg["poll_ms"]) / 1000.0))

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="autoclip")

    async def _tick(self) -> None:
        scene = (await self.obs.request("GetCurrentProgramScene", {})).get(
            "currentProgramSceneName", "")
        game = scene_to_game(scene)
        self.game = game
        if not game:
            self.status = "idle (not a game scene)"
            await self._maybe_close(time.monotonic())
            return

        source = f"SRC_Capture_{game.capitalize()}"
        # the Apex/BF6/Finals captures are named SRC_Capture_Apex etc; Deadlock
        # likewise. If the guess is wrong, fall back to the first capture that
        # mentions the game.
        names = await self._capture_sources()
        match = next((n for n in names if n.lower().endswith(game)), None)
        if match:
            source = match
        elif source not in names:
            self.status = f"no capture source for {game}"
            return

        raw = await self._screenshot(source)
        if raw is None:
            self.status = "screenshot failed"
            return

        if game not in LIVE_TEMPLATES and game != "deadlock":
            self.status = f"{game}: no live template yet (OCR too slow to poll)"
            await self._maybe_close(time.monotonic())
            return

        score = self._detect(game, raw)
        now = time.monotonic()
        if score >= float(self.cfg["threshold"]):
            if now - self.last_hit >= float(self.cfg["hit_debounce"]):
                self.last_hit = now
                # The template is fast but not perfectly specific, and OCR is
                # accurate but costs ~0.8s. So the template gates and OCR only
                # runs on candidates, off the poll loop - a handful of times per
                # match rather than every 300ms.
                if self.cfg.get("confirm_ocr") and not self._confirming:
                    self._confirming = True
                    self.status = f"{game}: candidate, confirming"
                    asyncio.ensure_future(self._confirm(game, raw, now))
                    return
                self.hits_total += 1
                if self.event is None:
                    self.event = Event(started=now)
                self.event.hits.append(now)
                self.status = f"{game}: {len(self.event.hits)} kill(s) in event"
                self._emit()
                if now - self.event.started >= float(self.cfg["max_cluster"]):
                    await self._close_event(now, "max cluster")
                return

        await self._maybe_close(now)

    async def _confirm(self, game: str, raw: bytes, seen_at: float) -> None:
        """OCR the candidate frame off the poll loop, then accept or drop it."""
        try:
            score = await asyncio.to_thread(self._ocr_score, game, raw)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"ocr confirm: {exc}"
            score = 0.0
        finally:
            self._confirming = False
        if score < float(self.cfg.get("confirm_threshold", 0.5)):
            self.status = f"{game}: candidate rejected by OCR"
            self._emit()
            return
        self.hits_total += 1
        if self.event is None:
            self.event = Event(started=seen_at)
        self.event.hits.append(seen_at)
        self.status = f"{game}: {len(self.event.hits)} kill(s) in event (OCR confirmed)"
        self._emit()
        if seen_at - self.event.started >= float(self.cfg["max_cluster"]):
            await self._close_event(seen_at, "max cluster")

    def _ocr_score(self, game: str, raw: bytes) -> float:
        import numpy as np
        import cv2
        import marker_scan
        arr = np.frombuffer(raw, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return 0.0
        marker_scan.load(game)
        return float(marker_scan.ocr_hit(frame))

    async def _capture_sources(self) -> list[str]:
        try:
            inputs = await self.obs.request("GetInputList", {})
            return [i["inputName"] for i in inputs.get("inputs", [])
                    if i.get("inputKind") == "game_capture"]
        except Exception:  # noqa: BLE001
            return []

    async def _maybe_close(self, now: float) -> None:
        if not self.event:
            return
        if now - self.event.closed_at >= float(self.cfg["cluster_gap"]):
            await self._close_event(now, "gap")

    async def _close_event(self, now: float, why: str) -> None:
        ev, self.event = self.event, None
        if not ev or len(ev.hits) < int(self.cfg["min_hits"]):
            self.status = "event discarded (too few kills)"
            self._emit()
            return
        self.status = f"saving clip ({len(ev.hits)} kills, closed by {why})"
        self._emit()
        # remember the event so ReplayBufferSaved can trim it
        self._pending_save = {"hits": list(ev.hits), "saved_at": None}
        try:
            await self.obs.request("SaveReplayBuffer", {})
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"SaveReplayBuffer: {exc}"
            self.status = "save failed - is the replay buffer started?"
            self._pending_save = None
            self._emit()

    # --------------------------------------------------------- after the save
    async def on_replay_saved(self, path: str) -> None:
        """Called by the OBS event handler with the saved buffer path."""
        pend, self._pending_save = self._pending_save, None
        if not pend:
            log.info("replay saved with no pending event: %s", path)
            return
        saved_at = time.monotonic()
        try:
            buf = await self._buffer_seconds()
            dur = self._file_duration(path)
            start, end = trim_window(pend["hits"], saved_at, buf,
                                     float(self.cfg["pre"]), float(self.cfg["post"]),
                                     dur)
            out = self._out_path(path, len(pend["hits"]))
            ok = self._trim(path, out, start, end)
            if ok:
                # Front-load what we already know: kill offsets inside the trimmed
                # clip. Writing them here means the promo pipeline (and any future
                # scan) never has to re-detect them from scratch.
                try:
                    starts = [buf - (saved_at - k) for k in pend["hits"]]
                    span = max(0.0, end - start)
                    offsets = sorted({round(x - start, 2) for x in starts
                                      if 0.0 <= (x - start) <= span})
                    side = {
                        "game": getattr(self, "game", None),
                        "kills": offsets,
                        "duration": round(span, 2),
                        "captured": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "source": "replaybuffer",
                        "raw": os.path.basename(str(path)),
                    }
                    out.with_suffix(".json").write_text(
                        json.dumps(side, indent=2), encoding="utf-8")
                except Exception as exc:  # noqa: BLE001
                    log.warning("sidecar write failed: %s", exc)
                rec = {"path": str(out), "kills": len(pend["hits"]),
                       "start": start, "end": round(end - start, 2),
                       "at": time.strftime("%H:%M:%S")}
                self.clips.append(rec)
                self.status = f"clip saved: {out.name} ({rec['end']}s, {rec['kills']} kills)"
                if not self.cfg.get("keep_raw"):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            else:
                self.status = "trim failed; raw save kept"
                self.last_error = "ffmpeg trim failed"
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"post-save: {exc}"
            self.status = "post-save failed"
        self._emit()

    async def _buffer_seconds(self) -> float:
        try:
            r = await self.obs.request("GetProfileParameter", {
                "parameterCategory": "SimpleOutput", "parameterName": "RecRBTime"})
            return float(r.get("parameterValue") or 60)
        except Exception:  # noqa: BLE001
            return 60.0

    @staticmethod
    def _file_duration(path: str) -> float:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60).stdout.strip()
        try:
            return float(out or 0) or 60.0
        except ValueError:
            return 60.0

    def _out_path(self, raw_path: str, kills: int) -> Path:
        base = self.cfg.get("out_dir") or os.path.join(
            os.path.expanduser("~"), "Videos", AUTO_CLIP_DIRNAME)
        d = Path(base)
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return d / f"{getattr(self, 'game', 'game')}_{kills}k_{stamp}.mp4"

    def _trim(self, src: str, out: Path, start: float, end: float) -> bool:
        r = subprocess.run(
            [FFMPEG, "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", src,
             "-t", f"{max(0.5, end - start):.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
            capture_output=True, text=True, timeout=600)
        return r.returncode == 0 and out.is_file()
