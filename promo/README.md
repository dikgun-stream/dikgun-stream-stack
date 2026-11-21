# Promo video generator

Builds a ~1 minute channel promo from the clip library plus a music track.

```powershell
python promo_build.py --music song.mp3 --duration 60 --dry-run
python promo_build.py --music song.mp3 --duration 60 --games "Rainbow Six Siege,Deadlock,Apex Legends" --out promo.mp4
```

`--dry-run` prints the planned cut list without rendering, which is the fast way
to sanity-check the pacing before spending time on an encode.

Measured on this machine: 22 cuts over 60s renders in about 18 seconds.

## How it decides

1. Analyse the track - onsets, low-band kicks, short-window RMS envelope.
2. Smooth the loud/quiet split over ~1.5s so a drop is a continuous section
   rather than a flicker.
3. **Build-up** = quiet sections. One sustained take per section, 5s from a
   single recording, NOT beat-synced. The brief is "regular gameplay", so it is
   deliberately not cut to the grid.
4. **Drops** = loud sections. One kill per beat, cut to the coarsened onset grid.
5. Game audio is kept at -18 dB under the music, because shots and footsteps
   still need to be there.

## Why the grid is coarsened

Analysis of the two existing edits (`analyse.py`) measured:

| edit | length | cuts | median shot | tempo | cuts within 80ms of an onset | of a kick |
|---|---|---|---|---|---|---|
| Montage? | 223s | 46 | 1.30s | 129 BPM | **60.9%** | 34.8% |
| yeah right | 174s | 28 | 1.73s | 115 BPM | **57.1%** | 21.4% |

Cuts land on musical onsets far more often than on kicks specifically, and the
raw onset rate is ~3/second. Cutting on every onset would strobe at 300ms. The
grid is therefore floored at 0.85s, which puts the median shot near the 1.3-1.7s
actually measured.

## Requirements

- ffmpeg and ffprobe on PATH
- The overlay server running (kill windows come from `/api/clips/highlights`)
- numpy
- A music file. **Not** a streaming URL - a file you hold.

## Known limits

- Kill detection exists for R6, Deadlock, Battlefield 6, Apex and The Finals.
  Tarkov has none.
- The generator has no idea whether a shot *looks* good; it trusts the kill
  index. Anything the scanners missed cannot be chosen.
- Output is 1080p60 at CRF 20, roughly 150 MB per minute. Raise the CRF in
  `extract()` for a smaller file.
