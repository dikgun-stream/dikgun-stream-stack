# Rebuilding this stack on a new machine

What a `git clone` gets you, what it does not, and the order to do things in.

**Short version:** the clone gives you all the *code* — server, overlays, bot,
deck profile, brand assets, scene builders. It does **not** give you the OBS
scene collection, the channel config, the detection indexes, or the Discord
token. Those are machine-local by design and have to be recreated or restored
from a backup.

---

## 1. What the clone contains

| Area | Files | What it is |
|---|---|---|
| `overlay-server/` | 210+ | FastAPI server, overlay pages, dashboard, scanners, DDC/OC helpers |
| `streamer-mode/` | 480+ | Streamer.bot config, Stream Deck scripts, detection templates |
| `streamdeck/` | 32 | Stream Dock profile + installer |
| `discord-bot/` | 19 | Bot, setup script, walkthrough |
| `brand/` | 45 | Avatars, banners, thumbnails, generators |
| `obs-build/` | 10 | `build_scenes.py`, `build_vertical.py`, OBS websocket helpers |

## 2. What the clone does NOT contain

| Missing | Where it lives | How to recover |
|---|---|---|
| **OBS scene collection** | `obs/scenes/DikGun.json` | **In git.** Restore with `install.ps1 -Scenes` (copies it into `%APPDATA%\obs-studio\basic\scenes\`), or rebuild with `obs-build/build_scenes.py` (scaffold, not identical) |
| **Channel config** | `overlay-server/config.json` | Regenerated from defaults on first start — channel name, titles and checklists need re-entering |
| **Detection indexes** | `laughter_index.json`, `kill_index.json`, `marker_index.json` | Re-run the scanners (hours of CPU) |
| **Discord token** | `discord-bot/.env` | New token from the Developer Portal |
| **YAMNet model** | `obs-build/yamnet/` (~35 MB) | Re-download (see §5) — only needed for laughter rescans |
| **Media** | `C:\Users\JR\Videos`, `Q:\Game Clips` | Not in git; hundreds of GB |

**The scene collection is the big one.** Everything else has a script or a
scanner behind it. Scenes do not.

## 3. Software to install first

| Thing | Notes |
|---|---|
| **OBS Studio 33.x** | 32-bit-capable install; the version matters for scene JSON format |
| **vertical-canvas plugin 1.6.8** | Install to ONE location only — duplicate copies are skipped but cause confusion |
| **aitum-multistream plugin 1.0.9** | |
| **Python 3.12** | `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` is the path baked into several scripts |
| **ffmpeg** | Must be on PATH. Verified at `C:\ffmpeg\ffmpeg.exe` |
| **Tesseract OCR** | `C:\Program Files\Tesseract-OCR\` — needed for OCR kill markers |
| **Streamer.bot 1.0.7** | `%USERPROFILE%\Apps\Streamer.bot\` |
| **Stream Dock AJAZZ** | For the deck profile |
| **RivaTuner 2.24** (optional) | Display profiles only |

## 4. One command, or step by step

```powershell
powershell -File install.ps1 -Check     # report what is present / missing
powershell -File install.ps1 -All       # install everything and wire it up
```

`-All` runs every stage: winget software, OBS plugins, python deps, config,
scene collection, YAMNet, deck profile. Each stage skips what already exists and
backs up before overwriting, so it is safe to re-run.

Stages can be run alone: `-Software`, `-Plugins`, `-Deps`, `-Scenes`, `-Model`,
`-Deck`, `-Config`.

What it cannot do, and will list at the end as manual steps:

- install **Stream Dock AJAZZ** or **RivaTuner** - neither is on winget
- sign in to Twitch/YouTube - stream keys are per account
- enable obs-websocket inside OBS
- import the Streamer.bot actions and connect accounts
- the Discord bot token (`discord-bot/setup.ps1` does that interactively)

### Doing it by hand

## 4b. Order of operations

```powershell
# 0. clone
git clone git@github-dikgun:dikgun-stream/dikgun-stream-stack.git dikgun-stream
cd dikgun-stream

# 1. python deps for the server
cd overlay-server
pip install -r requirements.txt

# 2. start it -- it writes a default config.json on first run
python -m uvicorn server:app --host 127.0.0.1 --port 7475

# 3. open the dashboard and set the channel + categories
#    http://127.0.0.1:7475/

# 4. OBS: restore the scene collection, OR rebuild it
#    restore: copy DikGun.json into %APPDATA%\obs-studio\basic\scenes\
#    rebuild: python obs-build/build_scenes.py --dry-run   (then without)

# 5. point OBS browser sources at the server
powershell -File obs-build/refresh-scene-sources.ps1

# 6. Stream Dock profile
cd streamdeck
powershell -ExecutionPolicy Bypass -File install.ps1

# 7. Discord bot
cd ..\discord-bot
powershell -ExecutionPolicy Bypass -File setup.ps1
python bot.py --provision
python bot.py
```

## 5. Rebuilding the scanner indexes

Without these the clip player falls back to whole files instead of highlights.

```powershell
cd overlay-server
# takes minutes; walks the clip roots
python kill_scan.py    --root "C:\Users\JR\Videos" --all --game R6
python marker_scan.py  --root "Q:\Game Clips" --all --game deadlock
python marker_scan.py  --root "Q:\Game Clips" --all --game bf6 --stage fast
python marker_scan.py  --root "C:\Users\JR\Videos" --all --game apex --stage fast
python marker_scan.py  --root "C:\Users\JR\Videos" --all --game finals --stage fast
```

Laughter additionally needs YAMNet:

```
obs-build/yamnet/   <- TensorFlow SavedModel, ~35 MB, not in git
```

Fetch it from TensorFlow Hub (`https://tfhub.dev/google/yamnet/1`), unpack the
SavedModel into that folder, then:

```powershell
python laughter_scan.py --root "C:\Users\JR\Videos" --all --threshold 0.02 --pad 35 --min-gap 6
```

That one is the expensive scan — roughly an hour per hundred recordings.

## 6. Verification

Work down this list; each step fails visibly if the one before it is wrong.

| Check | Expected |
|---|---|
| `http://127.0.0.1:7475/` | dashboard loads, obs + twitch dots green |
| `/api/status` | `obs.connected: true` |
| `/api/clips` | clip count > 0 |
| `/api/clips/highlights?source=all` | `count` > 0, entries have `windows` |
| `/api/viewers` | twitch `state: ok` |
| `/api/live/status` | returns the current scene |
| OBS: a clip scene | footage plays, banner sweeps with game name |
| Stream Dock: BOT button | starts the bot |
| `python bot.py --check` | token present, overlay reachable |

## 7. What cannot be recovered at all

- **The OBS scene collection, if you did not back it up.** `build_scenes.py`
  produces a working scaffold, not the same file — every manual crop, position
  and per-game tweak has to be redone.
- **Recordings.** Hundreds of GB, never in git.
- **RivaTuner launcher profiles** — stored in `HKLM\...\Unwinder\RivaTuner`,
  not exported anywhere by the project.

## 8. Back up before you need this

`backup.ps1` in the repo root copies the machine-local state into a timestamped
folder:

```powershell
powershell -File backup.ps1
```

It collects `config.json`, the three indexes, the OBS scene collection, the
Stream Dock profile and a token-free `.env` template. Keep that folder somewhere
that is not this machine — a private repo, a USB stick, cloud storage.

Without that, a dead boot drive costs you the scenes and hours of rescanning.
