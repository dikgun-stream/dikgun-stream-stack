"""Laughter scanner.

Finds moments of laughter in the local recordings and emits clip windows around
them, so the stream can play "the good bits" without hand-picking.

Detection is a real audio-tagging model (YAMNet, AudioSet ontology), not a
heuristic: it scores 521 sound classes per 0.48 s window and we read the
laughter family (Laughter / Giggle / Snicker / Chuckle).

Usage:
    python laughter_scan.py --list                 # files and durations
    python laughter_scan.py --one <file>           # scan a single file, verbose
    python laughter_scan.py --all                  # scan everything, write index
    python laughter_scan.py --all --threshold 0.08

Output: laughter_index.json next to this file, consumed by /api/clips.

Cost note: extracting 16 kHz mono is cheap (audio-only, no video decode), and
YAMNet on CPU runs comfortably faster than realtime, but 100 GB of source means
an overnight job. The index is incremental -- already-scanned files are skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

# YAMNet operates on 0.48 s windows
WINDOW = 0.48
LAUGH_KEYS = ("laughter", "giggle", "snicker", "chuckle", "chortle")

INDEX_PATH = Path(__file__).with_name("laughter_index.json")
MODEL_DIR = Path(__file__).parent.parent / "obs-build" / "yamnet"


# --------------------------------------------------------------------------- audio

def extract_audio(video: Path, sr: int = 16000) -> "list[float] | None":
    """Decode a video's audio track to mono float samples."""
    import numpy as np

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(video),
             "-vn", "-ac", "1", "-ar", str(sr), "-f", "wav", wav_path],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not os.path.getsize(wav_path):
            return None
        with wave.open(wav_path, "rb") as w:
            n = w.getnframes()
            raw = w.readframes(n)
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


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


# --------------------------------------------------------------------------- model

_MODEL = None
_NAMES = None
_IDX = None


def load_model():
    global _MODEL, _NAMES, _IDX
    if _MODEL is not None:
        return _MODEL, _IDX
    import csv

    import tensorflow as tf

    _MODEL = tf.saved_model.load(str(MODEL_DIR))
    names = []
    with open(MODEL_DIR / "assets" / "yamnet_class_map.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            names.append(row["display_name"])
    _NAMES = names
    _IDX = [i for i, n in enumerate(names)
            if any(k in n.lower() for k in LAUGH_KEYS)]
    return _MODEL, _IDX


def laughter_curve(audio) -> "list[float]":
    """Per-0.48s laughter probability (max over the laughter class family)."""
    import numpy as np

    model, idx = load_model()
    scores, _, _ = model(audio)
    scores = scores.numpy()
    if not idx:
        return [0.0] * len(scores)
    return scores[:, idx].max(axis=1).tolist()


# --------------------------------------------------------------------------- scan

def segments_from_curve(curve, threshold, min_gap=8.0, pad=90.0, total=None):
    """Turn a laughter curve into playable windows.

    Consecutive detections closer than min_gap are one event. Each event becomes
    a window of +/- pad seconds -- the default 90 s either side gives the
    three-minute window that was asked for.

    The +/- pad windows then get merged, because a 3-minute window around each
    of three events 30 s apart would otherwise be three near-identical windows
    all starting at zero.
    """
    hits = [i * WINDOW for i, v in enumerate(curve) if v >= threshold]
    if not hits:
        return []

    groups = [[hits[0], hits[0]]]
    for t in hits[1:]:
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
            "laugh_at": round(first, 2),
            "laugh_until": round(last, 2),
        })

    # merge overlapping windows so each moment is played once
    windows.sort(key=lambda w: w["start"])
    merged: list[dict] = []
    for w in windows:
        if merged and w["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], w["end"])
            merged[-1]["laugh_until"] = max(merged[-1]["laugh_until"], w["laugh_until"])
        else:
            merged.append(dict(w))
    return merged


def scan_file(video: Path, threshold: float, verbose: bool = False) -> dict:
    audio = extract_audio(video)
    if audio is None or len(audio) < 16000 * 5:
        return {"ok": False, "reason": "no audio track"}

    curve = laughter_curve(audio)
    dur = len(audio) / 16000.0
    peak = max(curve) if curve else 0.0
    segs = segments_from_curve(curve, threshold, total=dur)

    if verbose:
        import numpy as np
        c = np.array(curve)
        print(f"  duration {dur:.1f}s   windows {len(curve)}")
        print(f"  laughter prob: max={c.max():.4f} p99={np.percentile(c,99):.4f} "
              f"mean={c.mean():.5f}  (>={threshold}: {(c>=threshold).sum()} windows)")
        for s in segs[:10]:
            print(f"    laugh at {s['laugh_at']:.1f}s -> play {s['start']:.1f}-{s['end']:.1f}s")

    for s in segs:
        s["peak"] = round(peak, 4)
    return {"ok": True, "duration": round(dur, 2), "peak": round(peak, 4), "segments": segs}


# --------------------------------------------------------------------------- index

def load_index() -> dict:
    try:
        if INDEX_PATH.is_file():
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {"files": {}}


def save_index(idx: dict) -> None:
    INDEX_PATH.write_text(json.dumps(idx, indent=1), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("DIKGUN_CLIPS", r"C:\Users\JR\Videos"))
    ap.add_argument("--threshold", type=float, default=0.10)
    ap.add_argument("--one", help="scan a single file")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--rescan", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"clip root not found: {root}")
        return 2

    exts = {".mp4", ".mkv", ".mov", ".webm"}
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in exts)

    if args.list:
        for p in files:
            print(f"{duration(p):8.1f}s  {p.relative_to(root)}")
        return 0

    if args.one:
        target = Path(args.one)
        print(f"scanning {target.name}")
        print(f"  size {target.stat().st_size/1e6:.0f} MB")
        res = scan_file(target, args.threshold, verbose=True)
        print(f"  result: {res}")
        return 0 if res.get("ok") else 1

    if not args.all:
        ap.print_help()
        return 1

    idx = load_index()
    todo = [p for p in files
            if args.rescan or str(p.relative_to(root)) not in idx["files"]]
    print(f"{len(files)} files, {len(todo)} to scan, threshold {args.threshold}")

    for i, p in enumerate(todo, 1):
        rel = str(p.relative_to(root)).replace("\\", "/")
        print(f"[{i}/{len(todo)}] {rel}", flush=True)
        res = scan_file(p, args.threshold)
        idx["files"][rel] = res
        if res.get("ok") and res["segments"]:
            print(f"    {len(res['segments'])} window(s), peak {res['peak']}", flush=True)
        save_index(idx)

    total = sum(len(v.get("segments") or []) for v in idx["files"].values())
    print(f"done. {total} laughter windows across {len(idx['files'])} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
