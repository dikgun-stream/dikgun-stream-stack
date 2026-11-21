# Cheat sheet

Everything runs from this folder:

```powershell
cd C:\Users\JR\Documents\deepseek-harness\default-workspace\obs-build\promo
```

One prerequisite: the overlay server supplies the kill windows.

```powershell
cd ..\..\overlay-server; python -m uvicorn server:app --host 127.0.0.1 --port 7475
```

---

## The only difference: `-Mode`

| | What you get |
|---|---|
| `-Mode promo` *(default)* | DIKGUN letter cards cut in on the accents |
| `-Mode edit` | identical cut and kill/music interplay, **no letter cards** |

Same pacing, same kills-on-beats, same distortion. Just no cards.

---

## The five commands you actually use

**1. Check a cut before committing to an encode**

```powershell
powershell -File make-promo.ps1 -Music black-sky.m4a -Game all -Duration 90 -Outro outro.mp4 -DryRun
```

**2. Build a promo**

```powershell
powershell -File make-promo.ps1 -Music black-sky.m4a -Game all -Duration 90 -Outro outro.mp4
```

**3. Build a regular music edit**

```powershell
powershell -File make-promo.ps1 -Music song.opus -Game all -Mode edit -Duration 180
```

**4. Just the gameplay — no outro, no cards** (the fastest thing to try on a new song)

```powershell
powershell -File make-promo.ps1 -Music song.opus -Game all -Mode edit -Duration 60 -Start 42
```

**5. Fix one clip via the EDL**

```powershell
# save the cut
powershell -File make-promo.ps1 -Music black-sky.m4a -Game all -Duration 90 -Outro outro.mp4 -SaveEdl cut.json -DryRun

# edit cut.json by hand, then render
powershell -File make-promo.ps1 -Music black-sky.m4a -Game all -Duration 90 -Outro outro.mp4 -FromEdl cut.json -Out promo.mp4
```

---

## Flags

### make-promo.ps1

| Flag | Default | Notes |
|---|---|---|
| `-Music` | **required** | `.opus`, `.mp3`, `.m4a`, `.wav` all work |
| `-Game` | `all` | `"Apex Legends"`, `"Rainbow Six Siege"`, … |
| `-Duration` | `60` | seconds of finished video |
| `-Start` | `0` | where in the **track** the window begins |
| `-Mode` | `promo` | `promo` or `edit` |
| `-Outro` | none | `outro.mp4` to append the end card |
| `-Out` | auto | `promo-<game>-<n>s.mp4` |
| `-Prefer` | none | games to favour on drop cuts, e.g. `"Battlefield 6"` |
| `-Pin` | none | force one clip into a window (below) |
| `-SaveEdl` / `-FromEdl` | none | save / render a hand-editable cut |
| `-NoCache` | off | re-encode every segment |
| `-DryRun` | off | print the cut list, encode nothing |

### promo_build.py

Same options in `--kebab-case` (`--music`, `--games`, `--duration`, `--start`,
`--mode`, `--outro`, `--out`, `--prefer`, `--pin`, `--save-edl`, `--from-edl`,
`--no-cache`, `--dry-run`).

---

## Picking `-Start` — this is the one that decides everything

`-Start` is where in the song the video begins. Read the plan output to see whether
you get a build-up before the drop.

For **Black Sky** (262s):

| `-Start` | What you get |
|---|---|
| `42` | build (42–78) then the drop — the promo setting |
| `78` | straight into the drop, all kills |
| `120` | late drop and breakdown |

On a new song, try `-Duration 20 -DryRun` at a few values and read the first ten lines.

---

## Pinning a specific clip

```powershell
-Pin "Q:\Game Clips\BF6\Cobra-M-J2J-1K.mp4|12|19|32.0|3.7"
#    path                            |src_start|src_end|at|len
```

- `src_start`/`src_end` — the moment in the source file
- `at` — **video** seconds where it lands
- `len` — how long it occupies (source is speed-fitted: 7s into 3.7s = 1.89×)
- Surrounding shots are trimmed to absorb it, so everything after stays on the beat

---

## Editing one clip without re-encoding everything

Every segment is cached in `segcache/`, keyed on source + start + length + speed.

| Change | Cost |
|---|---|
| Nothing | 59 reused, 0 encoded — **7 s** |
| One shot's start | 58 reused, 1 encoded — **18 s** |
| Full rebuild | 59 encoded — **~5 min** |

`cut.json` fields per shot: `at` (video seconds), `len`, `start`, `src`, `mode`, `speed`.
Change `start` to use a different moment from the same recording.

---

## Games with kill detection

| Game | windows |
|---|---|
| Apex Legends | 47 |
| Battlefield 6 | 33 |
| The Finals | 23 |
| Rainbow Six Siege | 21 |
| Deadlock | 10 |

Deadlock repeats footage at 60s — go longer or mix games. Tarkov has **no** kill
detection (laughter only).

---

## Re-rendering the outro

```powershell
python render_outro.py --out outro_frames --dur 8.0 --beats 0.30,2.00
ffmpeg -framerate 60 -i outro_frames/f_%04d.png `
  -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 `
  -vf "scale=1920:1080,setsar=1" -c:v libx264 -preset veryfast -crf 19 -pix_fmt yuv420p `
  -c:a aac -b:a 192k -ar 48000 -ac 2 -shortest -movflags +faststart outro.mp4
```

`--beats` is `name_time,mark_time` in seconds — the two moments that should hit kicks.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `overlay server not reachable` | start it (command at the top) |
| `music not found` | check the path; `.opus` is fine |
| Audio glitches **after upload** but fine locally | rebuild — fixed in v21+; old files have duplicate audio timestamps |
| A clip looks wrong | edit its `start` in the EDL, re-render (18s) |
| Suspect a stale render | add `-NoCache` |
| Output longer/shorter than wanted | the outro reserves its own time; `-Duration` is the **total** |

---

## The one syntax trap

`-Game` takes **ONE comma-separated string**, not several arguments.

```powershell
# WRONG - PowerShell binds each extra name to the NEXT parameter,
# so "Rainbow Six Siege" becomes -Out and ffmpeg fails on the filename
-Game "Apex Legends", "Rainbow Six Siege", "Battlefield 6"

# RIGHT
-Game "Apex Legends,Rainbow Six Siege,Battlefield 6"
```

The script now refuses positional binding, so this fails immediately with
"A positional parameter cannot be found" instead of doing something surprising.
