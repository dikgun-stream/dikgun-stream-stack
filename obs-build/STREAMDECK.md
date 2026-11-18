# Stream Deck Profile — AJAZZ AKP153E

Generated 2026-11-17. Profile: **"DikGun Stream"** (16 of 18 keys used).

## Activate it

1. Open **Stream Dock AJAZZ** (already running).
2. Profile dropdown → select **DikGun Stream**.
   If it isn't listed, restart the app so it rescans the profiles folder.

## Layout

Columns are left→right (col 0-2), rows top→bottom (row 0-5).

| Row | Col 0 | Col 1 | Col 2 |
|---|---|---|---|
| **0** | TARKOV `F13` | DEADLOCK `F14` | APEX `F15` |
| **1** | CODE `F16` | TALKING `F17` | SCREEN `F18` |
| **2** | STARTING `F19` | BRB `F20` | ENDING `F21` |
| **3** | **LIVE** (stream toggle) | **REC** (record toggle) | **CLIP** (replay buffer) |
| **4** | **VCAM** (virtual camera) | NEXT scene | PREV scene |
| **5** | **STATS** (OBS stats) | — | — |

## How the two halves work — and why one half needs setup

**The 9 scene keys work immediately.** They send bare **F13–F21**. Those are
real Windows virtual keys that no physical keyboard has, so they never collide
with anything. OBS is already bound: F13→`GAME_Tarkov` … F21→`SHOW_Ending`, and
OBS kept those bindings through a save cycle (verified).

**The 7 plugin keys need the OBS plugin connected once.** They use
`com.gabilan.obs` (OBS Studio – StreamDock), whose per-action settings are empty —
all connection config lives in the plugin itself. Until it's pointed at OBS,
those buttons do nothing.

To connect it: add any OBS action to a spare key, open its settings panel, and
set host `127.0.0.1`, port `4455`, and the OBS WebSocket password (the same one
in `%APPDATA%\obs-studio\plugin_config\obs-websocket\config.json`). There's also
a **"Connection Tutorial"** action in the plugin that walks through it.

## Why F13–F24 instead of modifier chords

OBS exposes *separate* hotkeys for start/stop and a separate mute/unmute — there
are no true "toggle" hotkeys for most things. Modifier chords like Ctrl+Alt+3
also risk colliding with application shortcuts. F13–F24 sidestep both problems:
one key, one action, zero collision risk. Reserve them for OBS only.

## Regenerating / editing

`build_streamdeck_profile.py` writes the whole profile — icons included — into
`%APPDATA%\HotSpot\StreamDock\profiles\`.

Edit the `SCENES` list or the block of `place(...)` calls and re-run. It writes a
**new** profile each time (fresh GUID), so your existing profiles are untouched.

Note: StreamDock's profile grid is addressed `"row,col"` with 3 columns. The
`KeyModifiers` field is a constant `65536`; the actual modifier state lives in
the `KeyCtrl` / `KeyShift` / `KeyOption` / `KeyCmd` booleans.
