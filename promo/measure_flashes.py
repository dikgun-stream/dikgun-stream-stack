"""Measure how the two reference edits handle transitions.

For both files:

  black flashes   - runs of near-zero luma; the "cut to black on a kick" trick.
                    Their times are compared against detected onsets and kicks.
  cut classification - every detected scene change, classified by the frames either
                    side of it: BLACK (a flash), WHITE, or HARD (straight cut).
  flash rate      - how often the black trick is used, which is the recipe.

The point is to turn "it looks elegant" into numbers a generator can reproduce.
"""

import os
import subprocess
import tempfile
import wave

import numpy as np

WORK = r"C:\Users\JR\Documents\deepseek-harness\default-workspace\obs-build\promo"
FFMPEG = r"C:\ffmpeg\ffmpeg.exe"

FILES = [
    ("ps38LVKlQm4", "Montage?", (35, 100)),
    ("xd2pWbCS3f0", "yeah right", (30, 95)),
]

SR, HOP, N_FFT = 22050, 512, 2048
FPS = 15
BLACK = 22.0
WHITE = 232.0


def find(name):
    # prefer the 1080p copy; the 360p one was only ever a workaround for the
    # android client capping resolution, which the default clients do not
    for prefix in ("1080-", "hd-", ""):
        for f in os.listdir(WORK):
            if f.startswith(prefix) and name in f and f.endswith(".mp4"):
                return os.path.join(WORK, f)
    return None


def onsets(path):
    wav = tempfile.mktemp(suffix=".wav")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", path, "-ac", "1",
                    "-ar", str(SR), "-f", "wav", wav], capture_output=True, timeout=900)
    with wave.open(wav, "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    os.unlink(wav)
    n = 1 + max(0, (len(x) - N_FFT)) // HOP
    win = np.hanning(N_FFT).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    low = freqs < 150.0
    flux = np.zeros(n, np.float32)
    k = np.zeros(n, np.float32)
    prev = None
    for i in range(n):
        c = x[i * HOP: i * HOP + N_FFT]
        if len(c) < N_FFT:
            break
        m = np.abs(np.fft.rfft(c * win))
        k[i] = float((m[low] ** 2).sum())
        if prev is not None:
            flux[i] = float(np.clip(m - prev, 0, None).sum())
        prev = m
    t = np.arange(len(flux)) * HOP / SR
    return t, flux, k


def peaks(sig, times, gap, sens):
    from numpy.lib.stride_tricks import sliding_window_view
    if len(sig) < 8:
        return np.array([])
    w = max(9, int(0.5 * SR / HOP) | 1)
    roll = sliding_window_view(np.pad(sig, w // 2, mode="edge"), w).mean(axis=1)
    mask = sig > roll * sens
    out, last = [], -1e9
    for i in np.flatnonzero(mask):
        if i in (0, len(sig) - 1) or sig[i] < sig[i-1] or sig[i] < sig[i+1]:
            continue
        if times[i] - last < gap:
            continue
        out.append(float(times[i]))
        last = float(times[i])
    return np.array(out)


def luma_and_cuts(path, t0, t1):
    dark = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", str(t0), "-t", str(t1 - t0), "-i", path,
         "-vf", f"fps={FPS},scale=160:90,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=900).stdout
    n = len(dark) // (160 * 90)
    if n == 0:
        return None, None, None
    arr = np.frombuffer(dark[: n * 160 * 90], dtype=np.uint8).reshape(n, 90, 160).astype(np.float32)
    means = arr.reshape(n, -1).mean(axis=1)
    times = t0 + np.arange(n) / FPS

    scene = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostats", "-v", "info", "-ss", str(t0),
         "-t", str(t1 - t0), "-i", path, "-vf", "select=gt(scene\\,0.25),showinfo",
         "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=1800).stderr
    cuts = []
    for line in scene.splitlines():
        if "pts_time:" in line:
            try:
                cuts.append(t0 + float(line.split("pts_time:")[1].split()[0]))
            except (IndexError, ValueError):
                pass
    return times, means, sorted(cuts)


def report(name, label, window):
    path = find(name)
    print("=" * 68)
    if not path:
        print(f"{label}: file not found")
        return
    print(f"{label}   ({os.path.basename(path)})")
    t_all, flux, kenergy = onsets(path)
    on = peaks(flux, t_all, 0.10, 1.30)
    kicks = peaks(kenergy, t_all, 0.18, 1.35)
    print(f"  onsets {len(on)}   kicks {len(kicks)}")

    t0, t1 = window
    times, means, cuts = luma_and_cuts(path, t0, t1)
    if times is None:
        print("  decode failed")
        return

    dk = means < BLACK
    runs, i = [], 0
    while i < len(dk):
        if dk[i]:
            j = i
            while j + 1 < len(dk) and dk[j + 1]:
                j += 1
            runs.append((float(times[i]), float(times[j]), j - i + 1))
            i = j + 1
        else:
            i += 1

    dur = t1 - t0
    print(f"  window {t0}-{t1}s   mean luma {means.mean():.0f}")
    print(f"  black flashes {len(runs)}  ->  one every {dur/max(1,len(runs)):.1f}s")
    hit_onset = 0
    for a, b, n in runs:
        d_on = np.abs(on - a).min() if len(on) else 99
        d_k = np.abs(kicks - a).min() if len(kicks) else 99
        if d_on <= 0.07:
            hit_onset += 1
        mark = "ONSET" if d_on <= 0.07 else ""
        mark += " KICK" if d_k <= 0.07 else ""
        print(f"    {a:7.2f}s  {n:2}f   +{d_on*1000:4.0f}ms  {mark}")
    if runs:
        print(f"    -> {100*hit_onset/len(runs):.0f}% of flashes land on an onset")

    kinds = {"BLACK": 0, "WHITE": 0, "HARD": 0}
    for c in cuts:
        idx = min(range(len(times)), key=lambda i: abs(times[i] - c))
        v = means[idx]
        if v < BLACK:
            kinds["BLACK"] += 1
        elif v > WHITE:
            kinds["WHITE"] += 1
        else:
            kinds["HARD"] += 1
    print(f"  cuts {len(cuts)}  ->  {kinds}")
    print(f"  cut rate: one every {dur/max(1,len(cuts)):.1f}s")
    print()


if __name__ == "__main__":
    for name, label, win in FILES:
        report(name, label, win)
