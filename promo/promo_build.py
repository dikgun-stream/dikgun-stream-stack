"""Build a ~1 minute promo from the clip library and a music track.

    python promo_build.py --music song.mp3 --out promo.mp4
    python promo_build.py --music song.mp3 --games "Rainbow Six Siege,Deadlock" --duration 60
    python promo_build.py --music song.mp3 --dry-run

How it decides what to show
---------------------------
1. Analyse the track: onsets, low-band kicks, and a short-window RMS envelope.
2. Split it into loud and quiet sections. Quiet = build-up, loud = drop.
3. Build-up gets continuous gameplay - the longest uninterrupted stretch from
   whichever kill window is nearest, so it reads as raw play rather than a
   montage.
4. Drops get one kill per beat, cut to the nearest onset. Measured on the two
   existing edits, cuts land on ANY musical onset far more often than on kicks
   specifically (61% and 57% within 80ms of an onset, versus 35% and 21% on
   kicks), so onsets drive the cut grid and kicks only mark the strongest beats.
5. Game audio is kept but attenuated, because "lowered in game audio" still wants
   the shots and footsteps under the music.

Why two passes
--------------
A single filter graph covering ~60 segments of hundreds of MB each is slow and
fragile. Segments are extracted to intermediate files first, concatenated, then
the music is mixed in. Re-running a bad segment is then a one-line fix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

STING = str(HERE / "stingers")   # Blender letter renders
FFMPEG = r"C:\ffmpeg\ffmpeg.exe"
FFPROBE = r"C:\ffmpeg\ffprobe.exe"

SR = 22050
HOP = 512
N_FFT = 2048

OUT_W, OUT_H, OUT_FPS = 1920, 1080, 60
GAME_AUDIO_DB = -18.0      # "lowered in game audio"


# --------------------------------------------------------------------- audio
def load_audio(path: Path) -> np.ndarray:
    import wave
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
        wav = t.name
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(path),
                    "-ac", "1", "-ar", str(SR), "-f", "wav", wav],
                   capture_output=True, timeout=900)
    with wave.open(wav, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    os.unlink(wav)
    return data.astype(np.float32) / 32768.0


def analyse_music(path: Path) -> dict:
    x = load_audio(path)
    n = 1 + max(0, (len(x) - N_FFT)) // HOP
    window = np.hanning(N_FFT).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    low_mask = freqs < 150.0

    low = np.zeros(n, np.float32)
    flux = np.zeros(n, np.float32)
    rms = np.zeros(n, np.float32)
    prev = None
    for i in range(n):
        chunk = x[i * HOP: i * HOP + N_FFT]
        if len(chunk) < N_FFT:
            break
        mag = np.abs(np.fft.rfft(chunk * window))
        low[i] = float((mag[low_mask] ** 2).sum())
        rms[i] = float(np.sqrt((chunk ** 2).mean()))
        if prev is not None:
            flux[i] = float(np.clip(mag - prev, 0, None).sum())
        prev = mag

    times = np.arange(len(low)) * HOP / SR
    on = _peaks(flux, times, 0.10, 1.30)
    kicks = _peaks(low, times, 0.18, 1.35)

    # section split: a rolling mean of the RMS envelope, cut at its midpoint.
    # Enough to tell a quiet intro from a loud drop without a full music parser.
    win = max(9, int(2.0 * SR / HOP))
    kern = np.ones(win, np.float32) / win
    smooth = np.convolve(rms, kern, mode="same")
    if len(smooth) and smooth.max() > 0:
        mid = float(np.median(smooth[smooth > smooth.max() * 0.2]))
        loud = smooth > mid
    else:
        loud = np.zeros(len(smooth), bool)

    return {
        "duration": float(len(x) / SR),
        "onsets": on, "kicks": kicks, "loud": loud,
        "hop_times": times, "rms": smooth,
    }


def _peaks(signal, times, min_gap, sensitivity):
    if len(signal) < 8:
        return np.array([])
    from numpy.lib.stride_tricks import sliding_window_view
    win = max(9, int(0.5 * SR / HOP) | 1)
    padded = np.pad(signal, win // 2, mode="edge")
    roll = sliding_window_view(padded, win).mean(axis=1)
    mask = signal > roll * sensitivity
    out, last = [], -1e9
    for i in np.flatnonzero(mask):
        if i == 0 or i == len(signal) - 1:
            continue
        if signal[i] < signal[i - 1] or signal[i] < signal[i + 1]:
            continue
        t = float(times[i])
        if t - last < min_gap:
            continue
        out.append(t)
        last = t
    return np.array(out)


# -------------------------------------------------------------------- source
def load_kills(games: list[str]) -> list[dict]:
    """Kill moments from the scanner indexes, restricted to the clip roots."""
    from urllib.request import urlopen

    try:
        with urlopen("http://127.0.0.1:7475/api/clips/highlights?source=all",
                     timeout=60) as r:
            data = json.loads(r.read().decode())
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"overlay server not reachable: {exc}\n"
                         "start it first: cd overlay-server; python -m uvicorn server:app")

    pools: dict[str, list[dict]] = {}
    for h in data.get("highlights", []):
        if h.get("source") not in ("kill", "marker"):
            continue
        game = h.get("game") or "?"
        if games and not any(g.lower() in game.lower() for g in games):
            continue
        for w in h.get("windows") or []:
            if len(w) != 2:
                continue
            pools.setdefault(game, []).append({
                "rel": h["rel"], "mount": h["mount"], "game": game,
                "start": float(w[0]), "end": float(w[1]),
            })
    # round-robin across games: concatenating by game would put 25 seconds of
    # Deadlock back to back before Apex ever appears
    queues = [list(v) for v in pools.values()]
    for q in queues:
        random.shuffle(q)
    out: list[dict] = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop())
    if not out:
        raise SystemExit("no kill windows found for those games")
    return out


ROOTS = {
    "videos": r"C:\Users\JR\Videos",
    "videosq": r"Q:\Game Clips",
}


def resolve(clip: dict) -> Path | None:
    if clip.get("path"):
        p = Path(clip["path"])
        return p if p.is_file() else None
    root = ROOTS.get(clip.get("mount") or "videos")
    if not root:
        return None
    p = Path(root) / str(clip["rel"]).replace("\\", "/")
    return p if p.is_file() else None


# --------------------------------------------------------------------- build
def _atempo_chain(speed: float) -> str:
    """atempo accepts 0.5-2.0 per instance, so extreme ratios are chained."""
    parts = []
    v = float(speed)
    while v > 2.0:
        parts.append(2.0)
        v /= 2.0
    while v < 0.5:
        parts.append(0.5)
        v /= 0.5
    parts.append(v)
    return ",".join(f"atempo={p:.4f}" for p in parts)


CACHE_DIR = HERE / "segcache"
CACHE_TAG = "crf20-veryfast-g3"  # g3: glitch lengthened 4->8 frames, shift 10->16


def stinger_seconds() -> float:
    """Duration of one stinger, from the rendered frame count.

    Exact, and known before anything is encoded - which lets the timing
    compensation run during planning rather than after the encode starts.
    """
    d = Path(STING) / "DIKGUN"
    if not d.is_dir():
        return 0.0
    n = len(list(d.glob("f_*.png")))
    return n / float(OUT_FPS) if n else 0.0


def segment_key(src: Path, start: float, length: float, speed: float,
                src_len: float | None, glitch: bool, heavy: bool) -> str:
    """Identity of a rendered segment.

    Source size AND mtime are included, so re-recording or re-trimming the source
    invalidates the entry instead of silently reusing a stale clip.
    """
    st = src.stat()
    raw = "|".join([
        str(src.resolve()), str(st.st_size), str(int(st.st_mtime)),
        f"{start:.3f}", f"{length:.3f}", f"{speed:.4f}",
        "" if src_len is None else f"{src_len:.3f}",
        "g" if glitch else "-", "h" if heavy else "-", CACHE_TAG,
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def cache_get(key: str, out: Path) -> bool:
    """Hardlink a cached segment into place, falling back to a copy."""
    src = CACHE_DIR / f"{key}.mp4"
    if not src.is_file():
        return False
    try:
        if out.exists():
            out.unlink()
        os.link(src, out)
    except OSError:
        import shutil
        shutil.copy2(src, out)
    return True


def cache_put(key: str, produced: Path) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    dst = CACHE_DIR / f"{key}.mp4"
    if dst.exists():
        return
    try:
        os.link(produced, dst)
    except OSError:
        import shutil
        shutil.copy2(produced, dst)


def _run(cmd: list, timeout: int = 1800) -> bool:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"    ffmpeg: {(r.stderr or '').strip()[:220]}")
    return r.returncode == 0


def extract(src: Path, start: float, length: float, out: Path,
            glitch: bool = False, heavy: bool = False,
            speed: float = 1.0, src_len: float | None = None) -> bool:
    """One cut, normalised to the output frame, with optional glitch and retime.

    Output seeking (-i before -ss) is exact; input seeking lands on the previous
    keyframe and holds it, which shows as a freeze at the head of every cut.

    Glitch and retime are BOTH two-pass, for the same reason: an output seek keeps
    the filtergraph seeing source timestamps, so a frame-index trim or a timeline
    guard inside the graph does not line up with the segment. Cutting plainly
    first gives a clean intermediate that starts at zero.
    """
    base_vf = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
               f"crop={OUT_W}:{OUT_H},fps={OUT_FPS},setsar=1")
    consume = length if src_len is None else src_len
    retime = abs(speed - 1.0) > 1e-6
    do_glitch = bool(glitch or heavy)

    enc_v = ["-c:v", "libx264", "-preset", "veryfast"]
    enc_a = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]

    if not do_glitch and not retime:
        return _run([FFMPEG, "-v", "error", "-y",
                     "-i", str(src),
                     "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, consume):.3f}",
                     "-vf", base_vf, *enc_v, "-crf", "20", *enc_a,
                     "-movflags", "+faststart", str(out)]) and out.is_file()

    # ---- pass 1: a clean cut, generous quality so pass 2 does not stack loss
    raw = out.with_name(out.stem + "_raw" + out.suffix)
    ok = _run([FFMPEG, "-v", "error", "-y",
               "-i", str(src),
               "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, consume):.3f}",
               "-vf", base_vf, *enc_v, "-crf", "14", *enc_a,
               "-movflags", "+faststart", str(raw)])
    if not ok or not raw.is_file():
        return False

    cur = raw
    try:
        # ---- pass 2a: glitch the first frames
        if do_glitch:
            shift = 26 if heavy else 16
            head_n = 6 if heavy else 4
            fx = f"rgbashift=rh={shift}:bh=-{shift}:edge=wrap"
            if heavy:
                fx += ",pixelize=w=48:h=27"
            g_out = out.with_name(out.stem + "_g" + out.suffix)
            fc = (f"[0:v]split=2[gh][gb];"
                  f"[gh]trim=end_frame={head_n},setpts=PTS-STARTPTS,{fx}[g1];"
                  f"[gb]trim=start_frame={head_n},setpts=PTS-STARTPTS[g2];"
                  f"[g1][g2]concat=n=2:v=1:a=0[v]")
            if not _run([FFMPEG, "-v", "error", "-y", "-i", str(cur),
                         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
                         *enc_v, "-crf", "20", *enc_a,
                         "-movflags", "+faststart", str(g_out)]):
                return False
            cur = g_out

        # ---- pass 2b: retime (pins only)
        if retime:
            r_out = out if cur == raw else out
            if not _run([FFMPEG, "-v", "error", "-y", "-i", str(cur),
                         "-vf", f"setpts=PTS/{speed:.6f},fps={OUT_FPS},setsar=1",
                         "-af", _atempo_chain(speed),
                         *enc_v, "-crf", "20", *enc_a,
                         "-movflags", "+faststart", str(r_out)]):
                return False
        else:
            # move the finished file into place
            if cur != out:
                if out.exists():
                    out.unlink()
                os.replace(cur, out)

        return out.is_file() and out.stat().st_size > 2048
    finally:
        for tmp in (raw, out.with_name(out.stem + "_g" + out.suffix)):
            if tmp != out and tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


# server-side padding: the API builds windows as [moment - 5, moment + 3]
PAD_BEFORE = 5.0
KILL_LEAD = 0.30      # start this far before the kill; 0.45 starved the tail
KILL_TAIL = 1.55      # and hold this long, so the kill and its result are shown


def plan(music: dict, kills: list[dict], t0: float, duration: float,
         prefer: list[str] | None = None) -> list[dict]:
    """Decide, section by section, whether to show sustained play or a kill cut."""
    hops = music["hop_times"]
    onsets = music["onsets"]

    # majority-vote the loud/quiet flag over ~1.5s so a drop is a section
    raw_loud = np.asarray(music["loud"], bool)
    span = max(3, int(1.5 * SR / HOP))
    if len(raw_loud) > span:
        kern = np.ones(span, np.float32) / span
        loud = np.convolve(raw_loud.astype(np.float32), kern, mode="same") > 0.5
    else:
        loud = raw_loud

    t_end = t0 + duration

    # ---- section detection: every loud run, not just the first one.
    # A single "first_drop" could not distinguish the real payoff from a brief
    # loud swell, so a false drop got treated as the main event.
    runs, i = [], 0
    while i < len(loud):
        if loud[i] and hops[i] >= t0:
            j = i
            while j + 1 < len(loud) and loud[j + 1]:
                j += 1
            a, b = float(hops[i]), float(hops[j])
            if b - a >= 1.0:
                runs.append((a, b))
            i = j + 1
        else:
            i += 1
    runs = [(a, min(b, t_end)) for a, b in runs if a < t_end]

    MIN_REAL_DROP = 4.0
    main_drop = None
    if runs:
        best = max(runs, key=lambda r: r[1] - r[0])
        if best[1] - best[0] >= MIN_REAL_DROP:
            main_drop = best
    if main_drop is None:
        main_drop = (min(t0 + duration * 0.55, t_end - 1.0), t_end)
    first_drop = main_drop[0]

    # A false drop is a brief swell that stands out from its LOCAL surroundings,
    # not one that crosses an absolute level - an absolute gate missed the 66-70s
    # bump entirely because the real drop is only a couple of dB louder.
    # Measured in dB against a +/-5s baseline: rise >= 6 dB and 1-5s long.
    env = np.asarray(music["rms"], np.float32)
    db = 20 * np.log10(np.maximum(env, 1e-6))
    # Median, not mean: a mean baseline is dragged upward by the swell it is
    # supposed to expose, which is why the 66-70s bump measured as +2.8 dB and was
    # rejected. Against a median over +/-8s the same bump reads about +9 dB.
    win = max(5, int(8.0 * SR / HOP)) | 1
    from numpy.lib.stride_tricks import sliding_window_view
    padded = np.pad(db, win // 2, mode="edge")
    baseline = np.median(sliding_window_view(padded, win), axis=1)
    rise = db - baseline

    false_drops = []
    covered = [(a - 0.5, b + 0.5) for a, b in ([main_drop] if main_drop else [])]
    i = 0
    while i < len(rise):
        t = float(hops[i])
        # 4.5 dB rather than 6: the 66-70s swell peaks at +5.8 dB against the
        # median baseline, so a 6 dB gate rejected the very thing being hunted.
        # The absolute floor keeps quiet passages from qualifying on noise.
        if t < t0 or t >= t_end or rise[i] < 4.5 or db[i] < -17.0:
            i += 1
            continue
        # walk the contiguous above-threshold swell, waiting out short dips
        j, gap = i, 0
        while j + 1 < len(rise) and (rise[j + 1] >= 3.0 or gap < int(0.4 * SR / HOP)):
            gap = gap + 1 if rise[j + 1] < 3.0 else 0
            j += 1
        a, b = t, float(hops[j])
        if 1.0 <= (b - a) <= 6.0 and not any(a < cb and b > ca for ca, cb in covered):
            false_drops.append((a, b))
        i = j + 1

    for a, b in false_drops:
        print(f"  false drop {a:.1f}-{b:.1f}s ({b-a:.1f}s)")

    def in_false(t: float) -> bool:
        return any(a <= t < b for a, b in false_drops)

    # cut grid, floored so the median shot stays near the measured 1.3-1.7s
    MIN_SHOT = 0.85
    grid, last = [], -99.0
    for t in onsets:
        if t - last >= MIN_SHOT:
            grid.append(float(t))
            last = float(t)
    if not grid:
        grid = [float(t) for t in np.arange(t0, t_end, 1.2)]
    grid = [t for t in grid if t0 <= t < t_end]

    def is_loud(t: float) -> bool:
        i = min(max(int(np.searchsorted(hops, t)), 0), len(loud) - 1)
        return bool(loud[i])

    def next_grid(t: float) -> float:
        for g in grid:
            if g > t + 0.05:
                return g
        return t_end

    # game-aware picker: avoid cutting from the same game twice in a row
    by_game: dict[str, list[dict]] = {}
    for c in kills:
        by_game.setdefault(c["game"], []).append(c)
    game_names = list(by_game)
    idx = {g: 0 for g in game_names}
    state = {"last": None}

    def pick() -> dict:
        order = game_names
        if len(order) > 1 and state["last"] in order:
            # rotate so the previous game is considered last
            i = order.index(state["last"])
            order = order[i + 1:] + order[:i]
        for g in order:
            pool = by_game[g]
            c = pool[idx[g] % len(pool)]
            idx[g] += 1
            state["last"] = g
            return c
        return kills[0]

    # drop-time preference: some games are simply better on a beat. Jets in
    # Battlefield are the obvious one, so those clips get first refusal on every
    # drop cut and the rotation only falls through to the others when exhausted.
    prefer_pools = []
    for g in (prefer or []):
        for name, pool in by_game.items():
            if g.lower() in name.lower():
                prefer_pools.append((name, pool))
    pidx = {"n": 0}
    last_used = {"clip": None}

    def pick_drop() -> dict:
        # Alternate: preferred game, then the normal rotation. Taking every drop
        # cut from the preferred pool made 24 consecutive jet cuts, which is
        # monotonous; the point is for them to LEAD the drop, not own it.
        if prefer_pools:
            if pidx["n"] % 2 == 0:
                k = pidx["n"] // 2
                name, pool = prefer_pools[k % len(prefer_pools)]
                c = pool[(k // max(1, len(prefer_pools))) % len(pool)]
                pidx["n"] += 1
                state["last"] = name
                return c
            pidx["n"] += 1
        return pick()

    shots: list[dict] = []
    t = t0
    BUILD_START, BUILD_END = 4.6, 1.9   # shot length at the start vs at the drop

    while t < t_end - 0.25:
        # false drops still get kills, but they are flagged so the render can
        # hit them with a heavier glitch than the main drop
        is_false = in_false(t)
        is_drop = is_false or (t >= first_drop) or is_loud(t)
        clip = pick_drop() if is_drop else pick()
        remaining = t_end - t
        span = max(0.8, clip["end"] - clip["start"])

        if is_drop:
            # ---- drop: the kill must be ON screen, straddled by the cut
            kill_at = clip["start"] + PAD_BEFORE
            take_start = max(clip["start"], kill_at - KILL_LEAD)
            seg_end = min(next_grid(t), t + KILL_TAIL)
            seg_len = max(0.7, min(seg_end - t, KILL_TAIL, remaining))
            shots.append({"clip": clip, "start": take_start,
                          "len": max(0.7, min(seg_len, span)),
                          "mode": "kill",
                          "heavy": is_false,
                          "at": round(t, 2)})
        else:
            # ---- build: progressively busier as the drop approaches
            p = (t - t0) / max(0.5, first_drop - t0)
            want = BUILD_START + (BUILD_END - BUILD_START) * min(1.0, p)
            # deliberately NOT snapped to the onset grid: only the drops are
            # beat-synced. Clamping to next_grid here locked the build to ~1s,
            # which made the quiet half as busy as the drop.
            seg_len = max(0.7, min(want, remaining))
            take = min(seg_len, span)
            # take from the run-up to the kill, not the padded tail
            start_at = clip["start"] + max(0.0, (span - take) * 0.45)
            shots.append({"clip": clip, "start": start_at, "len": take,
                          "mode": "play", "at": round(t, 2)})
        t += seg_len

    return shots


def apply_pin(shots: list[dict], path: str, src_start: float, src_end: float,
              at: float, length: float | None = None,
              at_label: float | None = None) -> tuple[list[dict], dict | None]:
    """Force a source range into an exact time window.

    Boundary shots are trimmed rather than swallowed, so the window can be any
    length (the Cobra manoeuvre is 7s of source squeezed into a 3s slot). Totals
    are preserved, so nothing after the pin shifts.
    """
    if length is None or length <= 0.05:
        # fall back: take the shot that starts at/after `at`
        idx = next((i for i, sh in enumerate(shots) if sh["at"] >= at - 1e-6), None)
        if idx is None:
            print(f"  pin: nothing at or after {at:.2f}s - ignored")
            return shots, None
        length = shots[idx]["len"]

    win_end = at + length
    src_span = max(0.1, src_end - src_start)
    pin = {
        "clip": {"path": path, "game": "Battlefield 6"},
        "start": src_start, "src_len": src_span, "len": length,
        "mode": "pin", "speed": src_span / length, "at": at,
    }

    out: list[dict] = []
    placed = False
    for sh in shots:
        a, b = sh["at"], sh["at"] + sh["len"]
        if b <= at + 1e-6:
            out.append(sh)
            continue
        if a >= win_end - 1e-6:
            if not placed:
                out.append(pin)
                placed = True
            out.append(sh)
            continue

        if not placed and a < at - 1e-6:
            head = dict(sh)
            head["len"] = at - a
            if head["len"] > 0.05:
                out.append(head)
        if not placed:
            out.append(pin)
            placed = True
        if b > win_end + 1e-6:
            tail = dict(sh)
            consumed = win_end - a
            tail["start"] = sh["start"] + consumed
            tail["len"] = b - win_end
            if tail["len"] > 0.05:
                out.append(tail)

    if not placed:
        out.append(pin)

    t = out[0]["at"]
    for sh in out:
        sh["at"] = round(t, 2)
        t += sh["len"]

    shown = at if at_label is None else at_label
    print(f"  pin '{os.path.basename(path)}' {src_start:.1f}-{src_end:.1f}s "
          f"-> {length:.2f}s at video {shown:.2f}s ({src_span / length:.2f}x)")
    return out, pin


FADE = 0.015      # seconds; kills the click at a join without being audible


def parse_songs(spec: str) -> list[tuple[str, float, float]]:
    """Parse 'path|start|end; path|start|end' into validated tuples."""
    out = []
    for raw in (spec or "").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        parts = [v.strip() for v in raw.split("|")]
        if len(parts) < 3:
            raise SystemExit(f"--songs entry needs PATH|start|end : {raw}")
        path = parts[0].strip('"')
        if not os.path.isfile(path):
            alt = os.path.join(HERE, path)
            if os.path.isfile(alt):
                path = alt
            else:
                raise SystemExit(f"--songs: file not found: {path}")
        a, b = float(parts[1]), float(parts[2])
        if b <= a:
            raise SystemExit(f"--songs: end must be after start : {raw}")
        out.append((path, a, b))
    if not out:
        raise SystemExit("--songs was empty")
    return out


def assemble_songs(spec: str, duration: float, out_path: Path) -> Path:
    """Decode, trim, fade and concat the slices into one track of `duration`."""
    entries = parse_songs(spec)

    parts: list[tuple[str, float, float]] = []
    total = 0.0
    i = 0
    while total < duration - 1e-6:
        path, a, b = entries[i % len(entries)]
        seg = min(b - a, duration - total)
        if seg <= 1e-6:
            break
        parts.append((path, a, seg))
        total += seg
        i += 1
        if i > 400:            # guard against a runaway loop
            break

    inputs, chains, labels = [], [], []
    for idx, (path, start, seg) in enumerate(parts):
        inputs += ["-ss", f"{start:.3f}", "-t", f"{seg:.3f}", "-i", path]
        # NOTE: the input label must sit directly against the first filter - a
        # comma after it makes ffmpeg read an empty filter name.
        filters = [f"atrim=0:{seg:.3f}", "asetpts=PTS-STARTPTS"]
        if idx > 0:
            filters.append(f"afade=t=in:st=0:d={FADE}")
        if idx < len(parts) - 1:
            filters.append(f"afade=t=out:st={max(0.0, seg - FADE):.3f}:d={FADE}")
        filters.append("aresample=48000")
        chains.append(f"[{idx}:a]" + ",".join(filters) + f"[a{idx}]")
        labels.append(f"[a{idx}]")

    fc = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(parts)}:v=0:a=1[out]"

    cmd = [FFMPEG, "-v", "error", "-y", *inputs,
           "-filter_complex", fc, "-map", "[out]",
           "-t", f"{duration:.3f}",
           "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise SystemExit(f"song assembly failed: {(r.stderr or '').strip()[:300]}")

    print(f"  soundtrack assembled: {len(parts)} slice(s) -> {duration:.2f}s")
    for path, start, seg in parts:
        print(f"    {os.path.basename(path):28} {start:6.2f}s +{seg:.2f}s")
    return out_path


KILL_MIN = 0.70            # a kill cut may not shrink below this: 0.30 lead + 0.40 tail
SYNC_MIN_SHOT = 0.35      # a shot may not shrink below this
SYNC_MAX_GROW = 0.45      # how much a kill may grow to absorb a nudge


def actual_times(shots: list[dict], accent_before: set[int], stinger_secs: float) -> list[float]:
    """True video start time of every shot, exactly as the concat will lay out."""
    t = 0.0
    out = []
    for i, sh in enumerate(shots):
        if i in accent_before and stinger_secs:
            t += stinger_secs
        out.append(t)
        t += sh["len"]
    return out


def verify_kills(shots, starts, beats_video, tol) -> dict:
    """Distance from each kill's real start to its nearest onset."""
    errs = []
    for i, sh in enumerate(shots):
        if sh.get("mode") != "kill":
            continue
        # the beat should carry the KILL, which lands KILL_LEAD into the shot -
        # measuring the shot start is what let the sync eat the lead
        t = starts[i] + KILL_LEAD
        near = min(beats_video, key=lambda b: abs(b - t)) if beats_video else t
        errs.append((i, t, near, t - near))
    if not errs:
        return {"n": 0, "within": 0, "median": 0.0, "worst": 0.0, "errs": []}
    mags = sorted(abs(e[3]) for e in errs)
    median = mags[len(mags) // 2]
    return {
        "n": len(errs),
        "within": sum(1 for m in mags if m <= tol),
        "median": median,
        "worst": mags[-1],
        "errs": errs,
    }


def sync_kills(shots, accent_before, stinger_secs, beats_music, t0, tol,
               repair=True, rounds=3):
    """Nudge boundaries so kills sit on onsets. Returns the final report."""
    beats_video = [b - t0 for b in beats_music]
    moved = 0
    for _ in range(rounds if repair else 1):
        starts = actual_times(shots, accent_before, stinger_secs)
        rep = verify_kills(shots, starts, beats_video, tol)
        if not repair or rep["within"] == rep["n"]:
            return rep, moved
        for i, t, near, err in rep["errs"]:
            if abs(err) <= tol or i == 0:
                continue
            prev, cur = shots[i - 1], shots[i]
            if prev.get("mode") == "pin" or cur.get("mode") == "pin":
                continue
            # the previous shot gives up `err`, the kill takes it: total unchanged.
            # If the previous shot is ITSELF a kill, it must keep its room too.
            prev_floor = KILL_MIN if prev.get("mode") == "kill" else SYNC_MIN_SHOT
            give = max(-(prev["len"] - prev_floor), min(err, SYNC_MAX_GROW))
            # the kill itself may be the one taking the nudge - never below its floor
            cur_floor = KILL_MIN if cur.get("mode") == "kill" else SYNC_MIN_SHOT
            give = max(give, -(cur["len"] - cur_floor))
            if abs(give) < 1e-4:
                continue
            prev["len"] = round(prev["len"] - give, 4)
            cur["len"] = round(cur["len"] + give, 4)
            moved += 1
    starts = actual_times(shots, accent_before, stinger_secs)
    return verify_kills(shots, starts, beats_video, tol), moved


def build(args) -> int:
    songs = getattr(args, "songs", "") or ""
    if songs:
        # --start is meaningless against a timeline that already begins at zero
        if float(getattr(args, "start", 0.0)) > 0.01:
            print("  note: --start is ignored with --songs; the timeline starts at 0")
        args.start = 0.0
        music_path = HERE / "_soundtrack.mp4"
        assemble_songs(songs, float(args.duration), music_path)
    else:
        music_path = Path(args.music)
        if not music_path.is_file():
            raise SystemExit(f"music not found: {music_path}")

    print(f"  analysing {music_path.name} ...")
    music = analyse_music(music_path)
    print(f"  duration {music['duration']:.1f}s  onsets {len(music['onsets'])}  "
          f"kicks {len(music['kicks'])}")

    kills = load_kills([g for g in (args.games or "").split(",") if g.strip()])
    kills = [k for k in kills if resolve(k)]
    if not kills:
        raise SystemExit("no kill windows resolve to real files")
    print(f"  kill windows available: {len(kills)}")

    t0 = max(0.0, min(args.start, music["duration"] - 1.0))
    duration = min(args.duration, music["duration"] - t0)

    # The outro is part of the same window: gameplay is planned for the
    # remainder, so the music still spans the full duration and nothing is cut.
    outro_path = getattr(args, "outro", "") or ""
    outro_secs = 0.0
    if outro_path and os.path.isfile(outro_path):
        try:
            d = subprocess.run(
                [FFPROBE, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", outro_path],
                capture_output=True, text=True, timeout=120).stdout.strip()
            outro_secs = float(d or 0)
        except (ValueError, subprocess.SubprocessError):
            outro_secs = 0.0
        if outro_secs:
            print(f"  outro {outro_secs:.2f}s - gameplay planned for "
                  f"{duration - outro_secs:.2f}s")
    gameplay = max(1.0, duration - outro_secs)
    prefer = [g.strip() for g in (args.prefer or "").split(",") if g.strip()]
    shots = plan(music, kills, t0, gameplay, prefer)
    loud_n = sum(1 for s in shots if s["mode"] == "kill")
    print(f"  planned {len(shots)} cuts over {duration:.1f}s "
          f"({loud_n} on drops, {len(shots) - loud_n} build-up)")

    for spec in (getattr(args, "pin", "") or "").split(";"):
        spec = spec.strip()
        if not spec:
            continue
        try:
            parts = [v.strip() for v in spec.split("|")]
            path, s0, s1 = parts[0], float(parts[1]), float(parts[2])
            at_video = float(parts[3])
            ln = float(parts[4]) if len(parts) > 4 else None
            # shots are keyed on MUSIC time; the user thinks in video seconds
            shots, _ = apply_pin(shots, path, s0, s1, at_video + t0, ln,
                                 at_label=at_video)
        except (ValueError, IndexError):
            print(f"  pin ignored, need PATH|src_start|src_end|at|len : {spec}")

    # ---- accents and the timing compensation, applied BEFORE the EDL is
    # written. A stinger occupies video time the music does not wait for, so each
    # one's duration is taken out of the shots that follow. Doing this during
    # planning is what makes a saved EDL final rather than a pre-trim snapshot.
    flash_every = 6
    accent_before: set[int] = set()
    stinger_count = 0 if args.mode == "edit" else len(
        [d for d in ("D", "DI", "DIK", "DIKG", "DIKGU", "DIKGUN")
         if (Path(STING) / d).is_dir()])
    if stinger_count:
        # The letters spell the name once. Place them in the LAST stretch of the
        # cut rather than spreading them through it: spreading put a full DIKGUN
        # early, then the outro spelled it again, so a 2 minute video read the
        # name twice. Taking the last N eligible cuts builds the word once,
        # leading into the outro.
        eligible = [_i for _i, _sh in enumerate(shots)
                    if _sh["mode"] == "kill" and _i > 0]
        # `eligible[-n:]` takes the last n kill cuts, and kill cuts are ~0.9s
        # apart in a drop - so all six stingers landed inside 5.5 seconds with
        # almost no gameplay between them. Walk backwards with a minimum spacing
        # instead, so the word builds across the tail rather than machine-gunning.
        gap = float(getattr(args, "stinger_gap", 4.0))
        chosen = []
        last_t = None
        for _i in reversed(eligible):
            _t = shots[_i]["at"]
            if last_t is not None and (last_t - _t) < gap:
                continue
            chosen.append(_i)
            last_t = _t
            if len(chosen) == stinger_count:
                break
        if len(chosen) < stinger_count:
            # not enough room at this spacing: top up with the nearest leftovers
            for _i in reversed(eligible):
                if _i in chosen:
                    continue
                chosen.append(_i)
                if len(chosen) == stinger_count:
                    break
            print(f"  note: only {len(chosen)} accent(s) fit at a {gap:.1f}s gap")
        chosen.sort()
        accent_before.update(chosen)
        if chosen:
            times = [round(shots[_i]["at"] - t0, 1) for _i in chosen]
            gaps = [round(b - a, 1) for a, b in zip(times, times[1:])]
            print(f"  accents at video {times}s  (gaps {gaps})")

    stinger_secs = stinger_seconds() if accent_before else 0.0
    if getattr(args, "from_edl", "") or "":
        # the EDL is the finished design; do not re-trim it
        accent_before = {i for i in accent_before}
        stinger_secs_used = 0.0
    else:
        stinger_secs_used = stinger_secs

    if stinger_secs_used and accent_before:
        print(f"  stinger duration {stinger_secs_used:.2f}s - compensated out of "
              f"the following shots")
        carry = 0.0
        for _i, _sh in enumerate(shots):
            if _i in accent_before:
                carry += stinger_secs_used
            if carry > 0.0 and _sh.get("mode") != "pin":
                floor = KILL_MIN if _sh.get("mode") == "kill" else 0.40
                take = min(carry, max(0.0, _sh["len"] - floor))
                _sh["len"] -= take
                carry -= take
        # With the letters clustered at the very end there are not enough shots
        # after them to absorb the stinger time, which left the cut 0.96s long and
        # clipped the outro's fade. Sweep backwards through the remaining shots.
        if carry > 0.05:
            for _sh in reversed(shots):
                if carry <= 0.05:
                    break
                if _sh.get("mode") == "pin":
                    continue
                floor = KILL_MIN if _sh.get("mode") == "kill" else 0.40
                take = min(carry, max(0.0, _sh["len"] - floor))
                _sh["len"] -= take
                carry -= take
        if carry > 0.05:
            print(f"  warning: {carry:.2f}s of stinger time could not be absorbed")

        # The 0.40s floors keep the sweep from absorbing everything, which left
        # the cut a fraction short and cut the outro's final frames. Reconcile
        # against the true expected total: shots + stingers must equal gameplay.
        expected = sum(_sh["len"] for _sh in shots) + len(accent_before) * stinger_secs_used
        diff = expected - gameplay
        if abs(diff) > 0.02:
            # Spread the correction across as many shots as it takes. Adjusting a
            # single shot hits the 0.30s floor and leaves a residual, which showed
            # up as a cut 0.41s long with the outro's fade clipped.
            remaining = diff
            for _sh in reversed(shots):
                if abs(remaining) <= 0.02:
                    break
                if _sh.get("mode") == "pin":
                    continue
                floor = KILL_MIN if _sh.get("mode") == "kill" else 0.30
                new_len = max(floor, _sh["len"] - remaining)
                remaining -= (_sh["len"] - new_len)
                _sh["len"] = new_len
            got = expected - remaining
            print(f"  reconciled by {-diff:+.2f}s -> shots + stingers = {got:.2f}s "
                  f"(target {gameplay:.2f}s)")
            if abs(remaining) > 0.02:
                print(f"  warning: {remaining:+.2f}s could not be reconciled")

    # ---- verify kills against the REAL timeline and nudge boundaries to fix
    # drift. Must run after compensation (which inserts the stinger time) and
    # before the EDL is written, so a saved EDL is already aligned.
    if accent_before or True:
        tol = float(getattr(args, "sync_tol", 0.04))
        rep, moved = sync_kills(
            shots, accent_before, stinger_secs_used, music["onsets"], t0, tol,
            repair=not bool(getattr(args, "no_sync", False)))
        if rep["n"]:
            print(f"  kill sync: {rep['within']}/{rep['n']} kills within "
                  f"{int(tol*1000)}ms   median {rep['median']*1000:.0f}ms   "
                  f"worst {rep['worst']*1000:.0f}ms   boundaries moved: {moved}")
            if rep["within"] < rep["n"]:
                worst = sorted(rep["errs"], key=lambda e: -abs(e[3]))[:3]
                for i, t, near, err in worst:
                    print(f"    shot {i:3d} at {t:6.2f}s is {err*1000:+.0f}ms off "
                          f"(nearest onset {near:.2f}s) - cannot move further")

    # ---- render from a hand-edited EDL instead of re-planning.
    # This is the modular path: change one shot in the JSON and only that
    # segment's cache key moves, so only it re-encodes.
    src_edl = getattr(args, "from_edl", "") or ""
    if src_edl:
        import json as _json
        with open(src_edl, encoding="utf-8") as fh:
            ed = _json.load(fh)
        loaded = []
        for r in ed.get("shots", []):
            clip = {"path": r["src"], "game": r.get("game", "?")}
            sh = {"clip": clip, "start": float(r["start"]), "len": float(r["len"]),
                  "mode": r.get("mode", "play"), "at": float(r["at"]) + t0,
                  "speed": float(r.get("speed", 1.0))}
            if r.get("src_len"):
                sh["src_len"] = float(r["src_len"])
            if r.get("heavy"):
                sh["heavy"] = True
            loaded.append(sh)
        if not loaded:
            raise SystemExit(f"EDL has no shots: {src_edl}")
        shots = loaded
        gameplay = sum(x["len"] for x in shots)
        print(f"  EDL loaded: {src_edl} - {len(shots)} shots, {gameplay:.2f}s")

    edl = getattr(args, "save_edl", "") or ""
    if edl:
        import json as _json
        rows = []
        for sh in shots:
            src = resolve(sh["clip"])
            rows.append({
                # video seconds, not music seconds - an EDL you hand-edit has to
                # match what you see when you scrub the output
                "at": round(sh["at"] - t0, 4), "len": round(sh["len"], 4),
                "mode": sh["mode"], "game": sh["clip"].get("game", "?"),
                "start": round(sh["start"], 4),
                "src": str(src) if src else None,
                "speed": round(float(sh.get("speed", 1.0)), 4),
                "src_len": sh.get("src_len"),
                "heavy": bool(sh.get("heavy")),
            })
        meta = {"music": str(music_path), "start": t0, "duration": duration,
                "gameplay": gameplay, "outro": outro_path or None,
                "outro_secs": outro_secs, "mode": args.mode, "shots": rows}
        with open(edl, "w", encoding="utf-8") as fh:
            _json.dump(meta, fh, indent=1)
        print(f"  EDL saved: {edl} ({len(rows)} shots)")

    if args.dry_run:
        print(f"  (times below are seconds into the finished video; t0={t0:.1f})")
        for s in shots[:25]:
            print(f"    {s['at'] - t0:6.2f}s  {s['mode']:5}  {s['clip']['game']:22} "
                  f"{s['start']:8.1f} +{s['len']:.2f}")
        return 0

    segdir = HERE / "segments"
    segdir.mkdir(exist_ok=True)
    for old in segdir.glob("seg_*.mp4"):
        old.unlink()

    # 2-frame colour accents, spliced into the concat list rather than rendered
    # per occurrence - the concat demuxer happily lists the same file twice.
    # ---- Blender stingers, used instead of colour flashes
    stinger_files: list[Path] = []

    def build_stinger(ch: str) -> Path | None:
        src_dir = os.path.join(STING, ch)
        if not os.path.isdir(src_dir):
            return None
        out = segdir / f"stinger_{ch}.mp4"
        if out.is_file():
            return out
        # The stinger MUST carry a silent audio track. Concatenating a video-only
        # file with audio+video segments using -c copy drops the audio for the
        # stinger's duration, so the music drifts and glitches on every accent and
        # the A/V offset shifts the following cuts.
        r = subprocess.run([
            FFMPEG, "-v", "error", "-y", "-framerate", str(OUT_FPS),
            "-i", os.path.join(src_dir, "f_%04d.png"),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf", f"scale={OUT_W}:{OUT_H},setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-shortest", "-movflags", "+faststart", str(out)],
            capture_output=True, timeout=600)
        return out if r.returncode == 0 and out.is_file() else None

    # cumulative stages: each clip contains every letter landed so far, so the
    # word builds D -> DI -> DIK -> DIKG -> DIKGU -> DIKGUN with the camera
    # pulling back. See blender_letters.py.
    if args.mode == "edit":
        stinger_files = []
        print("  mode: edit - no letter stingers")
    else:
        for stage in ("D", "DI", "DIK", "DIKG", "DIKGU", "DIKGUN"):
            p = build_stinger(stage)
            if p:
                stinger_files.append(p)
    if stinger_files:
        print(f"  stingers available: {len(stinger_files)} "
              f"({', '.join(p.stem.replace('stinger_', '') for p in stinger_files)})")
    elif args.mode == "promo":
        print("  no stingers found - falling back to colour flashes")

    def make_flash(name: str, colour: str) -> Path:
        p = segdir / name
        subprocess.run([
            FFMPEG, "-v", "error", "-y", "-f", "lavfi",
            "-i", f"color=c={colour}:s={OUT_W}x{OUT_H}:d=0.034:r={OUT_FPS}",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-shortest", "-movflags", "+faststart", str(p)],
            check=True, timeout=300)
        return p

    black = make_flash("flash_black.mp4", "black")
    white = make_flash("flash_white.mp4", "white")

    made: list = []
    n_cache = n_new = 0
    for i, s in enumerate(shots):
        src = resolve(s["clip"])
        if not src:
            continue
        out = segdir / f"seg_{i:03d}.mp4"
        # two thirds of kills, so the effect stays an accent rather than a
        # every-single-cut tic
        glitch = (s["mode"] == "kill" and (i % 3) != 2)
        heavy = bool(s.get("heavy"))
        speed = float(s.get("speed", 1.0))
        src_len = s.get("src_len")

        key = segment_key(src, s["start"], s["len"], speed, src_len, glitch, heavy)
        if not args.no_cache and cache_get(key, out):
            made.append(out)
            n_cache += 1
            continue

        if extract(src, s["start"], s["len"], out,
                   glitch=glitch, heavy=heavy, speed=speed, src_len=src_len):
            made.append(out)
            n_new += 1
            if not args.no_cache:
                cache_put(key, out)
        else:
            print(f"    cut {i} failed ({s['clip']['game']})")
    if not made:
        raise SystemExit("no segments extracted")
    print(f"  segments: {n_cache} reused from cache, {n_new} encoded")

    # Frame rounding adds up: each segment is ceil()'d to a frame at 60fps, so
    # ~59 segments drift ~+0.5s. The final mux is cut at `duration`, which was
    # eating the end of the outro's fade. Measure the real total and give the
    # excess back out of the last gameplay shot.
    def _seg_dur(pth: Path) -> float:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(pth)],
                             capture_output=True, text=True, timeout=120).stdout.strip()
        try:
            return float(out or 0)
        except ValueError:
            return 0.0

    if outro_secs and made:
        n_acc = len(accent_before) if args.mode == "promo" else 0
        measured = sum(_seg_dur(p) for p in made) + n_acc * stinger_secs + outro_secs
        excess = measured - duration
        if excess > 0.02:
            # Walk backwards re-extracting shots until the excess is gone. One
            # shot is often shorter than the excess, and the 0.30s floor stops it
            # absorbing the rest - which left the outro's fade clipped.
            remaining = excess
            trimmed = 0
            for idx in range(len(made) - 1, -1, -1):
                if remaining <= 0.02:
                    break
                sh = shots[idx] if idx < len(shots) else None
                if not sh or sh.get("mode") == "pin":
                    continue
                src = resolve(sh["clip"])
                if not src:
                    continue
                floor = KILL_MIN if sh.get("mode") == "kill" else 0.30
                new_len = sh["len"] - remaining
                if new_len < floor:
                    new_len = floor
                give = sh["len"] - new_len
                if give <= 0.005:
                    continue
                if extract(src, sh["start"], new_len, made[idx],
                           glitch=(sh["mode"] == "kill" and (idx % 3) != 2),
                           heavy=bool(sh.get("heavy")),
                           speed=float(sh.get("speed", 1.0)),
                           src_len=sh.get("src_len")):
                    remaining -= give
                    trimmed += 1
            if remaining <= 0.02:
                print(f"  trimmed {trimmed} shot(s) by {excess:.2f}s total "
                      f"(frame rounding) so the outro plays in full")
            else:
                print(f"  warning: {remaining:+.2f}s of frame-rounding excess remains")

    # Flash accents: one every ~6s on a drop cut, mostly black, occasional white.
    # That cadence and ratio come from measuring the reference edits.
    listing_lines = []
    n_sting = n_flash_b = n_flash_w = 0
    for i, p in enumerate(made):
        shot = shots[i] if i < len(shots) else None
        # The letters spell the channel name, so they cycle exactly ONCE. Beyond
        # that the accents stop - a repeated letter read as "DIKGUND", which is
        # not the name. The remaining accent positions become plain hard cuts,
        # which matches the measured reference edits anyway.
        if i in accent_before and args.mode == "promo":
            if stinger_files and n_sting < len(stinger_files):
                listing_lines.append(f"file '{stinger_files[n_sting % len(stinger_files)].name}'")
                n_sting += 1
            elif (i // flash_every) % 4 == 3:
                listing_lines.append(f"file '{white.name}'")
                n_flash_w += 1
            else:
                listing_lines.append(f"file '{black.name}'")
                n_flash_b += 1
        listing_lines.append(f"file '{p.name}'")
    if outro_secs:
        outro_seg = segdir / "outro_seg.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", outro_path,
                        "-vf", f"scale={OUT_W}:{OUT_H},setsar=1,fps={OUT_FPS}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                        "-ar", "48000", "-ac", "2", "-shortest",
                        "-movflags", "+faststart", str(outro_seg)],
                       check=True, timeout=1800)
        listing_lines.append(f"file '{outro_seg.name}'")
        print(f"  outro appended ({outro_secs:.2f}s)")

    if args.mode == "edit":
        print("  no accents inserted")
    elif stinger_files:
        print(f"  stinger accents: {n_sting}")
    else:
        print(f"  flash accents: {n_flash_b} black, {n_flash_w} white")

    listing = segdir / "list.txt"
    listing.write_text("\n".join(listing_lines), encoding="utf-8")

    # CONCAT: re-encode the audio, copy only the video.
    #
    # Concatenating with plain -c copy keeps each segment's independently encoded
    # AAC frames, and every join lands two packets on the same timestamp. On this
    # 90s cut that produced 74 duplicate-timestamp audio packets, all of them on
    # cut boundaries, plus a negative start time (-0.021s) from unhandled encoder
    # delay. VLC plays through it; YouTube's re-encode turns each one into an
    # audible glitch. aresample=async=1 rebuilds a continuous timeline and
    # first_pts=0 pins the start at zero.
    silent = segdir / "video.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(listing),
                    "-c:v", "copy",
                    "-af", "aresample=async=1:first_pts=0",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-avoid_negative_ts", "make_zero",
                    "-movflags", "+faststart", str(silent)],
                   check=True, timeout=1800)

    out = Path(args.out)
    # music at full level, game audio attenuated under it
    # The track is already mastered near full scale, so music + game audio summed
    # straight together clipped (measured max_volume -0.0 dB). Pull the music down
    # a touch and finish with a limiter so nothing touches the ceiling.
    MUSIC_TRIM_DB = -3.0
    filt = (f"[0:a]volume={GAME_AUDIO_DB}dB[game];"
            f"[1:a]atrim={t0}:{t0 + duration},asetpts=PTS-STARTPTS,"
            f"volume={MUSIC_TRIM_DB}dB[mus];"
            f"[game][mus]amix=inputs=2:duration=first:dropout_transition=0:"
            f"weights=1 1,alimiter=limit=0.95:level=false[a]")
    subprocess.run([
        FFMPEG, "-v", "error", "-y",
        "-i", str(silent), "-i", str(music_path),
        "-filter_complex", filt, "-map", "0:v", "-map", "[a]",
        "-t", f"{duration:.3f}",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart", str(out),
    ], check=True, timeout=1800)

    print(f"  wrote {out}  ({out.stat().st_size/1e6:.1f} MB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--music", default="",
                    help="single audio file (wav/mp3/m4a/opus)")
    ap.add_argument("--songs", default="",
                    help="assemble the soundtrack from several tracks: "
                         "\"path|start|end; path|start|end\" - entries repeat to "
                         "fill --duration")
    ap.add_argument("--out", default="promo.mp4")
    ap.add_argument("--games", default="", help="comma list; empty = every game with kills")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--start", type=float, default=0.0,
                    help="seconds into the track to begin at")
    ap.add_argument("--outro", default="", help="mp4 to append as the outro; "
                    "gameplay is shortened to make room for it")
    ap.add_argument("--save-edl", default="",
                    help="write the planned cut to JSON for hand editing")
    ap.add_argument("--from-edl", default="",
                    help="render from a saved EDL instead of re-planning")
    ap.add_argument("--stinger-gap", type=float, default=4.0,
                    help="minimum seconds of gameplay between letter stingers")
    ap.add_argument("--sync-tol", type=float, default=0.04,
                    help="how close a kill must land to an onset, seconds")
    ap.add_argument("--no-sync", action="store_true",
                    help="report kill alignment but do not nudge boundaries")
    ap.add_argument("--no-cache", action="store_true",
                    help="re-encode every segment, ignore the segment cache")
    ap.add_argument("--mode", choices=("promo", "edit"), default="promo",
                    help="promo = with DIKGUN letter stingers; edit = same cut and "
                         "the same kill/music interplay, just no letter cards")
    ap.add_argument("--pin", default="", help="force a source range into a window: "
                    "PATH|src_start|src_end|at|len, semicolon-separated for several")
    ap.add_argument("--prefer", default="",
                    help="comma list of games to favour on drop cuts")
    ap.add_argument("--dry-run", action="store_true")
    parsed = ap.parse_args()
    if not parsed.music and not parsed.songs:
        ap.error("one of --music or --songs is required")
    return build(parsed)


if __name__ == "__main__":
    sys.exit(main())
