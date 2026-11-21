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
def extract(src: Path, start: float, length: float, out: Path) -> bool:
    """One cut, normalised to the output frame so concat cannot drift."""
    cmd = [
        FFMPEG, "-v", "error", "-y",
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, length):.3f}",
        "-i", str(src),
        "-vf", (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{OUT_H},fps={OUT_FPS},setsar=1"),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return r.returncode == 0 and out.is_file() and out.stat().st_size > 2048


def plan(music: dict, kills: list[dict], t0: float, duration: float) -> list[dict]:
    """Decide, section by section, whether to show sustained play or a kill cut.

    Build-up must read as regular gameplay, so it is NOT beat-synced: one long
    take per section, 4-7s from a single recording. Drops are beat-synced, but on
    a coarsened grid - the raw onset rate here is ~3 per second, which would cut
    every 300ms and strobe.

    The target is the shot length actually measured in the two existing edits:
    median 1.3s and 1.7s.
    """
    hops = music["hop_times"]
    onsets = music["onsets"]

    # Majority-vote the loud/quiet flag over ~1.5s. The raw per-hop envelope
    # flickers, which scattered single kill cuts through the build-up instead of
    # producing sustained drops.
    raw_loud = np.asarray(music["loud"], bool)
    span = max(3, int(1.5 * SR / HOP))
    if len(raw_loud) > span:
        kern = np.ones(span, np.float32) / span
        frac = np.convolve(raw_loud.astype(np.float32), kern, mode="same")
        loud = frac > 0.5
    else:
        loud = raw_loud

    # coarsen the cut grid so the median shot lands near the measured 1.3-1.7s
    MIN_SHOT = 0.85
    grid = []
    last = -99.0
    for t in onsets:
        if t - last >= MIN_SHOT:
            grid.append(float(t))
            last = float(t)
    if not grid:
        grid = [float(t) for t in np.arange(0.0, duration, 1.2)]
    t_end = t0 + duration
    grid = [t for t in grid if t0 <= t < t_end]

    def is_loud(t: float) -> bool:
        i = int(np.searchsorted(hops, t))
        i = min(max(i, 0), len(loud) - 1)
        return bool(loud[i])

    def next_grid(t: float) -> float:
        for g in grid:
            if g > t + 0.05:
                return g
        return t_end

    shots: list[dict] = []
    ki = 0
    t = t0
    BUILD_TAKE = 5.0        # seconds of uninterrupted play per build-up section
    KILL_TAKE = 1.6         # longest a single kill cut may run

    while t < t_end - 0.25:
        clip = kills[ki % len(kills)]
        ki += 1
        remaining = t_end - t

        if is_loud(t):
            # drop: cut on the grid, hold long enough to read the kill
            seg_end = min(next_grid(t), t + KILL_TAKE)
            seg_len = max(0.55, min(seg_end - t, remaining))
            span = max(0.6, clip["end"] - clip["start"])
            shots.append({"clip": clip, "start": clip["start"],
                          "len": min(seg_len, span), "mode": "kill",
                          "at": round(t, 2)})
        else:
            # build-up: one sustained take, ignoring the beat grid entirely
            seg_len = max(2.0, min(BUILD_TAKE, remaining))
            span = clip["end"] - clip["start"]
            take = min(seg_len, max(2.0, span))
            start_at = clip["start"] + max(0.0, (span - take) * 0.25)
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
    shots = plan(music, kills, t0, duration)
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

    made = []
    for i, s in enumerate(shots):
        src = resolve(s["clip"])
        if not src:
            continue
        out = segdir / f"seg_{i:03d}.mp4"
        if extract(src, s["start"], s["len"], out):
            made.append(out)
        else:
            print(f"    cut {i} failed ({s['clip']['game']})")
    if not made:
        raise SystemExit("no segments extracted")
    print(f"  extracted {len(made)} segments")

    listing = segdir / "list.txt"
    listing.write_text("\n".join(f"file '{p.name}'" for p in made), encoding="utf-8")

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
    ap.add_argument("--dry-run", action="store_true")
    return build(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
