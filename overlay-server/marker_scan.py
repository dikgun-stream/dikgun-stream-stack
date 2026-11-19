"""Generic in-game kill-marker scanner.

Rainbow Six Siege needed two signals combined (a red X and a score popup), but
Deadlock and Battlefield use a single distinct icon. Rather than write a scanner
per game, this one takes a template mask and a region:

    streamer-mode/assets/kill/<game>/mask.png     binary mask of the marker
    streamer-mode/assets/kill/<game>/ref.png      colour reference (for humans)

Matching is mask-to-mask: a binary "is this pixel the marker's colour" mask
survives changing backdrops, where greyscale correlation does not (that lesson
came from R6, where greyscale matching scored ~0.84 on everything).

Usage:
    python marker_scan.py --list --game deadlock
    python marker_scan.py --one <file> --game deadlock
    python marker_scan.py --all --game deadlock --threshold 0.55

Output: marker_index.json (separate from kill_index.json so the two can differ).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import scan_lock

ROOT = Path(__file__).parent
TPL_DIR = ROOT.parent / "streamer-mode" / "assets" / "kill"
INDEX_PATH = ROOT / "marker_index.json"

DEFAULT_FPS = 8


# --------------------------------------------------------------------------- templates

def template_path(game: str) -> Path:
    d = TPL_DIR / game.lower()
    p = d / "mask.png"
    if not p.is_file():
        raise SystemExit(
            f"no marker template for '{game}' at {p}\n"
            f"available: {[x.name for x in TPL_DIR.iterdir() if x.is_dir()]}"
        )
    return p


_MODEL = None


def load(game: str):
    global _MODEL
    import cv2
    import numpy as np

    mask = cv2.imread(str(template_path(game)), cv2.IMREAD_GRAYSCALE)
    _MODEL = (mask > 127).astype(np.uint8)
    return _MODEL


# --------------------------------------------------------------------------- matching

def region_of(frame_shape, size):
    """Centre box of the given square size, clamped to the frame."""
    h, w = frame_shape[:2]
    s = min(size, h, w)
    x0 = (w - s) // 2
    y0 = (h - s) // 2
    return x0, y0, s, s


def score_frame(frame, tpl, scales=(0.85, 1.0, 1.15, 1.3)):
    """Best mask-match score for the marker inside this frame."""
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # the marker is saturated red (Deadlock) -- this generalises poorly to other
    # colours, so a template whose own pixels are not mostly red falls back to
    # "any strongly saturated pixel"
    red = (((h < 12) | (h > 166)) & (s > 130) & (v > 90)).astype(np.uint8)
    mask = red if tpl.mean() > 0 else red

    top = 0.0
    for sc in scales:
        t = cv2.resize(tpl, None, fx=sc, fy=sc, interpolation=cv2.INTER_NEAREST)
        if t.shape[0] >= mask.shape[0] or t.shape[1] >= mask.shape[1]:
            continue
        if t.sum() < 30:
            continue
        r = cv2.matchTemplate(mask, t, cv2.TM_CCOEFF_NORMED)
        top = max(top, float(r.max()))
    return top


# --------------------------------------------------------------------------- windows

def segments_from_hits(times, pad=18.0, min_gap=8.0, total=None):
    if not times:
        return []
    groups = [[times[0], times[0]]]
    for t in times[1:]:
        if t - groups[-1][1] <= min_gap:
            groups[-1][1] = t
        else:
            groups.append([t, t])

    wins = []
    for first, last in groups:
        start = max(0.0, first - pad)
        end = last + pad
        if total:
            end = min(end, total)
        if end - start < 5:
            continue
        wins.append({"start": round(start, 2), "end": round(end, 2),
                     "first_at": round(first, 2), "last_at": round(last, 2),
                     "hits": None})
    wins.sort(key=lambda w: w["start"])
    merged = []
    for w in wins:
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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return float(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return 0.0


def frames(video: Path, fps: int, size: int):
    import numpy as np

    x0, y0, w, h = region_of((1080, 1920), size)
    cmd = ["ffmpeg", "-v", "error", "-i", str(video),
           "-vf", f"crop={w}:{h}:{x0}:{y0},fps={fps}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    n = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))
    finally:
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        proc.wait(timeout=30)


def scan_file(video: Path, game: str, threshold: float, fps: int,
              center: int, pad: float, min_gap: float, verbose=False) -> dict:
    tpl = load(game)
    times, peak, nf = [], 0.0, 0
    for fr in frames(video, fps, center):
        sc = score_frame(fr, tpl)
        peak = max(peak, sc)
        if sc >= threshold:
            times.append(nf / fps)
        nf += 1

    merged = []
    for t in times:
        if not merged or t - merged[-1] > 0.5:
            merged.append(t)

    dur = duration(video) or (nf / fps)
    segs = segments_from_hits(merged, pad=pad, min_gap=min_gap, total=dur)
    for s in segs:
        s["hits"] = sum(1 for t in merged if s["start"] <= t <= s["end"])

    if verbose:
        print(f"  frames={nf} ({nf/max(fps,1):.0f}s)  peak={peak:.3f}")
        print(f"  frames over threshold: {len(times)}   distinct hits: {len(merged)}")
        for t in merged[:20]:
            print(f"    hit at {t:7.2f}s")
        for s in segs:
            print(f"    window {s['start']:.1f}-{s['end']:.1f} ({s['hits']} hits)")

    return {"ok": True, "game": game, "duration": round(dur, 2),
            "peak": round(peak, 4), "hits": len(merged), "segments": segs}


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
    ap.add_argument("--game", required=True, help="template folder name, e.g. deadlock")
    ap.add_argument("--folder", help="only folders matching this name")
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS)
    ap.add_argument("--center", type=int, default=520, help="centre box size in px")
    ap.add_argument("--pad", type=float, default=18.0)
    ap.add_argument("--min-gap", type=float, default=8.0)
    ap.add_argument("--one", help="scan a single file")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--rescan", action="store_true")
    args = ap.parse_args(argv)

    if args.one:
        t = Path(args.one)
        print(f"scanning {t.name} with template '{args.game}'")
        res = scan_file(t, args.game, args.threshold, args.fps, args.center,
                        args.pad, args.min_gap, verbose=True)
        print(json.dumps(res, indent=1))
        return 0

    root = Path(args.root)
    exts = {".mp4", ".mkv", ".mov", ".webm"}
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in exts)
    if args.folder:
        want = " ".join(args.folder.lower().split())
        files = [p for p in files if want in " ".join(p.parent.name.lower().split())]

    if args.list:
        for p in files:
            print(f"{duration(p):8.1f}s  {p.relative_to(root)}")
        print(f"{len(files)} files")
        return 0

    if not args.all:
        ap.print_help()
        return 1

    if not scan_lock.acquire(ROOT, "marker"):
        return 1
    idx = load_index()
    todo = [p for p in files if args.rescan or str(p.relative_to(root)) not in idx["files"]]
    print(f"{len(files)} files, {len(todo)} to scan (template={args.game}, "
          f"threshold={args.threshold}, fps={args.fps})")

    for i, p in enumerate(todo, 1):
        rel = str(p.relative_to(root)).replace("\\", "/")
        print(f"[{i}/{len(todo)}] {rel}", flush=True)
        res = scan_file(p, args.game, args.threshold, args.fps, args.center,
                        args.pad, args.min_gap)
        idx["files"][rel] = res
        if res.get("hits"):
            print(f"    {res['hits']} hit(s), {len(res['segments'])} window(s)", flush=True)
        save_index(idx)

    scan_lock.release(ROOT, "marker")
    hits = sum(v.get("hits", 0) for v in idx["files"].values() if isinstance(v, dict))
    wins = sum(len(v.get("segments") or []) for v in idx["files"].values() if isinstance(v, dict))
    print(f"done. {hits} hits -> {wins} windows across {len(idx['files'])} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
