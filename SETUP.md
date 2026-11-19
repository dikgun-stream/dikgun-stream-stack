# Setting up on a new machine

Everything machine-specific is either in `overlay-server/config.json` (not
committed) or at the top of `streamer-mode/Streamer-Mode.ps1`. Nothing is hidden
in the registry or in `%APPDATA%` except the deck profile, which `install.ps1`
regenerates.

## 1. Prerequisites

```powershell
winget install --id Microsoft.PowerShell            # pwsh 7
winget install --id Python.Python.3.12
pip install fastapi uvicorn websockets pillow
```

OBS Studio 33+ with:
- [obs-websocket](https://github.com/obsproject/obs-websocket) — **Tools → WebSocket Server Settings → Enable**
- [Aitum Vertical](https://github.com/Aitum/obs-vertical-canvas) + [Aitum Multistream](https://github.com/Aitum/obs-aitum-multistream)

## 2. Run the installer

```powershell
pwsh -File install.ps1
```

It will:
1. copy `config.example.json` → `config.json` if you have no config yet,
2. render `streamer-mode/deck/manifest.template.json` into your Stream Dock
   profile with this checkout's real paths,
3. check Python, the Python modules, and whether OBS's websocket is listening.

## 3. Fill in the live config

Edit `overlay-server/config.json`:

| Key | What to set |
|---|---|
| `twitch.channel` | your channel name (lowercase) — used for anonymous IRC |
| `obs.host` / `obs.port` | default `127.0.0.1:4455` |
| `obs.password` | from OBS → Tools → WebSocket Server Settings |
| `panic.scene` | the scene to cut to on panic (e.g. `SHOW_BRB`) |
| `panic.muteSources` | audio source names to mute (e.g. `["Mic/Aux"]`) |

Overlay appearance (colours, fonts, durations, max messages) lives in the
`alerts`, `chat`, `goals`, `labels`, `starting-soon` blocks — all hot-reloadable
through the dashboard.

## 4. Fix the machine paths in `Streamer-Mode.ps1`

Near the top of `streamer-mode/Streamer-Mode.ps1`:

```powershell
$MON_X = 1920; $MON_Y = -17      # stream monitor origin (can be negative!)
$MON_W = 1920; $MON_H = 1080

$OBS_EXE  = 'C:\Program Files\obs-studio\bin\64bit\obs64.exe'
$DOCK_EXE = 'C:\Program Files (x86)\Stream Dock AJAZZ Global\Stream Dock AJAZZ.exe'
$SB_EXE   = "$env:USERPROFILE\Apps\Streamer.bot\Streamer.bot.exe"
$DSH_EXE  = "$env:LOCALAPPDATA\Programs\DeepSeek Harness\DeepSeek Harness.exe"
```

Then the measured minimum window sizes in the tiling section:

```
OBS 1241x496 | Stream Dock 1202x832 | Streamer.bot 720x640
DeepSeek Harness 522x601 | Chromium app window 286x193
```

**These are measured, not guessed.** Apps silently clamp to their minimum and
then overlap, which is exactly the bug to avoid. Re-measure with:

```powershell
# request a tiny size, read back what the app allowed
pwsh -File streamer-mode/Test-Tiling.ps1 -Mode stream -SkipApply
```

Verify a layout change with the full harness, which reports windows before and
after plus any pairwise overlap:

```powershell
pwsh -File streamer-mode/Test-Tiling.ps1 -Mode stream
```

## 5. Game launchers

`streamer-mode/deck/Launch-Deadlock.ps1`:
- `$DMM` — Deadlock Mod Manager exe
- `$GAMEINFO` — `...\Deadlock\game\citadel\gameinfo.gi`
- `$URI` — `steam://run/1422450//-condebug` (app id 1422450 is Deadlock's)

`streamer-mode/deck/Launch-EFT.ps1`:
- `$LAUNCHER` — `BsgLauncher.exe`
- `streamer-mode/assets/eft-play-ref.png` is a reference crop of the Play button.
  **If the launcher UI changes, recapture it**: open the launcher while logged
  in, screenshot the window, and crop the bottom-right button area to the same
  aspect as the existing file.

## 6. OBS scenes

`obs-build/build_scenes.py` and `build_vertical.py` regenerate the scene
collections. They write into `%APPDATA%\obs-studio\basic\scenes\`. Close OBS
first — it overwrites the collection on exit.

**OBS reads the active collection from `user.ini`, not `global.ini`.** If you
patch the wrong file the new collection appears to do nothing.

## 7. Stream Deck (optional)

`install.ps1` writes the profile to:

```
%APPDATA%\HotSpot\StreamDock\profiles\<PROFILE_ID>.sdProfile\manifest.json
```

`PROFILE_ID` defaults to the id this repo was built against. On a different
machine the deck app will have its own id — register the profile in
`DataCache.db` (table `AKP153E`) or simply let the app create a profile and copy
the `Actions` block across. The key art lives in `...\Images\`.

Every button script is invoked through `streamer-mode/deck/*.bat`, and those use
`%~dp0`, so they work from any checkout path. Only the paths *inside the deck
manifest* are absolute — that is what `install.ps1` rewrites.

## 8. Verify

```powershell
# server up?
curl http://127.0.0.1:7475/api/status

# does OBS talk to us?
curl http://127.0.0.1:7475/api/obs/scenes

# does the go-live path resolve? (dry run, does NOT start the stream)
curl -X POST http://127.0.0.1:7475/api/live -H "Content-Type: application/json" -d '{\"action\":\"start\",\"dryRun\":true}'
```

The dry run should report `verticalHotkeyAvailable: true` and a two-line plan:
OBS native stream (Twitch) and the vertical hotkey (YouTube).

## Scenes

Six scenes carry the waiting / away / outro content, one of each per canvas:

| Horizontal | Vertical | Page |
|---|---|---|
| `SHOW_StartingSoon` | `V_SHOW_StartingSoon` | `/overlay/starting-soon` |
| `SHOW_BRB` | `V_SHOW_BRB` | `/overlay/brb` |
| `SHOW_Ending` | `V_SHOW_Ending` | `/overlay/ending` |

Vertical sources append `?v=1` to the same URL. That is not a scaled copy: the
composition inverts, the timer moves into the upper third for thumb-safety, and
the diagonal steepens from 30 degrees to 60.

### Wiring on a new machine

For each row above:

1. Add a **Browser** source to the scene.
2. Set width/height to the canvas size (1920x1080 horizontal, 1080x1920 vertical).
3. Paste the URL. For the vertical copies add `&cb=1` after `?v=1`.
4. Enable **Shutdown source when not visible** — a looping scene costs a Chromium
   instance while hidden otherwise.
5. Leave **Control audio via OBS** off; there is no audio in these pages.

The scenes pick up the current category automatically. Set it on the dashboard
and every open scene updates over WebSocket within a second.

### Refresh after an edit

OBS caches browser-source pages. After changing anything under
`overlay-server/public/`:

```powershell
pwsh -File obs-build\refresh-scene-sources.ps1
```

That appends a cache-busting timestamp to every scene source URL. Restarting OBS
alone will NOT show your change.

### Design notes

Palette, geometry and type live in `overlay-server/public/assets/scene.css` and
are shared by all three pages. The aesthetic is constructivist, deliberately
without any political symbols or slogans.

Rules worth keeping if you edit:

- one diagonal axis plus its perpendicular, never two competing diagonals
- red is reserved for the single most important element on the frame
- chamfered corners only, zero border radius
- no gradients, no drop shadows, no glass/blur fills
- animate `transform` and `opacity` only, with loop periods of 18 seconds or more
  so a ten-minute wait does not read as a gif

### Gameplay-driven content (placeholder)

`public/assets/scene.js` re-dispatches server events as a DOM event, so a kill or
assist can drive an animation later without touching the scene markup:

```js
document.addEventListener('scene:event', (e) => {
  if (e.detail.type === 'kill') { /* animate */ }
});
```

Fire one from anywhere:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:7475/api/event -Method Post `
  -ContentType 'application/json' -Body '{"type":"kill","channel":"scenes"}'
```

This is intentionally inert for now: the in-game scenes stay uncluttered, and
only the waiting/BRB/ending scenes listen.

## Clips

`SHOW_Clips` and `V_SHOW_Clips` play the local recordings indexed from
`C:\Users\JR\Videos`. The index is built at request time from the folder tree,
so dropping a new recording into a game folder is enough — no rebuild step.

### Wiring

1. Add a **Browser** source.
2. Size it to the canvas (1920x1080 or 1080x1920).
3. URL `http://127.0.0.1:7475/overlay/clips` (vertical adds `?v=1`).
4. Tick **Shutdown source when not visible** — a video source left running costs
   real CPU and will not be shown anyway.

### Pointing it somewhere else

```powershell
$env:DIKGUN_CLIPS = 'D:\Recordings'
```

Then restart the server. The mount is created at startup, so a change needs a
restart rather than a refresh.

### Choosing what plays

Append to the URL:

- `?game=The%20Finals` — one game only
- `?len=90` — longer window per clip
- `?shuffle=0` — play oldest-to-newest instead of random
- `?mute=0` — unmute (muted by default, since this runs under a live stream)

### Why it seeks instead of starting at zero

The recordings are full OBS DVR sessions, not trimmed clips — most are 300 MB to
3 GB. Playing from 0 lands on a menu or the previous round, so the player jumps
to `start` (default 12%) plus a random jitter, plays `len` seconds, then
advances. Set `start=0&len=0` to play whole files end to end.

## Streamer.bot in stream mode

Stream mode now launches Streamer.bot as well. It sources the follow and sub
alerts, so leaving it closed silently breaks them -- earlier versions only
started it in `-Mode dev`, which meant the alerts died the moment you went live.

It is still only *tiled* in dev mode. In stream mode the window is minimised,
because it is a background service and not something you watch.

If you genuinely want it closed during a stream, comment out the
`Start-IfNeeded -Label 'Streamer.bot'` line in `streamer-mode/Streamer-Mode.ps1`.
