"""Clip library: index local gameplay recordings and expose them to overlays.

The recordings live in per-game folders under the videos directory. This module
turns that folder tree into a flat, stable index that overlays can consume:

    {"id": "...", "game": "Apex Legends", "title": "...", "file": "...",
     "rel": "Apex Legends/....mp4", "bytes": 718000000, "duration": 1234.5}

Folder names are normalised because the filesystem has several spellings of the
same game ("R6siege", "Tom Clancy's Rainbow Six  Siege").
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}

# Folders that clearly are not games, or are duplicates of one.
_NON_GAME = {
    "captures", "desktop", "backup_2025-01-06_17-56-17", "reorganized_files",
    "ea launcher", "nvidia", "unreal crash report client", "rustdesk",
    "microsoft powershell ise", "134.0.3124.83",
}

# Canonical display names for the messier folders.
_GAME_ALIASES = {
    "r6siege": "Rainbow Six Siege",
    "tom clancy's rainbow six  siege": "Rainbow Six Siege",
    "tom clancy's rainbow six siege": "Rainbow Six Siege",
    "hunt  showdown": "Hunt: Showdown",
    "hunt showdown": "Hunt: Showdown",
    "hunt  showdown easy anti-cheat launcher": "Hunt: Showdown",
    "playerunknown's battlegrounds": "PUBG: Battlegrounds",
    "counter-strike 2": "Counter-Strike 2",
    "kingdom hearts -hd 1.5+2.5 remix-": "Kingdom Hearts HD 1.5+2.5",
    "kingdom hearts hd 1.5+2.5 remix": "Kingdom Hearts HD 1.5+2.5",
    "b1": "Battlefield 1",
    "repo": "R.E.P.O.",
    "the finals": "The Finals",
    "deep rock galactic": "Deep Rock Galactic",
    "hell let loose": "Hell Let Loose",
    "dayz": "DayZ",
    "valorant": "VALORANT",
    "xdefiant": "XDefiant",
    "just cause 3": "Just Cause 3",
    "killing floor 2": "Killing Floor 2",
    "no man's sky": "No Man's Sky",
    "sid meier's civilization vi": "Civilization VI",
    "titanfall 2": "Titanfall 2",
    "assassin's creed  syndicate": "Assassin's Creed Syndicate",
    "among us": "Among Us",
    "battlefield 1": "Battlefield 1",
    "battlefield 2042": "Battlefield 2042",
    "apex legends": "Apex Legends",
}

_lock = threading.Lock()
_cache: list[dict] | None = None
_cache_at = 0.0
_duration_cache: dict[str, float] = {}
_duration_loading = False
CACHE_TTL = 300.0


def _clean_game(folder: str) -> str:
    key = folder.strip().lower()
    key = re.sub(r"\s+", " ", key)
    if key in _GAME_ALIASES:
        return _GAME_ALIASES[key]
    # titlecase the folder, collapsing the doubled spaces the filesystem has
    return re.sub(r"\s+", " ", folder).strip()


def _clean_title(stem: str, game: str) -> str:
    """Pull a readable title out of names like
    'Apex Legends 2023.12.12 - 07.55.04.17.DVR'."""
    s = stem.replace(".DVR", "")
    # drop the leading game name
    if s.lower().startswith(game.lower()):
        s = s[len(game):]
    s = s.strip(" -_")
    # '2023.12.12 - 07.55.04.17' -> '2023-12-12 07:55'
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})\s*-\s*(\d{2})\.(\d{2})", s)
    if m:
        y, mo, d, hh, mm = m.groups()
        return f"{y}-{mo}-{d} {hh}:{mm}"
    return s or stem


def _probe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode == 0:
            return float(out.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return None


def _load_duration_cache(cache_file: Path) -> None:
    global _duration_cache
    try:
        if cache_file.is_file():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _duration_cache = {k: float(v) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        _duration_cache = {}


def _save_duration_cache(cache_file: Path) -> None:
    try:
        cache_file.write_text(json.dumps(_duration_cache, indent=0), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _fill_durations(paths: list[tuple[str, Path]], cache_file: Path) -> None:
    """Probe durations in the background; the overlay works without them."""
    changed = False
    for rel, path in paths:
        if rel in _duration_cache:
            continue
        d = _probe_duration(path)
        if d:
            _duration_cache[rel] = round(d, 2)
            changed = True
    if changed:
        _save_duration_cache(cache_file)


def scan(root: Path, cache_file: Path | None = None) -> list[dict]:
    """Walk the videos tree and return the clip index.

    Cheap enough to call directly, but results are memoised for a few minutes.
    """
    global _cache, _cache_at

    with _lock:
        if _cache is not None and (time.time() - _cache_at) < CACHE_TTL:
            return _cache

    clips: list[dict] = []
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            rel_dir = d.relative_to(root)
            parts = rel_dir.parts
            folder = parts[0] if parts else d.name
            if folder.lower() in _NON_GAME:
                continue
            for fn in filenames:
                p = d / fn
                if p.suffix.lower() not in VIDEO_EXTS:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                game = _clean_game(folder)
                rel = str(p.relative_to(root)).replace("\\", "/")
                cid = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]
                clips.append({
                    "id": cid,
                    "game": game,
                    "title": _clean_title(p.stem, game),
                    "file": fn,
                    "rel": rel,
                    "bytes": st.st_size,
                    "mtime": int(st.st_mtime),
                })

    # newest first: a "recent clips" panel is the common case
    clips.sort(key=lambda c: c["mtime"], reverse=True)

    if cache_file is not None:
        _load_duration_cache(cache_file)
        for c in clips:
            d = _duration_cache.get(c["rel"])
            if d:
                c["duration"] = d

    with _lock:
        _cache = clips
        _cache_at = time.time()
    return clips


def start_duration_fill(root: Path, cache_file: Path) -> None:
    """Probe any missing durations once, in the background."""
    global _duration_loading
    if _duration_loading:
        return
    _duration_loading = True

    def run() -> None:
        global _duration_loading
        try:
            clips = scan(root, cache_file)
            pending = [(c["rel"], root / c["rel"]) for c in clips
                       if c["rel"] not in _duration_cache]
            _fill_durations(pending, cache_file)
        finally:
            _duration_loading = False

    threading.Thread(target=run, daemon=True, name="clip-durations").start()


def games(clips: list[dict]) -> list[dict]:
    """Group counts by game, for the dashboard/player to offer filters."""
    out: dict[str, int] = {}
    for c in clips:
        out[c["game"]] = out.get(c["game"], 0) + 1
    return [{"game": g, "count": n} for g, n in sorted(out.items())]
