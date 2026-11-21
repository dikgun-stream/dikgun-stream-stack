"""Check for stalls at the actual cut points, not everywhere.

A static gameplay shot produces near-identical frames too, so a global stall count
is meaningless. What matters is whether the frame AFTER a cut is a repeat of the
frame BEFORE it - that is the keyframe stall signature, and it is what reads as a
freeze at the edit point.

Cut times come from the concat list the build wrote: the cumulative duration of
every listed file, which includes the 2-frame flash segments.
"""

import os
import subprocess

import numpy as np

WORK = r"C:\Users\JR\Documents\deepseek-harness\default-workspace\obs-build\promo"
SEGS = os.path.join(WORK, "segments")
FFMPEG = r"C:\ffmpeg\ffmpeg.exe"
FFPROBE = r"C:\ffmpeg\ffprobe.exe"
W, H, FPS = 160, 90, 60


def dur(p):
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", p], capture_output=True, text=True,
                         timeout=120).stdout.strip()
    return float(out or 0)


def cut_times():
    listing = os.path.join(SEGS, "list.txt")
    if not os.path.isfile(listing):
        return []
    times, acc = [], 0.0
    for line in open(listing, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("file '"):
            continue
        name = line[6:].rstrip("'")
        p = os.path.join(SEGS, name)
        if not os.path.isfile(p):
            continue
        d = dur(p)
        acc += d
        times.append(acc)
    return times[:-1]


def frames(path):
    raw = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path,
         "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=1800).stdout
    n = len(raw) // (W * H)
    return np.frombuffer(raw[: n * W * H], dtype=np.uint8).reshape(n, H, W).astype(np.float32)


def main():
    promo = os.path.join(WORK, "promo-v4.mp4")
    cuts = cut_times()
    print(f"  cuts from the concat list: {len(cuts)}")
    fr = frames(promo)
    print(f"  decoded {len(fr)} frames")

    # baseline: how different are neighbouring frames generally
    d_all = np.abs(np.diff(fr, axis=0)).reshape(len(fr) - 1, -1).mean(axis=1)
    base = np.median(d_all)

    frozen_at_cut, moved_at_cut = 0, 0
    worst = []
    for c in cuts:
        f = int(round(c * FPS))
        if f < 2 or f + 2 >= len(fr):
            continue
        # difference across the cut, and the very next frame
        across = float(np.abs(fr[f] - fr[f - 1]).mean())
        after = float(np.abs(fr[f + 1] - fr[f]).mean())
        if across < 0.35 and after < 0.35:
            frozen_at_cut += 1
            worst.append((c, across, after))
        else:
            moved_at_cut += 1

    print(f"  median frame difference overall: {base:.2f}")
    print(f"  cuts where the picture did NOT change: {frozen_at_cut}")
    print(f"  cuts where it did: {moved_at_cut}")
    for c, a, b in worst[:10]:
        print(f"    t={c:6.2f}s  across={a:.2f} after={b:.2f}")
    if not frozen_at_cut:
        print("    -> no cut is freezing on the previous frame")


if __name__ == "__main__":
    main()
