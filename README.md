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
| Overlays: alerts, chat, goals, starting-soon, topic | Working |
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

## Components

```
overlay-server/     FastAPI app: REST + WebSocket hub + OBS proxy + Twitch IRC
  server.py         routes, hub, OBS/IRC wiring
  chat_engine.py    chat commands, hint jar, counters, polls
  obs_client.py     async obs-websocket v5 client (auth + reconnect)
  twitch_irc.py     anonymous IRC reader -> events
  public/           dashboard, chatroom, and the five overlays
streamer-mode/      desktop automation
  Streamer-Mode.ps1 window tiling + taskbar/icons + app lifecycle
  Test-Tiling.ps1   measures windows before/after and reports overlaps
  deck/             one script per Stream Deck button (+ manifest template)
obs-build/          OBS scene generators and design docs
  build_scenes.py   horizontal scene collection
  build_vertical.py Aitum vertical scenes
  BUILD-SPEC.md     the full setup spec
```

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
