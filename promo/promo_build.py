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
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SERVER = Path(r"C:\Users\JR\Documents\deepseek-harness\default-workspace\overlay-server")
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
    root = ROOTS.get(clip.get("mount") or "videos")
    if not root:
        return None
    p = Path(root) / str(clip["rel"]).replace("\\", "/")
    return p if p.is_file() else None


# --------------------------------------------------------------------- build
def extract(src: Path, start: float, length: float, out: Path,
            glitch: bool = False, heavy: bool = False) -> bool:
    """One cut, normalised to the output frame so concat cannot drift.

    Output seeking (-i first) is slower than input seeking but exact. Input
    seeking lands on the preceding keyframe and holds it, which is the visible
    freeze at the start of every cut.
    """
    vf = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
          f"crop={OUT_W}:{OUT_H},fps={OUT_FPS},setsar=1")
    if glitch:
        # channels separate then snap back. False drops get a wider shift and a
        # short pixelate, so the accent is visibly different from the main drop.
        shift = 18 if heavy else 9
        vf += f",rgbashift=rh={shift}:bh=-{shift}:edge=wrap:enable='lt(t,0.07)'"
        if heavy:
            vf += ",pixelize=w=48:h=27:enable='lt(t,0.05)'"
    cmd = [
        FFMPEG, "-v", "error", "-y",
        "-i", str(src),
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, length):.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return r.returncode == 0 and out.is_file() and out.stat().st_size > 2048


# server-side padding: the API builds windows as [moment - 5, moment + 3]
PAD_BEFORE = 5.0
KILL_LEAD = 0.45      # start this far before the kill
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


def build(args) -> int:
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
    prefer = [g.strip() for g in (args.prefer or "").split(",") if g.strip()]
    shots = plan(music, kills, t0, duration, prefer)
    loud_n = sum(1 for s in shots if s["mode"] == "kill")
    print(f"  planned {len(shots)} cuts over {duration:.1f}s "
          f"({loud_n} on drops, {len(shots) - loud_n} build-up)")

    if args.dry_run:
        for s in shots[:25]:
            print(f"    {s['at']:6.2f}s  {s['mode']:5}  {s['clip']['game']:22} "
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
        r = subprocess.run([
            FFMPEG, "-v", "error", "-y", "-framerate", str(OUT_FPS),
            "-i", os.path.join(src_dir, "f_%04d.png"),
            "-vf", f"scale={OUT_W}:{OUT_H},setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
            capture_output=True, timeout=600)
        return out if r.returncode == 0 and out.is_file() else None

    for ch in "DIKGUN":
        p = build_stinger(ch)
        if p:
            stinger_files.append(p)
    if stinger_files:
        print(f"  stingers available: {len(stinger_files)} "
              f"({', '.join(p.stem.replace('stinger_', '') for p in stinger_files)})")
    else:
        print("  no stingers found - falling back to colour flashes")

    def make_flash(name: str, colour: str) -> Path:
        p = segdir / name
        subprocess.run([
            FFMPEG, "-v", "error", "-y", "-f", "lavfi",
            "-i", f"color=c={colour}:s={OUT_W}x{OUT_H}:d=0.034:r={OUT_FPS}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(p)],
            check=True, timeout=300)
        return p

    black = make_flash("flash_black.mp4", "black")
    white = make_flash("flash_white.mp4", "white")

    made: list = []
    for i, s in enumerate(shots):
        src = resolve(s["clip"])
        if not src:
            continue
        out = segdir / f"seg_{i:03d}.mp4"
        if extract(src, s["start"], s["len"], out,
                   glitch=(s["mode"] == "kill"),
                   heavy=bool(s.get("heavy"))):
            made.append(out)
        else:
            print(f"    cut {i} failed ({s['clip']['game']})")
    if not made:
        raise SystemExit("no segments extracted")
    print(f"  extracted {len(made)} segments")

    # Flash accents: one every ~6s on a drop cut, mostly black, occasional white.
    # That cadence and ratio come from measuring the reference edits.
    listing_lines = []
    flash_every = 6
    n_sting = n_flash_b = n_flash_w = 0
    for i, p in enumerate(made):
        shot = shots[i] if i < len(shots) else None
        if (shot and shot["mode"] == "kill" and i % flash_every == 0 and i > 0):
            if stinger_files:
                listing_lines.append(f"file '{stinger_files[n_sting % len(stinger_files)].name}'")
                n_sting += 1
            elif (i // flash_every) % 4 == 3:
                listing_lines.append(f"file '{white.name}'")
                n_flash_w += 1
            else:
                listing_lines.append(f"file '{black.name}'")
                n_flash_b += 1
        listing_lines.append(f"file '{p.name}'")
    if stinger_files:
        print(f"  stinger accents: {n_sting}")
    else:
        print(f"  flash accents: {n_flash_b} black, {n_flash_w} white")

    listing = segdir / "list.txt"
    listing.write_text("\n".join(listing_lines), encoding="utf-8")

    silent = segdir / "video.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c", "copy", str(silent)],
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
        "-movflags", "+faststart", str(out),
    ], check=True, timeout=1800)

    print(f"  wrote {out}  ({out.stat().st_size/1e6:.1f} MB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--music", required=True, help="audio file (wav/mp3/m4a)")
    ap.add_argument("--out", default="promo.mp4")
    ap.add_argument("--games", default="", help="comma list; empty = every game with kills")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--start", type=float, default=0.0,
                    help="seconds into the track to begin at")
    ap.add_argument("--prefer", default="",
                    help="comma list of games to favour on drop cuts")
    ap.add_argument("--dry-run", action="store_true")
    return build(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
