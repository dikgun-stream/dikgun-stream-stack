# Promo video generator

Builds a ~1 minute channel promo from the clip library plus a music track.

```powershell
python render_stingers.py                        # the DIKGUN cards
python promo_build.py --music song.mp3 --duration 90 --dry-run
python promo_build.py --music song.mp3 --duration 90 `
  --games "Rainbow Six Siege,Deadlock,Apex Legends,Battlefield 6,The Finals" `
  --prefer "Battlefield 6" --out promo.mp4
```

## Letter stingers

`render_stingers.py` draws six flat 2D cards with PIL - the accumulated word,
adding one letter per stage:

```
D    DI    DIK    DIKG    DIKGU    DIKGUN
```

Colours follow the brand: DIK in cream `#ECE4D2`, GUN in red `#C8161D`. Each card
is 0.70s at 60fps (about one beat at 120bpm), and `promo_build.py` splices them in
as accents, encoding a silent audio track so they cannot desync the music.

Why flat 2D and not 3D:

* the 3D version had letters sunk into a ground plane, then a camera framing bug
  that dropped every falling letter through the centre of the word, then a crack
  mask calibrated to the wrong range of the displacement map
* a flat card has no ground, no camera and no perspective, so none of that can
  happen - and every stage shares one baseline, verified at top=414 / bottom=628

Layout is **left-aligned at x=193 with a constant font size**, so earlier letters
never move when a new one appears. Centring the group each stage shifts every
previous letter by half a glyph, which reads as misalignment when the cards are
intercut with gameplay.

## Timing

A stinger occupies video time the music does not wait for, so `promo_build.py`
subtracts its duration from the following shots (carrying the remainder forward,
because one 0.7s stinger cannot be absorbed by a 0.7s shot). Without this the cut
drifted +7.68s over 90s; it is now -0.06s.

## Requirements

- ffmpeg and ffprobe on PATH
- The overlay server running (kill windows come from `/api/clips/highlights`)
- numpy, opencv-python, pillow
- A music file. **Not** a streaming URL - a file you hold.

## Known limits

- Kill detection exists for R6, Deadlock, Battlefield 6, Apex and The Finals.
  Tarkov has none.
- The generator cannot tell whether a shot *looks* good; it trusts the kill index.
- Output is 1080p60 at CRF 20, roughly 200 MB per 90 seconds.

## Outro

`render_outro.py` builds the end card, then `promo_build.py --outro outro.mp4`
appends it and shortens the gameplay to make room:

| t | what |
|---|---|
| 0.30s | DIKGUN snaps in (on a kick) |
| 2.00s | D and G slide together (on a kick); I K U N vanish |
| 2.18s | impact - flash, shake, the DG mark appears |
| 2.60s | "BACK AND BETTER THAN EVER" fades in |
| 6.80s -> 8.00s | fade to black |

The mark is the real brand artwork. `brand/dg-mark.svg` is three `<polygon>`
elements, so it is drawn with `ImageDraw.polygon` rather than rasterising the SVG.
The brand mark is D in ink and G in red; on a dark frame an ink D disappears, so
the D is inverted to cream and the red G is kept - which also matches the
DIK-cream / GUN-red rule used in the letter cards.

Encode the frames with a silent audio track before passing them in:

```powershell
python render_outro.py --out outro_frames --dur 8.0 --beats 0.30,2.00
ffmpeg -framerate 60 -i outro_frames/f_%04d.png `
  -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 `
  -vf "scale=1920:1080,setsar=1" -c:v libx264 -preset veryfast -crf 19 `
  -pix_fmt yuv420p -c:a aac -b:a 192k -ar 48000 -ac 2 -shortest `
  -movflags +faststart outro.mp4
```
