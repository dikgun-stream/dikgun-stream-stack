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
