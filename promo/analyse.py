"""Analyse the two existing edits to learn their structure.

What this measures, and why each one matters for building a generator:

  duration        - how long a promo actually is
  BPM             - the tempo to snap cuts to
  kick times      - low-band onsets; these are what "kills on the kicks" means
  cut times       - from frame-difference scene detection
  cut-on-beat %   - whether cuts actually land on beats, or somewhere else

If cuts cluster on beats, the generator can place kills at beat times. If they do
not, the edit is driven by something else and this approach would be wrong.

No scipy/librosa here, so onset detection is done with plain numpy: FFT per hop,
low-band energy for kicks, spectral flux for general onsets.
"""

import json
import os
import subprocess
import sys
import tempfile

import numpy as np

WORK = r"C:\Users\JR\Documents\deepseek-harness\default-workspace\obs-build\promo"
FFMPEG = r"C:\ffmpeg\ffmpeg.exe"
FFPROBE = r"C:\ffmpeg\ffprobe.exe"

SR = 22050
HOP = 512
N_FFT = 2048


def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "format=duration:stream=width,height,codec_type,codec_name",
         "-of", "json", path],
        capture_output=True, text=True, timeout=60).stdout
    return json.loads(out)


def load_audio(path):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
        wav = t.name
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "wav", wav],
        capture_output=True, timeout=600)
    import wave
    with wave.open(wav, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    os.unlink(wav)
    return data.astype(np.float32) / 32768.0


def spectral(path):
    """Return (hop times, low-band energy, full-band flux)."""
    x = load_audio(path)
    n_frames = 1 + max(0, (len(x) - N_FFT)) // HOP
    window = np.hanning(N_FFT).astype(np.float32)

    low_energy = np.zeros(n_frames, dtype=np.float32)
    flux = np.zeros(n_frames, dtype=np.float32)
    prev = None
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    low_mask = freqs < 150.0          # kick / bass band

    for i in range(n_frames):
        chunk = x[i * HOP: i * HOP + N_FFT]
        if len(chunk) < N_FFT:
            break
        mag = np.abs(np.fft.rfft(chunk * window))
        low_energy[i] = float((mag[low_mask] ** 2).sum())
        if prev is not None:
            d = mag - prev
            flux[i] = float(np.clip(d, 0, None).sum())
        prev = mag

    times = np.arange(len(low_energy)) * HOP / SR
    return times, low_energy, flux


def pick_peaks(signal, times, min_gap=0.20, sensitivity=1.4):
    """Onsets: local maxima above a rolling median threshold."""
    if len(signal) < 8:
        return np.array([])
    win = max(9, int(0.5 * SR / HOP) | 1)
    from numpy.lib.stride_tricks import sliding_window_view
    padded = np.pad(signal, win // 2, mode="edge")
    roll = sliding_window_view(padded, win).mean(axis=1)
    thr = roll * sensitivity
    thresh = signal > thr
    peaks = []
    last = -1e9
    for i in np.flatnonzero(thresh):
        if i == 0 or i == len(signal) - 1:
            continue
        if signal[i] < signal[i - 1] or signal[i] < signal[i + 1]:
            continue
        t = times[i]
        if t - last < min_gap:
            continue
        peaks.append(t)
        last = t
    return np.array(peaks)


def estimate_bpm(kick_times):
    if len(kick_times) < 4:
        return None, 0.0
    iv = np.diff(kick_times)
    iv = iv[(iv > 0.25) & (iv < 2.0)]
    if not len(iv):
        return None, 0.0
    med = float(np.median(iv))
    bpm = 60.0 / med
    # fold into a musical range
    while bpm < 70:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    return bpm, med


def detect_cuts(path, threshold=0.30):
    """Scene changes from ffmpeg's scene score, which is cheaper than decoding."""
    # Two traps here. The comma inside the filter expression must be escaped in
    # list form (no shell to quote it), and showinfo logs at INFO level, so
    # -v error silences the very output being parsed.
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostats", "-v", "info", "-i", path,
         "-vf", "select=gt(scene\\," + str(threshold) + "),showinfo",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=1800).stderr
    times = []
    for line in out.splitlines():
        if "pts_time:" not in line:
            continue
        try:
            t = float(line.split("pts_time:")[1].split()[0])
            times.append(t)
        except (IndexError, ValueError):
            pass
    return np.array(sorted(times))


def analyse(path, label):
    print("=" * 70)
    print(f"{label}  ->  {os.path.basename(path)}")
    meta = probe(path)
    dur = float(meta.get("format", {}).get("duration") or 0)
    vs = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), {})
    print(f"  duration : {dur:.1f} s")
    if vs:
        print(f"  video    : {vs.get('width')}x{vs.get('height')}  {vs.get('codec_name')}")

    times, low, flux = spectral(path)
    kicks = pick_peaks(low, times, min_gap=0.18, sensitivity=1.35)
    onsets = pick_peaks(flux, times, min_gap=0.10, sensitivity=1.30)
    bpm, med_iv = estimate_bpm(kicks)
    print(f"  kicks    : {len(kicks)} detected")
    print(f"  onsets   : {len(onsets)}")
    if bpm:
        print(f"  tempo    : {bpm:.1f} BPM (median kick interval {med_iv:.3f}s)")

    cuts = detect_cuts(path)
    print(f"  cuts     : {len(cuts)}")

    if len(cuts) and len(kicks):
        beat_grid = np.concatenate([kicks]) if len(kicks) else np.array([])
        if len(beat_grid):
            # nearest kick to each cut
            d = np.abs(cuts[:, None] - beat_grid[None, :]).min(axis=1)
            for tol in (0.04, 0.08, 0.12, 0.20):
                pct = 100.0 * (d <= tol).mean()
                print(f"    cuts within {tol*1000:3.0f}ms of a kick: {pct:5.1f}%")
        # and against every onset
        d2 = np.abs(cuts[:, None] - onsets[None, :]).min(axis=1)
        print(f"    cuts within  80ms of ANY onset : {100.0 * (d2 <= 0.08).mean():5.1f}%")

    # where do the cuts sit relative to the length of the video
    if len(cuts):
        gaps = np.diff(np.concatenate([[0], cuts, [dur]]))
        print(f"  shot len : median {np.median(gaps):.2f}s  min {gaps.min():.2f}s  max {gaps.max():.2f}s")

    return {
        "file": path, "duration": dur, "kicks": len(kicks), "onsets": len(onsets),
        "bpm": bpm, "cuts": len(cuts),
        "cut_times": cuts.tolist(), "kick_times": kicks.tolist(),
    }


if __name__ == "__main__":
    files = sorted(f for f in os.listdir(WORK) if f.lower().endswith((".mp4", ".mkv", ".webm")))
    results = []
    for f in files:
        label = "yeah right - joji" if f.startswith("xd2pWbCS3f0") else "Montage?"
        results.append(analyse(os.path.join(WORK, f), label))

    with open(os.path.join(WORK, "analysis.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    print()
    print("saved analysis.json")
