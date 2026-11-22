import os
"""Verify the rendered promo: freeze at cuts, and flash accents.

Freeze test: decode at output frame rate, compute mean absolute difference
between consecutive frames. A stall shows as a run of near-zero differences.
Input seeking used to hold the keyframe for 1-2 frames at the head of every cut,
so this is the direct measurement of the thing reported as "freezing between
scenes".

Flash test: count runs of near-black frames, and where they land.
"""

import os
import subprocess

import numpy as np

WORK = os.path.dirname(os.path.abspath(__file__))
FFMPEG = r"C:\ffmpeg\ffmpeg.exe"
W, H, FPS = 160, 90, 60


def load(path):
    raw = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path,
         "-vf", f"scale={W}:{H},format=gray,fps={FPS}", "-f", "rawvideo", "-"],
        capture_output=True, timeout=1800).stdout
    n = len(raw) // (W * H)
    return np.frombuffer(raw[: n * W * H], dtype=np.uint8).reshape(n, H, W).astype(np.float32)


def report(path, label):
    print("=" * 66)
    print(f"{label}  {os.path.basename(path)}")
    if not os.path.isfile(path):
        print("  missing")
        return
    fr = load(path)
    d = np.abs(np.diff(fr, axis=0)).reshape(len(fr) - 1, -1).mean(axis=1)
    lum = fr.reshape(len(fr), -1).mean(axis=1)
    print(f"  frames {len(fr)} at {FPS}fps")

    # a stall is a run of frames that barely differ
    frozen = d < 0.35
    runs, i = [], 0
    while i < len(frozen):
        if frozen[i]:
            j = i
            while j + 1 < len(frozen) and frozen[j + 1]:
                j += 1
            runs.append((i, j - i + 2))   # +2 dt frames
            i = j + 1
        else:
            i += 1
    long_runs = [r for r in runs if r[1] >= 2]
    print(f"  near-identical frame pairs: {int(frozen.sum())} "
          f"({100*frozen.mean():.1f}% of frames)")
    print(f"  stall runs >=2 frames: {len(long_runs)}")
    for start, ln in long_runs[:10]:
        print(f"    t={start/FPS:6.2f}s  {ln} frames frozen")
    if not long_runs:
        print("    none - cuts are not stalling")

    dark = lum < 22
    druns, i = [], 0
    while i < len(dark):
        if dark[i]:
            j = i
            while j + 1 < len(dark) and dark[j + 1]:
                j += 1
            druns.append((i / FPS, j - i + 1))
            i = j + 1
        else:
            i += 1
    print(f"  black flashes: {len(druns)}")
    for t, n in druns[:12]:
        print(f"    t={t:6.2f}s  {n} frame(s)")


if __name__ == "__main__":
    for f, lab in (("promo-black-sky-v3.mp4", "v3 (input seek, no flashes)"),
                   ("promo-v4.mp4", "v4 (exact seek + flashes)")):
        report(os.path.join(WORK, f), lab)
        print()
