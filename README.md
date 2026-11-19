# DikGun Stream Stack

Self-hosted streaming control stack for Twitch + YouTube (vertical), built to
replace StreamElements. Python/FastAPI overlay server, OBS scene generators, and
an AJAZZ AKP153E Stream Deck profile.

Checkpointed **November 2026**. This is a working snapshot, not a finished
product — see [Known limitations](#known-limitations) before assuming any
particular thing is automatic.

## What works

| Area | State |
|---|---|
| Overlay server (FastAPI + WebSocket hub) | Working |
| Twitch chat via anonymous IRC | Working — no auth, no bot account |
| Alerts: follow / sub / resub / gift / raid / bits | Working |
| Overlays: alerts, chat, goals, topic, input | Working |
| **Neosoviet scenes: Starting Soon / BRB / Ending** | **Working — horizontal + vertical** |
| **Clip player: plays local recordings, labels the game** | **Working — horizontal + vertical** |
| **Scenes show the live game/category from the dashboard** | **Working** |
| **Deadlock panel: K/D/A, W/L, item tower with real art** | **Working** |
| Chat commands: `!hint`, `!hints`, `!vote`, `!death`/`!kill`, `!hype` | Working |
| Hint Jar (viewer ideas, revealed on demand) | Working |
| Polls with live tally | Working |
| Dashboard (title/game, topic card, panic, audio, hints, polls) | Working |
| One-button go-live: Twitch + YouTube vertical | Working |
| Streamer mode / dev mode window tiling | Working |
| Stream Deck profile (2 pages, 15 keys) | Working |
| Deadlock launcher (mod manager + modded game) | Working |
| EFT launcher (BSG launcher + verified Play click) | Working |

## Known limitations

Read these before treating anything as done.

1. **YouTube horizontal is not automatable.** Aitum Multistream registers no
   start/stop hotkey (checked against all 354 OBS hotkeys), so that output needs
   a manual click in the Aitum dock. `yt-live.bat` only brings OBS forward to
   make that click obvious. Twitch and YouTube-vertical *are* fully automatic.
2. **The EFT Play click is image-verified, not API-driven.** The BSG launcher is
   CefSharp and exposes nothing to UI Automation. `Launch-EFT.ps1` matches a
   reference crop of the Play button before clicking. A launcher update, window
   resize, or DPI change may need `streamer-mode/assets/eft-play-ref.png`
   recaptured.
3. **Streamer mode closes programs.** Anything with a visible window that is not
   part of the layout gets closed (graceful, then forced). `Streamer.bot` is
   deliberately exempt — it sources the follow alerts.
4. **Deadlock launch needs mods already applied.** The preflight checks
   `gameinfo.gi` for the addons search path; if a Steam verify wiped it, the
   launch warns instead of silently running vanilla.
5. **OBS plugin deck buttons are dead.** The `com.gabilan.obs.studio.*` keys
   depend on a plugin that fails its websocket handshake. The page-2 buttons that
   call this repo's scripts work independently.
6. **Not built yet:** follower-gated TTS, automated clip pipeline to Shorts,
   per-game scene auto-switching, YouTube event alerts (token has wrong scopes).
7. **The Deadlock damage readout is removed on purpose.** The in-game panel
   changes layout between modes (Current Life / Last Death / per-source), so the
   OCR could not be made reliable. K/D/A, W/L and items work; damage does not.
8. **Deadlock K/D/A needs Tab to be held.** It is read from the scoreboard while
   the key is down. If the game does not have focus the scoreboard never opens
   and nothing is captured — that is expected, not a fault.
9. **Item art covers 181 of 250 purchasable items.** Items without published
   card art fall back to a placeholder. The known-miss offender (Golden Goose
   Egg) is covered by a learned template in `streamer-mode/assets/items_hud/`.

## Components

```
overlay-server/     FastAPI app: REST + WebSocket hub + OBS proxy + Twitch IRC
  server.py         routes, hub, OBS/IRC wiring
  chat_engine.py    chat commands, hint jar, counters, polls
  obs_client.py     async obs-websocket v5 client (auth + reconnect)
  twitch_irc.py     anonymous IRC reader -> events
  deadlock_hud.py   screen readers: scoreboard, kill/assist badges, item grid
  input_capture.py  polling key watcher (no low-level hook: anti-cheat safe)
  clips.py          indexes local recordings into a per-game clip library
  public/           dashboard, chatroom, and the overlays
    assets/         shared scene design system (scene.css / scene.js)
    overlays/       alerts, chat, goals, topic, input, deadlock,
                    starting-soon, brb, ending, clips
    items/          Deadlock item card art served to the overlay
streamer-mode/      desktop automation
  Streamer-Mode.ps1 window tiling + taskbar/icons + app lifecycle
  Test-Tiling.ps1   measures windows before/after and reports overlaps
  deck/             one script per Stream Deck button (+ manifest template)
  assets/           templates + item libraries (items, items_shop, items_hud)
obs-build/          OBS scene generators and design docs
  build_scenes.py   horizontal scene collection
  build_vertical.py Aitum vertical scenes
  BUILD-SPEC.md     the full setup spec
```

## Scene design system

The Starting Soon / BRB / Ending scenes share one stylesheet and one runtime:

- `public/assets/scene.css` — palette, geometry, type, grain, motion
- `public/assets/scene.js`  — orientation, live game label, countdown, marquee

Design language is **neosoviet / constructivist** — aesthetic only, no political
symbols or slogans. Palette is the convergent warm-paper / near-black / signal-red
triad used by three independent constructivist design systems (`#ECE4D2` paper,
`#161412` ink, `#C8161D` red). Geometry uses one axis plus its perpendicular:
−30°/+60° on the horizontal canvas, 60° on the vertical. Corners are chamfered,
never rounded; there are no gradients, shadows or glass.

**The game label is live.** All three scenes read `stream.game` from the config and
update over WebSocket when the dashboard changes the category — see
`_fanout_config()` in `server.py`.

Vertical versions use the same pages with `?v=1`. They are not scaled copies:
the composition inverts, the timer moves to the upper third, and the diagonal
steepens.

### Adding gameplay-driven content

`scene.js` listens for `{kind:"event", type:"..."}` on the `scenes` channel and
re-dispatches it as a `scene:event` DOM event. That is the hook for things like a
kill/assist circle — currently a placeholder that records the event type and
renders nothing, so the scene stays uncluttered during play.

```js
document.addEventListener('scene:event', (e) => {
  if (e.detail.type === 'kill') { /* animate something */ }
});
```

Trigger one with:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:7475/api/event -Method Post `
  -ContentType 'application/json' -Body '{"type":"kill","channel":"scenes"}'
```

## Clip library

`overlay-server/clips.py` indexes the OBS DVR recordings kept under
`C:\Users\JR\Videos` (override with the `DIKGUN_CLIPS` environment variable).
The folder tree is already organised per game, so the folder name becomes the
game label. Several folders are alternate spellings of the same game
("R6siege" / "Tom Clancy's Rainbow Six  Siege"), so `clips.py` normalises them
through an explicit alias table rather than guessing.

- `GET /api/clips` — index + per-game counts
- `GET /api/clips?game=The%20Finals` — filtered
- `GET /videos/<relative path>` — the media itself, with HTTP range support
- `/overlay/clips` — the player (`?v=1` for vertical)

The player is a `<video>` element in a browser source, not an OBS media source,
so the game label and the footage stay in sync in one source.

### Player options

| Param | Default | Meaning |
|---|---|---|
| `game=NAME` | all | only play that game's folder |
| `len=45` | 45 | seconds per clip before advancing (`0` = whole file) |
| `start=0.12` | 0.12 | seek this fraction in, plus a random 0–30 s jitter |
| `mute=0` | muted | unmute |
| `shuffle=0` | shuffle | play in order instead of random |
| `v=1` | horizontal | vertical composition |

The random seek exists because these are full session recordings, not trimmed
clips — starting every file at 0 usually lands on a menu or the tail of the
previous round.

Scenes: `SHOW_Clips` and `V_SHOW_Clips`. To use it as a background instead,
drop `OVL_Clips` into `SHOW_BRB` and delete its opaque scene layer.

Durations are probed with `ffprobe` once in the background and cached in
`overlay-server/clips_cache.json` (gitignored). Without `ffprobe` the player
still works — it just cannot size the random seek window.

## Prerequisites

- **Windows 11** (developed on build 26200 / 25H2)
- **PowerShell 7** (`pwsh`) — the wrappers call `pwsh`, not `powershell`
- **Python 3.12** with `fastapi`, `uvicorn`, `websockets`
- **OBS Studio 33+** with [obs-websocket](https://github.com/obsproject/obs-websocket)
  and the Aitum Vertical + Multistream plugins
- Optional: Streamer.bot (alerts), AJAZZ Stream Dock (hardware deck)

## Quick start

```powershell
# 1. configure this machine
pwsh -File install.ps1

# 2. run the overlay server
cd overlay-server
python -m uvicorn server:app --host 127.0.0.1 --port 7475

# 3. open the dashboard
start http://127.0.0.1:7475/
```

Full per-machine configuration is in [SETUP.md](SETUP.md).

## Updating an existing install

The overlay pages are cached aggressively by OBS's embedded browser. After any
change to `overlay-server/public/`, **a plain restart is not enough** — do this:

```powershell
# 1. pull
cd dikgun-stream
git pull

# 2. restart the server (config.json is not in git -- it survives the pull)
cd overlay-server
python -m uvicorn server:app --host 127.0.0.1 --port 7475

# 3. force every scene browser source to reload
pwsh -File ../obs-build/refresh-scene-sources.ps1
```

`refresh-scene-sources.ps1` appends a cache-busting query string to each scene
browser source's URL, which is the only reliable way to drop OBS's cached copy.

If you changed `server.py` and something 404s afterwards, check that the file is
one the app actually mounts: `/overlay/{name}` appends `.html`, so plain `.css`
and `.js` files must live under `public/assets/` (mounted at `/assets/`), not
under `public/overlays/`.

### What needs re-adding after a scene change

Scene *content* (the HTML/CSS) needs only the refresh above. Scene *structure*
(which sources exist in which scene) lives in OBS, not in this repo — if a scene
gains a new source, wire it once per machine as described in
[SETUP.md](SETUP.md#scenes).

## Secrets

This repo deliberately contains **no stream keys, OAuth tokens, or passwords.**

`overlay-server/config.json` is gitignored; the committed file is
`config.example.json`. OBS and Aitum keep stream keys under
`%APPDATA%\obs-studio`, which is outside this repo — do not copy those
directories in. `obs-build/obs-ws.ps1` reads the obs-websocket password from
OBS's own plugin config at runtime rather than storing it.

## Licensing note

`streamer-mode/assets/eft_a.png` is a fan-hosted Escape from Tarkov wordmark
(CC BY-NC). It is fine for personal key art but should not be redistributed.
`dl_logo.png` is the official Deadlock logo from Steam's CDN.

## Laughter highlights

`overlay-server/laughter_scan.py` finds the funny bits without hand-picking.
Detection is a real audio-tagging model -- YAMNet, trained on Google's AudioSet
ontology -- not a hand-rolled heuristic. It scores 521 sound classes per 0.48s
window and reads the laughter family (Laughter / Giggle / Snicker / Chuckle).

When laughter is detected the tool emits a **three-minute window** around it
(default +/- 90s). Overlapping windows are merged, so three laughs 30s apart
produce one window rather than three identical ones all starting at zero.

### Setup (one time)

```powershell
pip install tensorflow

# YAMNet model, ~14 MB
mkdir obs-build\yamnet
curl -L "https://tfhub.dev/google/yamnet/1?tf-hub-format=compressed" -o obs-build\yamnet\1.tar.gz
tar -xzf obs-build\yamnet\1.tar.gz -C obs-build\yamnet
```

### Running it

```powershell
cd overlay-server
python laughter_scan.py --list                        # see files and durations
python laughter_scan.py --one <file>                  # single file, verbose scores
python laughter_scan.py --all --threshold 0.05        # scan everything
```

The scan is incremental: `laughter_index.json` (gitignored) records what has
been done, and already-scanned files are skipped, so it is safe to stop and
resume. Plan for an overnight run on 100 GB of source.

### Measured behaviour

On this library the scores separate cleanly. A solo Rainbow Six recording peaked
at **0.004** laughter probability -- no laughter, correctly reported as none.
A co-op R.E.P.O. recording peaked at **0.58** and produced 7 usable windows
across the library so far.



### Markers that are text or pale icons

`marker_scan.py` handles three marker styles, chosen by the template folder:

| Mode | Template files | Used for |
|---|---|---|
| colour mask | `mask.png` (+ optional `ref.png`) | Deadlock's red ring |
| grayscale | `mask.png` + `region.json` with `"mode": "gray"` | Battlefield 6's skull |
| OCR | `text.json` naming a word | attempted for BF6's "KILL" label, abandoned |

`region.json` also sets the search box: **not every marker is centred**.
Battlefield's banner sits around x=530-830, which a centre crop never contains.

### Battlefield 6

The kill tell is a small **white skull** icon, and it moves around far more than
expected. Measured across three confirmed kills in two clips:

```
skull at (689,633) 38x40
skull at (824,704) 45x26
skull at (694,634) 39x37
```

Two lessons that cost real time:

- **Threshold matters enormously.** At 0.45 the detector fires ~116 times per
  clip. Confirmed kills score 0.78-0.81, so 0.75 is the working floor.
- **Battle Royale uses a different icon** -- a gold skull with a down arrow on
  its left, not the white one. The white template cannot match it by design, so
  BR recordings are missed rather than falsely matched.

Scan of 44 BF6 clips at 0.75: 353 hits -> 60 windows. Only **two** of those are
confirmed by a human, so treat the rest as candidates.

Text-based detection was tried and abandoned: the "KILL" label is ~14px tall,
light grey on a bright snowy map. OCR reads "TANK BUSTER" and "VEHICLE
DESTROYED" from the same banner but never "KILL", and a pixel template scores
only 0.58 on its own source frame.

### Tuning how many clips you get

Two knobs, and they pull in different directions.

**`--threshold`** sets how confident the laughter has to be. On this library:

| threshold | files qualifying |
|---|---|
| 0.05 | 25 |
| 0.03 | 34 |
| 0.02 | 48 |
| 0.015 | 67 |
| 0.01 | 92 |

Worth knowing the shape of the data: 79 of 129 recordings peak below 0.02. A lot
of them are solo ranked games with nobody to laugh at. Only four files peak
above 0.5, and those are unmistakable.

**`--pad`** sets the window either side of a detection. This matters more than it
looks, because most recordings here are only 2-5 minutes long -- so the default
90s padding immediately saturates the file. Measured on the first pass, 11 of 26
windows covered 90% or more of their source, which makes the "library" feel like
the same handful of clips repeating.

```powershell
# 3 minute windows (default) -- good for long recordings
python laughter_scan.py --all --threshold 0.05

# tighter windows -- more distinct clips out of short recordings
python laughter_scan.py --all --threshold 0.02 --pad 35 --min-gap 6 --rescan
```

`--rescan` is required when changing either value; without it, already-scanned
files are skipped and the old windows remain.


## Kill highlights

Laughter is not the only kind of moment worth playing. `overlay-server/kill_scan.py`
finds kills so the rotation is not all laughs, and both pools are merged by
`/api/clips/highlights?source=all`.

Currently implemented for **Rainbow Six Siege** only. The tell is a small red X
at the crosshair with a white centre dot, arriving together with a white
`+110`-style score popup just above it.

Detection requires all three of:

1. score popup matches (white-text mask) >= 0.50
2. kill X matches (red-mask) >= 0.40
3. the two matches are within 110px of each other

The third condition is what made it usable. Without it an orange cargo crate
scored 0.74 against the red X template and produced a false kill.

Matching is mask-to-mask rather than greyscale: a binary "is this pixel red /
white" mask survives changing backdrops that a brightness correlation does not.

Kill windows are +/-20s, deliberately tighter than laughter's +/-35s -- a kill
wants the second before and a beat after, not a minute of build-up.

Templates live in `streamer-mode/assets/kill/`. Verified 4/4 true positives on
independent recordings (red X, hit sparks and score popup all present).

```powershell
python kill_scan.py --list --game "rainbow six"
python kill_scan.py --one <file>
python kill_scan.py --all --game "rainbow six"
```

### Other games (not implemented)

Battlefield 1, Battlefield 6 and Battlefield 2042 were requested. There is no
usable footage to calibrate against:

- `Battlefield 2042/` -- one 105s recording, entirely menus (loading, unlock
  notices, specialist and vehicle select). No gameplay.
- `B1/` -- one 120s recording, which is not the game at all: it is a browser
  window showing a Twitch stream of a Steam library.
- `Battlefield 1/` -- directory exists but is empty.
- Battlefield 6 -- no directory, no footage.

Each game needs one confirmed kill frame to build a template from, then the
scanner works the same way. Tarkov is deliberately out of scope.


### Index safety

Both scanners take a lock file and write their index atomically. Two scans
running at once previously did load-modify-write on the same JSON, so whichever
finished last silently dropped the other's entries -- that is how 4 R6 files and
5 kill windows vanished once. A second scan now refuses to start while one holds
the lock, and `--root` may point at a different folder without conflict.

### Clip folders

`C:\Users\JR\Videos` and `Q:\Game Clips` are both indexed, each served from its
own mount. `Q:\Game Clips` is organised into per-game subfolders (BF6, R6,
Deadlock, EFT) which map to display names via an alias table in `clips.py` --
without it every clip in that folder is labelled "Game Clips" on screen.

### Playing them

`/api/clips/highlights?source=laughter` returns playable windows
(`rel`, `game`, `start`, `len`). The clip scenes point at
`/overlay/clips?source=laughter`, and the player falls back to the full clip
list automatically when no highlights exist yet -- the scan runs for hours, so an
empty index should not mean an empty scene.

Nothing is cut on disk. These are windows into the source recordings, because
copying hundreds of GB of clips is not worth it -- `<video>` seeks instead.
