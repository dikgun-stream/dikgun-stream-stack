# Stream Dock profile

The Stream Deck / Stream Dock layout for the channel, with an installer.

Your device is an **AJAZZ AKP153E** running **HotSpot Stream Dock**, which uses
Elgato-compatible `.sdPlugin` files. Profiles are folders, not single files, and
they live at:

```
%APPDATA%\HotSpot\StreamDock\profiles\<PROFILE-ID>.sdProfile\
```

## Install

```powershell
cd streamdeck
powershell -ExecutionPolicy Bypass -File install.ps1
```

What it does:

1. Backs up the profile currently installed, timestamped, next to it
2. Copies `profile\DikGun Stream.sdProfile` into place
3. **Rewrites every button path** to point at this clone

Step 3 matters. The manifests store absolute paths such as
`C:\Users\JR\...\streamer-mode\deck\panic.bat`. Copying the folder to another
machine would install 18 buttons that all fail silently. The installer rewrites
them from wherever the repo actually sits.

Flags:

| Flag | Effect |
|---|---|
| `-WhatIf` | report what would change, write nothing |
| `-Target <path>` | install somewhere else, for testing |

**The installed folder name must stay the same** (`MRX4M90L-...`). Both manifests
reference that GUID by name in `Pages.Current` / `Pages.Pages`, so renaming the
folder breaks the profile.

After installing, restart Stream Dock or switch away from the profile and back.

## Button map

Paths are relative to `streamer-mode/deck/` unless noted.

### Page 1 — main (18 buttons, 6 rows × 3 cols)

| Row | Left | Middle | Right |
|---|---|---|---|
| 0 | DMG+500 `damage.bat` | STREAM MODE `stream-mode.bat` | DASH `dashboard.bat` |
| 1 | launch EFT `launch-eft.bat` | STOP `stop-live.bat` | DEV MODE `dev-mode.bat` |
| 2 | launch Deadlock `launch-deadlock.bat` | MIC `mic-toggle.bat` | LIVE *(OBS plugin)* |
| 3 | PANIC `panic.bat` | RESTORE `restore.bat` | CHAT `chat.bat` |
| 4 | YT LIVE `yt-live.bat` | TWITCH+VERT `go-live.bat` | KILL+1 `kill.bat` |
| 5 | STATS *(OBS plugin)* | **ADMIN** *(page change)* | ASSIST+1 `assist.bat` |

### Page 2 — admin (13 buttons)

| Row | Left | Middle | Right |
|---|---|---|---|
| 0 | — | — | TALKING *(hotkey)* |
| 1 | — | TARKOV *(hotkey)* | BRB *(hotkey)* |
| 2 | — | HINT `hint.bat` | APEX *(hotkey)* |
| 3 | DEV MODE `dev-mode.bat` | **BOT** `discord-bot/Toggle Bot.bat` | ENDING *(hotkey)* |
| 4 | CLIP *(OBS)* | DEADLOCK *(hotkey)* | CODE *(hotkey)* |
| 5 | 2/2 *(indicator)* | **BACK** *(page change)* | — |

## The BOT button

Added at page 2, slot `3,1` — empty before, and next to DEV MODE where the other
tooling lives.

It runs `discord-bot/Toggle Bot.bat`, which:

- starts the bot if it is stopped, or stops it if it is running
- launches **hidden**, so no console window flashes on the deck
- refuses to start a second copy
- writes to `discord-bot/logs/`

To check state without the deck:

```powershell
cd ..\discord-bot
powershell -File bot-control.ps1 status
```

There are also `Start Bot.bat` and `Stop Bot.bat` if you would rather have
separate buttons than a toggle.

## Editing the layout

Two ways:

1. **In Stream Dock** — drag buttons around, then re-export and copy the profile
   folder back over `profile/DikGun Stream.sdProfile/` and commit.
2. **Edit the JSON** — `profile/DikGun Stream.sdProfile/manifest.json` is page 1,
   and `profile/DikGun Stream.sdProfile/profiles/8H3YBY19-....sdProfile/manifest.json`
   is page 2. Slots are keyed `"row,col"` starting at `"0,0"` top-left.

A `system.open` action needs only:

```json
{
  "Name": "BOT",
  "UUID": "com.hotspot.streamdock.system.open",
  "Settings": { "path": "__REPO__\\discord-bot\\Toggle Bot.bat" },
  "States": [{ "Title": "BOT", "TitleAlignment": "middle", "FontSize": 10 }]
}
```

`__REPO__` is replaced with the repo root at install time.

## Two things that broke during setup

**PowerShell writes a BOM.** `Set-Content -Encoding UTF8` in Windows PowerShell
5.1 emits a UTF-8 BOM, and the JSON parser rejects a BOM-prefixed manifest —
every button in the profile would have stopped loading. The installer writes with
`[System.IO.File]::WriteAllText` and an explicit no-BOM encoder instead.

**Page 2 is nested.** It is not a sibling of page 1; it lives inside
`Images/profiles/`. The installer walks for `manifest.json` rather than assuming
a fixed depth.

## Verifying an install

```powershell
python ..\..\obs-build\verify_profile.py "$env:APPDATA\HotSpot\StreamDock\profiles\MRX4M90L-GH25-1C1J-QU4E-HBQZCSII3GQA.sdProfile"
```

It reports button counts per page, any path that does not resolve, and the BOT
button's target.
