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


def region_for(game: str, center: int):
    """Search box in full-frame pixels.

    A per-template region.json overrides the default centre crop. Not every
    marker is centred: Battlefield's KILL banner sits left of centre at
    x~530, so a centre crop never even contains it.
    """
    cfg = TPL_DIR / game.lower() / "region.json"
    if cfg.is_file():
        try:
            r = json.loads(cfg.read_text(encoding="utf-8"))
            return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
        except Exception:  # noqa: BLE001
            pass
    s = min(center, 1080, 1920)
    return (1920 - s) // 2, (1080 - s) // 2, s, s


_MODEL = None
_MASK_KIND = None
_TEXT_CFG = None


def load(game: str):
    """Load the marker model for a game.

    Two kinds of marker are supported:

    1. Icon    -- a mask.png of the marker's shape/colour, matched against a
                  colour mask of the frame (Deadlock's red ring).
    2. Text    -- a text.json naming a literal on-screen word, read with OCR.
                  Battlefield announces kills with a boxed "KILL" label, and a
                  pixel template cannot separate that from the rest of a bright,
                  snowy HUD (measured: 56k white pixels in the crop, best
                  correlation 0.58 on the marker's own frame).
    """
    global _MODEL, _MASK_KIND, _TEXT_CFG
    import cv2
    import numpy as np

    d = TPL_DIR / game.lower()
    text_cfg = d / "text.json"
    if text_cfg.is_file():
        try:
            _TEXT_CFG = json.loads(text_cfg.read_text(encoding="utf-8"))
            _TEXT_CFG["_needle"] = str(_TEXT_CFG.get("text", "KILL")).upper()
            print(f"  marker '{game}' -> OCR, looking for '{_TEXT_CFG['_needle']}'")
            return None
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"bad text.json for {game}: {exc}")

    _TEXT_CFG = None
    mask = cv2.imread(str(template_path(game)), cv2.IMREAD_GRAYSCALE)
    tpl = (mask > 127).astype(np.uint8)
    _MODEL = tpl

    kind = "red"
    ref_path = d / "ref.png"
    if ref_path.is_file():
        ref = cv2.imread(str(ref_path))
        if ref is not None and ref.shape[:2] == tpl.shape[:2]:
            sel = tpl > 0
            if sel.any():
                b, g, r = [float(ref[:, :, i][sel].mean()) for i in range(3)]
                kind = "red" if r > max(g, b) + 25 else "white"
    _MASK_KIND = kind
    print(f"  marker '{game}' -> colour mask: {kind}")
    return _MODEL


def ocr_hit(frame) -> float:
    """Return 1.0 if the configured word is readable in this frame."""
    import cv2
    import pytesseract
    from PIL import Image

    if not _TEXT_CFG:
        return 0.0
    needle = _TEXT_CFG["_needle"]
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # upscale: the label is ~14px tall in a 1080p frame
        big = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        txt = pytesseract.image_to_string(
            Image.fromarray(big), config="--psm 11").upper()
        return 1.0 if needle in txt.replace("|", " ") else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


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

    if _MASK_KIND == "white":
        # bright and desaturated: white HUD text and box outlines
        mask = ((v > 150) & (s < 90)).astype(np.uint8)
    else:
        # saturated red
        mask = (((h < 12) | (h > 166)) & (s > 130) & (v > 90)).astype(np.uint8)

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


def frames(video: Path, fps: int, size: int, region=None):
    import numpy as np

    x0, y0, w, h = region if region else region_of((1080, 1920), size)
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
    region = region_for(game, center)
    times, peak, nf = [], 0.0, 0
    for fr in frames(video, fps, center, region):
        sc = ocr_hit(fr) if _TEXT_CFG else score_frame(fr, tpl)
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
