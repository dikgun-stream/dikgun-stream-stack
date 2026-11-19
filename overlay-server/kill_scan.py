"""Kill scanner for Rainbow Six Siege recordings.

Finds kills so the clip rotation is not all laughter. The in-game tell is a
small red X at the crosshair with a white centre dot, and it arrives together
with a white "+110"-style score popup just above it. Either alone is noisy --
red scenery and white HUD text both occur constantly -- but requiring **both at
once** is what makes this reliable enough to use.

Templates were taken from a hand-identified frame and live in
`streamer-mode/assets/kill/`:
    tpl_x_mask.png      the four red arms of the X
    tpl_score_mask.png  the white score popup glyphs

Matching is done mask-to-mask (binary), not greyscale, because a binary mask of
"red-ness" and "white-ness" survives the changing backdrop that a brightness
correlation does not.

Usage:
    python kill_scan.py --list
    python kill_scan.py --one <file>
    python kill_scan.py --all --threshold 0.5

Output: kill_index.json next to this file, read by /api/clips/highlights.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import scan_lock
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

ROOT = Path(__file__).parent
TEMPLATE_DIR = ROOT.parent / "streamer-mode" / "assets" / "kill"
INDEX_PATH = ROOT / "kill_index.json"

# centre crop of a 1920x1080 frame: the crosshair and its markers live here
CROP_W, CROP_H, CROP_X, CROP_Y = 480, 360, 720, 360

# the templates came from a downscaled screenshot; at 1080p the marker renders
# around 1.5x larger, so search a range rather than assuming one scale
SCALES = (1.15, 1.35, 1.55, 1.75, 1.95)

DEFAULT_FPS = 8
DEFAULT_THRESHOLD = 0.50
DEFAULT_X_THRESHOLD = 0.40


# --------------------------------------------------------------------------- templates

_TEMPLATES = None


def load_templates():
    global _TEMPLATES
    if _TEMPLATES is not None:
        return _TEMPLATES
    import cv2
    import numpy as np

    x_path = TEMPLATE_DIR / "tpl_x_mask.png"
    s_path = TEMPLATE_DIR / "tpl_score_mask.png"
    if not x_path.is_file() or not s_path.is_file():
        raise SystemExit(f"kill templates missing from {TEMPLATE_DIR}")
    tpl_x = cv2.imread(str(x_path), cv2.IMREAD_GRAYSCALE)
    tpl_score = cv2.imread(str(s_path), cv2.IMREAD_GRAYSCALE)
    _TEMPLATES = ((tpl_x > 127).astype(np.uint8), (tpl_score > 127).astype(np.uint8))
    return _TEMPLATES


def score_frame(frame):
    """Return ((score, (x, y)), (xscore, (x, y))) for one BGR frame.

    Positions matter: in a real kill the score popup sits directly above the X,
    so requiring the two matches to be near each other removes most of the
    false positives that a brightness-or-shape match alone lets through
    (an orange crate scored 0.74 on the X template on its own).
    """
    import cv2
    import numpy as np

    tpl_x, tpl_score = load_templates()
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    white = ((v > 170) & (s < 90)).astype(np.uint8)
    red = (((h < 12) | (h > 168)) & (s > 120) & (v > 90)).astype(np.uint8)

    def best(mask, tpl):
        top = 0.0
        loc = (mask.shape[1] // 2, mask.shape[0] // 2)
        for sc in SCALES:
            t = cv2.resize(tpl, None, fx=sc, fy=sc, interpolation=cv2.INTER_NEAREST)
            if t.shape[0] >= mask.shape[0] or t.shape[1] >= mask.shape[1]:
                continue
            if t.sum() < 20:
                continue
            r = cv2.matchTemplate(mask, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, mloc = cv2.minMaxLoc(r)
            if mx > top:
                # centre of the template, in frame coordinates
                top = float(mx)
                loc = (mloc[0] + t.shape[1] // 2, mloc[1] + t.shape[0] // 2)
        return top, loc

    (sc, sloc), (xs, xloc) = best(white, tpl_score), best(red, tpl_x)
    return (sc, sloc), (xs, xloc)


def proximity_ok(sloc, xloc, limit=110.0):
    """True when the popup and the X are close enough to be one event."""
    dx = sloc[0] - xloc[0]
    dy = sloc[1] - xloc[1]
    return (dx * dx + dy * dy) ** 0.5 <= limit


# --------------------------------------------------------------------------- scan

def frames(video: Path, fps: int):
    """Yield centre-cropped BGR frames straight off ffmpeg's stdout."""
    import numpy as np

    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vf", f"crop={CROP_W}:{CROP_H}:{CROP_X}:{CROP_Y},fps={fps}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    n = CROP_W * CROP_H * 3
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape((CROP_H, CROP_W, 3))
    finally:
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        proc.wait(timeout=30)


def segments_from_hits(times, pad=20.0, min_gap=10.0, total=None):
    """Turn kill timestamps into playable windows, merging overlaps.

    Kills deserve a tighter window than laughter: you want the second before and
    a beat after, not ninety seconds either side.
    """
    if not times:
        return []
    groups = [[times[0], times[0]]]
    for t in times[1:]:
        if t - groups[-1][1] <= min_gap:
            groups[-1][1] = t
        else:
            groups.append([t, t])

    windows = []
    for first, last in groups:
        start = max(0.0, first - pad)
        end = last + pad
        if total:
            end = min(end, total)
        if end - start < 5.0:
            continue
        windows.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "kills": None,
            "first_at": round(first, 2),
            "last_at": round(last, 2),
        })

    windows.sort(key=lambda w: w["start"])
    merged: list[dict] = []
    for w in windows:
        if merged and w["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], w["end"])
            merged[-1]["last_at"] = max(merged[-1]["last_at"], w["last_at"])
        else:
            merged.append(dict(w))
    return merged


def duration(video: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return float(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return 0.0


def scan_file(video: Path, threshold: float, x_threshold: float,
              fps: int = DEFAULT_FPS, pad: float = 20.0,
              min_gap: float = 10.0, verbose: bool = False) -> dict:
    times = []
    peak_s = peak_x = 0.0
    n_frames = 0
    for frame in frames(video, fps):
        (score, sloc), (xscore, xloc) = score_frame(frame)
        peak_s = max(peak_s, score)
        peak_x = max(peak_x, xscore)
        if (score >= threshold and xscore >= x_threshold
                and proximity_ok(sloc, xloc)):
            times.append(n_frames / fps)
        n_frames += 1

    dur = duration(video) or (n_frames / fps)
    # merge hits closer than half a second: one kill renders over several frames
    merged_times: list[float] = []
    for t in times:
        if not merged_times or t - merged_times[-1] > 0.5:
            merged_times.append(t)

    segs = segments_from_hits(merged_times, pad=pad, min_gap=min_gap, total=dur)
    for s in segs:
        s["kills"] = sum(1 for t in merged_times if s["start"] <= t <= s["end"])

    if verbose:
        print(f"  frames={n_frames} ({n_frames/max(fps,1):.0f}s)")
        print(f"  peak score-popup={peak_s:.3f}  peak kill-x={peak_x:.3f}")
        print(f"  frames passing both: {len(times)}  distinct kills: {len(merged_times)}")
        for t in merged_times[:25]:
            print(f"    kill at {t:7.2f}s")
        for s in segs:
            print(f"    window {s['start']:.1f}-{s['end']:.1f} ({s['kills']} kills)")

    return {
        "ok": True,
        "duration": round(dur, 2),
        "peak_score": round(peak_s, 4),
        "peak_x": round(peak_x, 4),
        "kills": len(merged_times),
        "segments": segs,
    }


# --------------------------------------------------------------------------- index

def load_index() -> dict:
    try:
        if INDEX_PATH.is_file():
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"files": {}}
    return {"files": {}}


def save_index(idx: dict) -> None:
    scan_lock.atomic_write_json(INDEX_PATH, idx)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("DIKGUN_CLIPS", r"C:\Users\JR\Videos"))
    ap.add_argument("--game", default="Rainbow Six Siege",
                    help="only scan folders for this game (the marker is R6-specific)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--x-threshold", type=float, default=DEFAULT_X_THRESHOLD)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS)
    ap.add_argument("--pad", type=float, default=20.0)
    ap.add_argument("--min-gap", type=float, default=10.0)
    ap.add_argument("--one", help="scan a single file")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--rescan", action="store_true")
    args = ap.parse_args(argv)

    if args.one:
        target = Path(args.one)
        print(f"scanning {target.name}")
        res = scan_file(target, args.threshold, args.x_threshold,
                        fps=args.fps, pad=args.pad, min_gap=args.min_gap, verbose=True)
        print(json.dumps(res, indent=1))
        return 0

    root = Path(args.root)
    if not root.is_dir():
        print(f"clip root not found: {root}")
        return 2

    exts = {".mp4", ".mkv", ".mov", ".webm"}
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in exts)
    if args.game:
        # folder names in this library have doubled spaces ("Rainbow Six  Siege"),
        # so compare on collapsed whitespace or the filter silently matches nothing
        def norm(x: str) -> str:
            return " ".join(x.lower().split())

        want = norm(args.game)
        files = [p for p in files if want in norm(p.parent.name)]

    if args.list:
        for p in files:
            print(f"{duration(p):8.1f}s  {p.relative_to(root)}")
        print(f"{len(files)} files")
        return 0

    if not args.all:
        ap.print_help()
        return 1

    if not scan_lock.acquire(ROOT, "kill"):
        return 1
    idx = load_index()
    todo = [p for p in files
            if args.rescan or str(p.relative_to(root)) not in idx["files"]]
    print(f"{len(files)} R6 files, {len(todo)} to scan "
          f"(threshold {args.threshold}, fps {args.fps})")

    for i, p in enumerate(todo, 1):
        rel = str(p.relative_to(root)).replace("\\", "/")
        print(f"[{i}/{len(todo)}] {rel}", flush=True)
        res = scan_file(p, args.threshold, args.x_threshold, fps=args.fps,
                        pad=args.pad, min_gap=args.min_gap)
        idx["files"][rel] = res
        if res.get("kills"):
            print(f"    {res['kills']} kill(s), {len(res['segments'])} window(s)", flush=True)
        save_index(idx)

    total = sum(v.get("kills", 0) for v in idx["files"].values() if isinstance(v, dict))
    wins = sum(len(v.get("segments") or []) for v in idx["files"].values() if isinstance(v, dict))
    scan_lock.release(ROOT, "kill")
    print(f"done. {total} kills -> {wins} windows across {len(idx['files'])} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
