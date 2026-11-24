# DikGun Stream Stack

Self-hosted streaming stack replacing StreamElements, for Twitch **DikGun** (`141446165`) by default, and YouTube (horizontal + vertical). Windows 11, Python 3.12 / FastAPI, OBS Studio 33 + obs-websocket v5, Aitum Vertical Canvas 1.6.8 + Aitum Multistream 1.0.9. Everything binds `127.0.0.1`; no cloud, no third-party accounts beyond the platforms themselves.

> **Status — November 2026.** Snapshot of a working system, not a finished product. See [Known open items](#28-known-open-items-deferred) and [Known duplication & planned consolidation](#29-known-duplication--planned-consolidation).

---

## 1. Repository map

```
dikgun-stream/
├─ overlay-server/          FastAPI app: REST + WebSocket hub + OBS proxy + chat + scanners
├─ streamer-mode/           Desktop automation, Streamer.bot config, deck scripts, detect templates
├─ obs-build/               OBS scene generators, vertical/HUD builders, design docs
├─ promo/                   Highlight/promo video generator + asset renderers
├─ streamdeck/              AJAZZ AKP153E Stream Dock profile + installer
├─ discord-bot/             Discord bot (talks to the overlay server over HTTP)
├─ brand/                   Avatars, banners, thumbnails, vector mark, generators
├─ obs/scenes/DikGun.json   Committed OBS scene-collection snapshot (restored by install.ps1 -Scenes)
├─ install.ps1              Machine setup: -Check | -All | -Scenes
├─ backup.ps1               Backs up machine-local state git does not hold
├─ SETUP.md / RECOVERY.md / README.md
```

**Runtime architecture.** `server.py` holds a config dict, an `OBSClient` (async obs-websocket v5), a `TwitchIRC` reader, an `AutoClipper`, and a WebSocket `HUB`. Overlays connect to `/ws?role=overlay&ch=<channel>`; the dashboard to `/ws?role=control`. Message kinds are `config` (pushed on connect and every save), `event`, and `obs_event`, plus `autoclip` state frames. Events enter by four paths — Twitch IRC, YouTube Innertube chat, `POST /api/event`, `GET /api/hook` — and all fan out through the same hub.

---

## 2. Overlay server

**(a) What it does.** Serves the dashboard, ten overlay pages, the clip player, Deadlock item art and media with HTTP range support; brokers events to browser sources over WebSocket; proxies OBS scene/audio/stream control; runs the chat engine, both chat readers, the scanners' HTTP surface and the auto-clipper.

**(b) Where.** `overlay-server/server.py` (~2,180 lines, 52 routes), `obs_client.py`, `autoclip.py`, `yt_chat.py`, `chat_engine.py`, `twitch_irc.py`, `input_capture.py`, `clips.py`, the four scanners, `config.json` (gitignored) / `config.example.json`, `public/dashboard.html`, `public/chatroom.html`, `public/overlays/*.html`, `public/assets/scene.css|scene.js`, `public/items/`, `start.bat`.

**(c) How it works.** FastAPI with a lifespan that starts concurrent tasks: `obs-client`, `twitch-irc`, `input-pump`, `deadlock-poll`, `kill-watch`, `item-watch`, `capture-janitor`, `autoclip`, `yt-chat`. Config is `DEFAULT_CONFIG` deep-merged with `config.json` (extras in the file survive), so a missing key never crashes a route. The obs-websocket password is read from OBS's own plugin config at runtime rather than stored.

**(d) Operate.**

```powershell
cd overlay-server
python -m uvicorn server:app --host 127.0.0.1 --port 7475     # or: start.bat
start http://127.0.0.1:7475/
```

**(e) Limits.** Binds loopback only. OBS's embedded Chromium caches overlay pages hard — after editing `public/`, restart the server **and** run `refresh-scene-sources.ps1` (see §27).

---

## 3. Dashboard

**(a) What it does.** Single control surface: stream title + category (pushed to **Twitch and YouTube at once**), topic card, panic, per-source audio faders, scene switching, stream/record toggles, hints, polls, checklists, viewer counts, overlay previews and an event log.

**(b) Where.** `overlay-server/public/dashboard.html`; endpoints `/api/stream/info`, `/api/topic`, `/api/panic`, `/api/obs/*`, `/api/hints/*`, `/api/poll/*`, `/api/checklists*`, `/api/viewers`, `/api/status`.

**(c) How it works.** `POST /api/stream/info` writes `stream.title`/`stream.game` to config, broadcasts a `config` frame so every scene label updates live, then posts to Streamer.bot's `DoAction` with args `dashTitle`/`dashGame`. Both platforms are driven through Streamer.bot deliberately — it holds the authorised accounts, so credentials stay in one place. Checklists are 23 startup and 18 shutdown items persisted in config with toggle/reset endpoints.

**(d) Operate.**

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:7475/api/stream/info `
  -ContentType 'application/json' -Body '{"title":"Gear donation center.","game":"Escape from Tarkov"}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:7475/api/panic
```

**(e) Limits.** Title/category sync requires Streamer.bot's HTTP server on `127.0.0.1:7474` with an action containing *Twitch → Channel → Set Title/Game* mapped to `%dashTitle%`/`%dashGame%` (using `%title%`/`%game%` silently shadows the arguments). Categories are validated against `/api/twitch/categories`.

---

## 4. Twitch IRC chat

**(a) What it does.** Reads channel chat anonymously and turns PRIVMSG/USERNOTICE into overlay events: chat lines, subs, resubs, gift subs, raids, bits cheers.

**(b) Where.** `overlay-server/twitch_irc.py`; wired at `server.py`.

**(c) How it works.** Anonymous read-only IRC (`justinfan`) with no token and no bot account, so nothing can be sent — it is a pure event source. `_irc_emit()` fans each event to the channel's overlays and the dashboard, tagging `source: "twitch"`, then feeds chat lines into `CHAT.handle()` for commands.

**(d) Operate.** `config.json → twitch.channel` (default `"dikgun"`), `twitch.enabled`. Verify with `GET /api/status` and `/api/chat/simulate`.

**(e) Limits.** Read-only. Followers are never announced on IRC, so follow alerts must come from Streamer.bot via `GET /api/hook`.

---

## 5. Chat commands, Hint Jar, polls, counters

**(a) What it does.** Six commands, a viewer "hint jar" for game ideas, live polls with tallies, and mod-run kill/death counters.

**(b) Where.** `overlay-server/chat_engine.py`; endpoints `/api/hints/reveal`, `/api/hints/clear`, `/api/poll/start`, `/api/poll/end`, `/api/chat/state`, `/api/chat/simulate`.

**(c) How it works.** `handle()` ignores anything not starting with `!`, splits `!cmd arg`, and dispatches by reflection to `_cmd_<name>` — a throwing command returns an empty list so a bad line can never kill the feed.

| Command | Behaviour |
|---|---|
| `!hint <idea>` | Appends to the jar. 240-char cap, 45 s per-user cooldown, jar capped at 200 |
| `!hints` | Reports how many are still unrevealed |
| `!vote <n or name>` | One vote per user, changeable; resolves to the **original option casing** |
| `!kill`, `!death` | Increment counters — **moderator only** |
| `!hype` | Fires a hype alert, 25 s global cooldown |

Polls hold ≤6 options. **(e) Limits.** **No betting, wagering, points or currency system exists.** No TTS. Counters are manual and persist until reset.

---

## 6. YouTube viewers

**(a) What it does.** Reports concurrent viewers per platform with an explicit confidence state so "nobody is watching" is never confused with "we cannot know".

**(b) Where.** `server.py` — `_youtube_viewers_sync()`, `_twitch_viewers_sync()`, `GET /api/viewers`.

**(c) How it works.** YouTube: Data API v3 `videos.list?part=liveStreamingDetails,snippet` → `concurrentViewers`, keyed by `youtube.videoId` and `viewers.youtubeApiKey`. Twitch: public channel lookup → `viewersCount`. Each source returns `state` ∈ `ok | offline | error | unconfigured`; failures carry a `detail` string rather than a fake `0`.

**(d) Operate.** Add to `config.json`:

```json
{ "youtube": { "videoId": "<live video id>" }, "viewers": { "youtubeApiKey": "<key>" } }
```

**(e) Limits.** **An API key is still required and not present.** The public watch page does not expose numbers for an unlisted live stream, so there is no unauthenticated fallback.

---

## 7. YouTube chat via Innertube (no OAuth)

**(a) What it does.** Reads YouTube live chat into the same pipeline as Twitch — one chat overlay showing both platforms, with no visible branding telling them apart.

**(b) Where.** `overlay-server/yt_chat.py`; `_yt_chat_loop()` in `server.py`; endpoints `/api/yt-chat/state`, `/api/yt-chat/enable`.

**(c) How it works.** Instead of the OAuth-gated official `liveChatMessages.list`, it uses the same internal endpoint YouTube's own web client does: fetch the watch page, regex the first `liveChatRenderer…"continuation"` token, then POST `youtubei/v1/live_chat/get_live_chat` with the WEB client context, carrying the returned token forward each poll. `parse_actions()` extracts author, text and MOD/OWNER badges. The loop dedupes by message id, polls every 8 s, refetches the token on any non-`ok` status, and backs off 15–30 s. Messages are emitted through `_irc_emit()` with `source: "youtube"` and the **same shape as a Twitch PRIVMSG** — plain text, no logo or branding anywhere in the payload, so nothing on screen reveals where it came from. Overlays that should stay Twitch-only can filter on the `source` field.

**(d) Operate.**

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:7475/api/yt-chat/enable `
  -ContentType 'application/json' -Body '{"enabled":true,"videoId":"<id>"}'
Invoke-RestMethod http://127.0.0.1:7475/api/yt-chat/state
```

Requires `youtube.videoId` and `youtube.chatEnabled`. Enabling takes effect **without a server restart** — the loop re-reads config every pass.

**(e) Limits.** Undocumented endpoint — it changes periodically; the module fails loudly (`no-chat-on-page`, `poll failed`, `live chat not available`) rather than pretending. Because messages flow through `_irc_emit`, YouTube chat **does** trigger commands (including mod-only `!kill` from a YouTube moderator) — intentional, but worth knowing. Read-only.

---

## 8. Alerts pipeline

**(a) What it does.** Normalises follow/sub/resub/giftsub/cheer/raid/tip/host events into one queue and animates them.

**(b) Where.** `overlay-server/public/overlays/alerts.html`; `POST /api/event`, `GET /api/hook`, `_dispatch()`.

**(c) How it works.** `ALERT_TYPES = {follow, sub, resub, giftsub, cheer, raid, tip, host, test}`. `_dispatch()` stamps an id/timestamp, fans the event to the channel's overlay sockets and the dashboard, and returns delivery counts. Two entry points exist because **Streamer.bot's native "Fetch URL" sub-action is GET-only** — hence `GET /api/hook?type=follow&user=%user%`, which lets a follow alert be wired with no C#. Alerts render at `alerts.duration_ms` (4500) with a queue cap of `alerts.max_queue` (6).

**(d) Operate.**

```powershell
# Streamer.bot action: Core -> Network -> Fetch URL
#   http://127.0.0.1:7475/api/hook?type=follow&user=%user%
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:7475/api/event `
  -ContentType 'application/json' -Body '{"type":"cheer","user":"Bob","amount":500,"message":"nice"}'
```

**(e) Limits.** Follows depend entirely on Streamer.bot running. Queue cap of 6 silently drops beyond that. Alert sound is off by default.

---

## 9. Overlays

**(a) What it does.** Ten browser-source pages, one shared design system, each available in horizontal and vertical composition.

**(b) Where.** `overlay-server/public/overlays/`: `alerts.html`, `chat.html`, `goals.html`, `topic.html`, `input.html`, `deadlock.html`, `clips.html`, `starting-soon.html`, `brb.html`, `ending.html`. Shared `public/assets/scene.css` + `scene.js`.

**(c) How it works.** Served by `GET /overlay/{name}`. Each connects to `/ws?role=overlay&ch=<channel>` — channels are `alerts`, `chat`, `topic`, `counters`, `scenes`, `deadlock`. Vertical is the same page with `?v=1`: not a scaled copy — the composition inverts, the timer moves to the upper third and the diagonal steepens.

| Overlay | Channel / source | Notes |
|---|---|---|
| `alerts` | `alerts` | Cards with queue |
| `chat` | `chat` | Per-user colours, `chat.max_messages` (12) |
| `goals` | config `goals` | Follower + sub bars |
| `topic` | `topic` | "Now Playing" card |
| `input` | `input` | Keyboard/mouse visualiser |
| `deadlock` | `deadlock` | K/D/A, W/L, item tower |
| `clips` | clips API | Local recordings player |
| `starting-soon` / `brb` / `ending` | `scenes` | Neosoviet full-frame scenes |

**(d) Operate.** Add as OBS **Browser Source**, 1920×1080, and **uncheck "Shutdown source when not visible"** so the socket stays open. Vertical variants: `/overlay/starting-soon?v=1`. Clear an overlay: `POST /api/overlay/clear`.

**(e) Limits.** A `.css`/`.js` file placed under `public/overlays/` will 404 — only `public/assets/` is mounted at `/assets/`.

### 9.1 Input overlay (anti-cheat safe)

Displays live keyboard/mouse state. `overlay-server/input_capture.py`, `/api/input/state`, `/api/input/simulate`. Deliberately **polls `GetAsyncKeyState`** instead of installing a `WH_KEYBOARD_LL` hook — low-level hooks are exactly what BattlEye/EAC inspect, and this only needs to *display* input, never intercept it. Limit: poll granularity means very fast taps can be missed.

### 9.2 Deadlock panel

Live K/D/A, session W/L, ability/boon values and an item tower with real card art. `overlays/deadlock.html`, `/api/deadlock/*`, `deadlock_hud.py`, `public/items/`. K/D/A is read from the **scoreboard** (Tab must be held, game focused); the damage readout was removed on purpose (panel layout varies between modes).

---

## 10. Scene design system (neosoviet / constructivist)

**(a)** The three full-frame scenes (Starting Soon, BRB, Ending) plus clip player share one look. **(b)** `public/assets/scene.css`, `scene.js`, the three HTML files. **(c)** Palette is the convergent triad used by three independent constructivist systems: `#ECE4D2` paper, `#161412` ink, `#C8161D` red. One axis plus its perpendicular (−30°/+60° horizontal, 60° vertical); corners chamfered, never rounded; no gradients, shadows or glass. **The game label is live** — all three read `stream.game` and update over WebSocket when the dashboard changes category. `scene.js` re-dispatches `{kind:"event",type:"..."}` as a `scene:event` DOM event, the hook for gameplay-driven animation (currently records but renders nothing). **(e)** Purely aesthetic — no political symbols or slogans.

---

## 11. OBS control via obs-websocket

**(a) What it does.** Drives scenes, sources, audio, stream/record, stats and hotkeys without any OBS plugin of ours.

**(b) Where.** `overlay-server/obs_client.py` (hand-rolled async obs-websocket v5 client on `websockets`: op-code protocol, SHA256 challenge auth, reconnect loop, event-subscription mask; exposes `request()`, `scenes()`, `set_scene()`, `stats()`, `on_event`), `obs-build/obs-ws.ps1` (`Connect-Obs`, `Invoke-Obs`, `Close-Obs`, `Get-ObsWsConfig` — reads the password from OBS's plugin config at runtime), `/api/obs/*`.

**(d) Operate.**

```powershell
. .\obs-build\obs-ws.ps1
Connect-Obs
Invoke-Obs 'GetSceneList'
Invoke-Obs 'SetCurrentProgramScene' @{ sceneName = 'GAME_Apex' }
Close-Obs
```

Server proxy: `GET /api/obs/scenes`, `POST /api/obs/scene`, `GET /api/obs/stats`, `POST /api/obs/stream|record`, `GET /api/obs/sources`, `POST /api/obs/source_visibility`, `GET|POST /api/obs/audio`.

**(e) Limits.** **Aitum vertical canvas scenes are invisible to the API** — measured: `GetSceneList` returns only the 12 main-canvas scenes, `GetSceneItemList("V_GAME_Apex")` returns zero items without erroring, and `CreateSceneItem` returns no `sceneItemId`. Anything inside a vertical scene must be edited on disk.

---

## 12. Aitum vertical scenes & Apex HUD insets

**(a) What it does.** Builds the 1080×1920 vertical scene set and reinstates game HUD elements that the vertical crop destroys. **(b)** `obs-build/build_vertical.py`, `add_apex_hud_insets.py`, `reload_apex_hud.ps1`, `install-aitum.ps1`, `BUILD-SPEC.md`.

**(c) How it works.** `build_vertical.py` rebuilds whole vertical scenes declaratively from a `LAYOUT` list — drop-then-recreate, so editing `LAYOUT` and re-running is the iteration loop. Constants: `AITUM_CANVAS = c70dca39-1325-4243-9e11-14d60cb38f29`, `V_W,V_H = 1080,1920`, `GAME_CROP = 420` (420 px off each side → 1080 wide), `GAME_HEIGHT = 1300`. Critically, OBS treats the **`*_rel` fields as canonical** and derives `pos`/`bounds` from them:

```python
half = V_H / 2                      # 960
pos_rel    = {x: (x - V_W/2)/half, y: (y - V_H/2)/half}
bounds_rel = {w/half, h/half}
```

Getting this wrong once placed every inset off-canvas at half size. `add_apex_hud_insets.py` inserts four cropped copies of the capture plus an opaque mask, because Apex's HUD bleeds into the centre crop:

```
HEALTHBAR  crop 13,908   425x122  -> (  0,   0) 480x138
SQUADS     crop 1534,50  265x 75  -> (742,   0) 338x 96
MINIMAP    crop 40,50    256x310  -> (802, 120) 272x329
WEAPONS    crop 1283,956 624x103  -> (455,1148) 620x128
MASK       opaque ink cover       -> (  0,1088) 236x188
input overlay moved (30,1030) -> (620,880), out of the masked corner
```

**(d) Operate.**

```powershell
python obs-build/build_vertical.py
python obs-build/add_apex_hud_insets.py            # dry run
python obs-build/add_apex_hud_insets.py --apply    # writes + timestamped backup
powershell -File obs-build/reload_apex_hud.ps1     # stop OBS unsaved -> apply -> restart
```

**(e) Limits — these cost real time, do not skip.**

- **OBS must be closed for any collection write.** It reads the collection once at startup, holds it in memory, and overwrites the file on exit. Edits made while it runs both fail to appear *and* get destroyed. `reload_apex_hud.ps1` force-stops OBS precisely so it cannot save over the edit; the trade-off is discarding unrelated unsaved OBS changes, and the next launch shows "Crash Detected" (choose *Run normally*, not Safe Mode — Safe Mode skips the collection).
- **OBS normalises a scene item's `name` to its source name on save**, wiping any label. The inset script therefore matches items by **crop rect**, not by name.
- Insets ship unlocked (`locked: true` makes items unselectable and undraggable).
- `build_vertical.py` currently **deletes and rebuilds** the scenes it generates — re-running it wipes the Apex HUD insets and regenerates scene UUIDs. Run `add_apex_hud_insets.py` *after* it.

---

## 13. Game HUD scanning (offline detection)

Four independent detectors populate the highlight pool. All take a lock and write indexes atomically. **(b)** `overlay-server/kill_scan.py`, `marker_scan.py`, `laughter_scan.py`, `deadlock_hud.py`, `scan_lock.py`, templates in `streamer-mode/assets/kill/<game>/`.

- **`kill_scan.py` — Rainbow Six Siege only.** A kill requires **all three** of: score-popup mask ≥ 0.50, red-X mask ≥ 0.40, **and the two matches within 110 px**. The proximity test is what made it usable — without it an orange cargo crate scored 0.74 against the red-X template and produced a false kill. Windows ±20 s.
- **`marker_scan.py` — per-game templates, three modes.** `colour mask` (`mask.png`, Deadlock's red ring), `grayscale` (`mask.png` + `region.json` `"mode":"gray"`, BF6's white skull), and `OCR` (`text.json` naming a word — Apex `ELIMINATED`/`KNOCKED DOWN`, The Finals `ELIMINATED`). `region.json` sets the search box because **not every marker is centred** — BF6's banner sits around x 530–830. Two-stage scanning: `--stage fast` is one cheap OCR pass (~0.1 s/frame) at 1 fps, `--stage slow` runs all passes at higher fps.
- **`laughter_scan.py` — YAMNet.** A real audio-tagging model trained on Google's AudioSet ontology, not a heuristic: 521 sound classes per 0.48 s window, reading the laughter family. Emits ±90 s windows and merges overlaps. Incremental and resumable.
- **`deadlock_hud.py` — OCR panel readers.** `find_player_column`, `read_kda`, `read_scoreboard`, `find_badge`, `find_kill_badge` (template), `read_items` (signature library over 181 art items), `read_damage`. **`scan_lock.py`** prevents the two-scan clobber that once silently dropped 4 R6 files and 5 kill windows.

**(d) Operate.**

```powershell
python kill_scan.py --list --game "rainbow six"
python kill_scan.py --all --game "rainbow six"          # --pad 20 --min-gap 10 --rescan
python marker_scan.py --root "C:\Users\JR\Videos" --all --game apex `
  --folder "Apex Legends" --fps 1 --stage fast
python laughter_scan.py --all --threshold 0.05          # --pad 35 --min-gap 6 --rescan
```

**(e) Limits.** R6 kill detection is the only tuned kill scanner; BF1/BF6/2042 were requested but there is no usable footage. **Tarkov is out of scope** — no kill detection at all. BF6 at 0.75 yields 353 hits → 60 windows of which only **two** are human-confirmed (Battle Royale uses a different gold skull). Deadlock's template is calibrated to **2024** clips — 2025 clips score ~0.60 and are gated out. Apex markers verified 4 of 6 by eye. Text detection for BF6 was tried and abandoned (the "KILL" label is ~14 px light-grey on bright snow).

---

## 14. Deadlock match sync (deadlock-api.com)

**(a) What it does.** Turns public match records into a session tracker: wins/losses this session, last match K/D/A, and the player's Steam persona name. **(b)** `server.py` — `_fetch_match_history()`, `_fetch_steam_profile()`, `_tally_session()`, `dl_sync()`, `_dl_poll_loop()`; `POST /api/deadlock/sync`.

**(c) How it works.** Two public reads, no memory, no injection, no screen sampling: `GET /v1/players/{account_id}/match-history` and `GET /v1/players/steam?account_ids={id}`. Win is encoded as `match_result == player_team`, **verified against `player_match_outcome` across 732 matches**. Each match with `start_time ≥ session_start` increments wins/losses and records `last_result` (match id, won, kills, deaths, assists).

**(d) Operate.** Set `deadlock.account_id` (default `162773939`), then `POST /api/deadlock/sync`; `GET /api/deadlock/state` shows it. **(e)** Post-match only — nothing live; third-party API.

---

## 15. Clip pipeline (library, indexes, windows API)

**(a) What it does.** Turns the folder tree of long session recordings into a flat, game-labelled, playable highlight pool. **(b)** `overlay-server/clips.py`, `curated_highlights.json`, `clips_cache.json` (gitignored), `laughter_index.json` / `kill_index.json` / `marker_index.json` (gitignored), `GET /api/clips`, `GET /api/clips/highlights`, `GET /videos/<rel>`, `overlays/clips.html`.

**(c) How it works.** `scan()` walks per-game folders into `{id, game, title, file, rel, bytes, duration}`. Folder names are normalised through an explicit alias table (`r6siege`, `Tom Clancy's Rainbow Six  Siege`, `bf6` → Battlefield 6); non-games (`captures`, `desktop`, `nvidia`) are excluded. Durations come from `ffprobe`, probed once in a background thread and cached per **root** with a 300 s TTL. `/api/clips/highlights?source=all` merges four detectors — `laughter`, `kill`, `marker`, `curated`.

**(d) Operate.**

```powershell
Invoke-RestMethod 'http://127.0.0.1:7475/api/clips/highlights?source=all'
# player params: /overlay/clips?game=NAME&len=45&start=0.12&mute=0&shuffle=0&v=1
```

**(e) Limits.** Nothing is cut on disk — these are **windows into source recordings** (`<video>` seeks; `start=0.12` plus 0–30 s jitter avoids menu frames). Indexes are machine-local and cost hours to rebuild. Last recorded pool: **224 clips across 10 games**.

---

## 16. Auto-clipping (kill-triggered replay buffer)

**(a) What it does.** Watches the game capture, detects kills, groups them into events, fires OBS's replay buffer when an event closes, then trims the saved buffer down to the event — so a clip is the verified approach-and-kill, not a 60-second dump. **(b)** `overlay-server/autoclip.py`; endpoints `GET /api/autoclip/state`, `POST /api/autoclip/enable`; templates `streamer-mode/assets/kill/apex/live_ref.png` + `live_mask.png`.

**(c) How it works.**

- **Detection is two-stage, because OCR cannot poll.** Measured here: `ocr_hit()` **800–2700 ms/frame**; masked template match **2.2 ms** (14 ms on a wide band); `GetSourceScreenshot` **22 ms at 1920×1080, 43 KB** — a whole poll is ~25 ms and 3–10 Hz is comfortable. The template (fixed `ELIMINATED` word at source `(800,748) 200×46`, brightness-masked) **gates**, and OCR confirms the candidate **off the poll loop** at ~0.8 s.
- **The search band must be wider than the template** (`520,730 900×90`): Apex centres that banner, so the word slides with the player-name length. A tight box found 6 of 15 kills; the wide band found 18 detections bracketing the index's `first_at 22.0` / `last_at 117.0`.
- **Clustering is by gap, not count.** Defaults `cluster_gap 6.0 s`, `max_cluster 15.0 s`, `pre 4.0 s` (more lead-in), `post 1.5 s` (short tail). Swept on real data: gap 6 s → 7 clips of 1–4 kills; gap 12 s chained 18 kills into one 75 s blob.
- **Game comes from the program scene** (`GAME_Apex` → `apex`). On `ReplayBufferSaved` the cluster's kill times are mapped onto the saved buffer (`buffer_secs - (saved_at - k)`), clamped to the file, and trimmed with ffmpeg (CRF 18) into `~/Videos/AutoClips`; the raw save is deleted unless `keep_raw`.

**(d) Operate.**

```powershell
# start the replay buffer in OBS first (configured for 60 s, not running by default)
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:7475/api/autoclip/enable `
  -ContentType 'application/json' -Body '{"enabled":true}'
Invoke-RestMethod http://127.0.0.1:7475/api/autoclip/state
```

Config surface: `enabled`, `poll_ms` (300), `shot_width` (1920), `hit_debounce` (1.4), `cluster_gap`, `max_cluster`, `pre`, `post`, `min_hits`, `keep_raw`, `out_dir`, `threshold` (0.45), `confirm_ocr`, `confirm_threshold`.

**(e) Limits.**

- **Never exercised against a live game.** The screenshot → template → `SaveReplayBuffer` → trim path is complete and validated on recorded footage; it has not fired a real buffer.
- **Only Apex has a live template.** Deadlock falls back to `find_kill_badge` (43 ms); **BF6 and The Finals have no live template** and the watcher reports that rather than silently idling.
- The template's position is calibrated to one HUD scale — if the current HUD differs, the gate will not fire.
- Auto-clipped files are **not yet written into the scanner indexes**, so promos cannot see them until a rescan.

---

## 17. Promo / highlight video generator

**(a) What it does.** Builds a ~60–90 s branded promo (or a plain music edit) from the clip library: cuts snap to the music, every kill lands on an onset, DIKGUN letter cards punctuate the drop, and an outro closes it. **(b)** `promo/promo_build.py`, `make-promo.ps1`, `render_stingers.py`, `render_outro.py`, `make_mark.py`, `analyse.py`, `measure_flashes.py`, `verify_style.py`, `check_cuts.py`, `CHEATSHEET.md`, `README.md`.

**(c) How it works.**

- **Two-pass, cached extraction.** Segments are extracted to files, concatenated, then the music is mixed. Segments cache in `segcache/` keyed on source + start + length + speed + glitch.
- **Kill-on-beat sync.** The beat should carry the *kill*, which lands `KILL_LEAD = 0.30 s` into the shot (`0.45` starved the tail); `KILL_MIN = 0.70` stops a kill cut shrinking below 0.30 lead + 0.40 tail; `SYNC_MIN_SHOT = 0.35`, `SYNC_MAX_GROW = 0.45`, `MIN_SHOT = 0.85`. Repair slides the boundary between a kill and its predecessor — the previous shot gives up `err`, the kill takes it — so **total duration never changes**. Measured: 48/48 kills within 40 ms (median 0 ms) on the shot-start metric; on the correct kill-moment metric, 41/48 with worst 229 ms where floors and pins block the nudge.
- **Stingers** are six accents spelling D I KGUN once, placed on the **last N kill cuts walking backwards** at `--stinger-gap` (default 4.0 s). Measured: 57.98 / 62.24 / 67.14 / 72.04 / 76.39 / 81.00 s — gaps **4.26–4.90 s**. (The earlier `eligible[-n:]` design took six consecutive kill cuts ~0.9 s apart, producing 1.10 s spacing and almost no gameplay between letters — fixed.)
- **Duration is exact.** Reconciliation spreads corrections across shots instead of clamping one, and frame rounding is measured and given back (`trimmed 2 shot(s) by 0.41 s`) because ~59 segments each ceil to a frame and accumulate ~+0.6 s — which was eating the end of the outro's fade.
- **EDL.** `--save-edl` writes the planned cut; `--from-edl` renders it, so `at`/`len`/`start` can be hand-edited. Measured turnaround: no changes **~7 s**, one shot changed **~18 s**, full rebuild **~5 min**.
- **`--pin`** forces a source range into a window — `PATH|src_start|src_end|at|len`, semicolon-separated for several; the source is speed-fitted (7 s into 3.7 s = 1.89×) and neighbours absorb the difference.
- **`--prefer`** alternates: that game takes **every other** drop cut rather than all of them (taking all produced 24 consecutive jet cuts once).
- **Asset renderers:** `render_stingers.py` draws flat 2D D/DI/DIK/DIKG/DIKGU/DIKGUN cards (the 3D/Blender approach was abandoned); `render_outro.py` builds an 8 s outro — DIKGUN at 0.30, real DG mark forming at 2.00, tagline at 2.60, fade 6.80→8.00; `make_mark.py` mattes the duotone avatar into a transparent mark; `analyse.py` measures the reference edits (duration, BPM, kick times, cut-on-beat %); `measure_flashes.py` classifies transitions; `verify_style.py` detects freeze and flash runs; `check_cuts.py` tests only the frames straddling actual cuts.

**(d) Operate.**

```powershell
cd promo
python render_stingers.py
python render_outro.py --out outro_frames --dur 8.0 --beats 0.30,2.00
powershell -File make-promo.ps1 -Music black-sky.m4a -Game all -Duration 90 -Outro outro.mp4
powershell -File make-promo.ps1 -Music song.mp3 -Game "Apex Legends" -Duration 60 -DryRun
powershell -File make-promo.ps1 -Music song.mp3 -Game all -Mode edit -Duration 180
powershell -File make-promo.ps1 -Music black-sky.m4a -Game all -Duration 90 -SaveEdl cut.json -DryRun
powershell -File make-promo.ps1 -Music black-sky.m4a -Game all -Duration 90 -FromEdl cut.json -Out fixed.mp4
powershell -File make-promo.ps1 -Music s.mp3 -Game all -Pin "Q:\Game Clips\BF6\Cobra.mp4|12|19|32.0|3.7"
python promo_build.py --songs "black-sky.m4a|42|78; WUBS.opus|0|40" --duration 60 --games "Apex Legends"
```

`promo_build.py` takes 18 flags: `--music --songs --out --games --duration --start --outro --save-edl --from-edl --vertical --stinger-gap --sync-tol --no-sync --no-cache --mode {promo,edit} --pin --prefer --dry-run`. `-Start` is where in the track the video begins — for **Black Sky** (262 s), `42` = build then drop, `78` = straight into the drop.

**(e) Limits.** **Outro beats are hand-set** (`0.30, 2.00`) and sit ~80–120 ms off the actual kicks. **`--prefer` starves the wide rotation** — R6 and Deadlock get very little when Battlefield 6 is favoured across five games. **Deadlock repeats after ~60 s** (10 kill windows). **Tarkov cannot drive a beat-synced cut.** `--pin` does **not** validate `src_end` against file length. **9:16 is supported** via `--vertical` (gameplay centre-cropped; cards fitted on ink). `-Game` must be one comma-separated string (`PositionalBinding = $false`). Audio glitches after upload were traced to `-c copy` concat leaving duplicate AAC timestamps — fixed with `aresample=async=1:first_pts=0` + `-avoid_negative_ts make_zero`.

---

## 18. One-button go-live and panic

**(a)** Takes every output live (or down) from one call, and cuts to safety instantly. **(b)** `POST /api/live`, `POST /api/panic`; `streamer-mode/deck/go-live.bat`, `stop-live.bat`, `panic.bat`.

**(c)** `/api/live` starts OBS's native stream output (Twitch) and triggers the **`VerticalCanvasDockStartStreaming`** hotkey (YouTube vertical), preserving the ordering so the vertical canvas is live before the program scene changes; `/api/panic` mutes `panic.muteSources` and cuts to `panic.scene`.

**(d) Operate.**

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:7475/api/live -ContentType 'application/json' -Body '{"action":"start"}'
```

**(e) Limits.** **YouTube horizontal is not automatable.** Aitum Multistream registers no start/stop hotkey (checked against all 354 OBS hotkeys), so that output needs a manual click in the Aitum dock; `yt-live.bat` only brings OBS forward to make the click obvious. Twitch and YouTube-vertical *are* fully automatic.

---

## 19. Stream Deck profile (AJAZZ AKP153E)

**(a)** 18-key hardware control surface. **(b)** `streamdeck/install.ps1`, `verify_profile.py`, `profile/`; generator `obs-build/build_streamdeck_profile.py`; layout doc `obs-build/STREAMDECK.md`; action scripts `streamer-mode/deck/`.

**(c)** The generator writes the whole profile — icons included — into `%APPDATA%\HotSpot\StreamDock\profiles\`. 16 of 18 keys are used: nine scene keys send bare **F13–F21** (real Windows virtual keys no physical keyboard has, so zero collision risk and no modifier chords), plus LIVE / REC / CLIP(VCAM) / NEXT / PREV / STATS.

**(d) Operate.**

```powershell
python obs-build/build_streamdeck_profile.py
powershell -File streamdeck/install.ps1
# Stream Dock AJAZZ -> profile dropdown -> "DikGun Stream"
```

**(e) Limits.** **The seven plugin keys are dead** — they use `com.gabilan.obs` (OBS Studio – StreamDock), which fails its websocket handshake; they need host `127.0.0.1`, port `4455` and the websocket password entered inside the plugin config once. The nine F13–F21 scene keys work immediately.

---

## 20. Streamer mode / desktop automation

**(a)** Puts the desktop into a broadcast-safe layout and lifecycle with one command, and reverses it. **(b)** `streamer-mode/Streamer-Mode.ps1`, `Test-Tiling.ps1`, `Stream Mode.bat` / `Dev Mode.bat` / `Restore.bat`, `deck/` (29 scripts).

**(c)** Tiles windows into fixed zones, pins taskbar/icons, starts and stops the applications in the layout, and closes anything with a visible window that is not part of it (graceful, then forced) — **Streamer.bot is deliberately exempt** because it sources the follow alerts. Deck scripts: `go-live`, `stop-live`, `panic`, `mic-toggle`, `hint`, `chat`, `dashboard`, `kill`/`assist`/`damage`, `yt-live`, `launch-eft`, `launch-deadlock`, `Day Profile`, `Night Profile`, `Fix RivaTuner`, plus `Deck-Action.ps1` and per-peripheral helpers (`rivatuner-profile.ps1`, `bench-display.ps1`, `benq-presets.json`, `eft_play.py`).

**(d) Operate.** `streamer-mode\Stream Mode.bat` / `Dev Mode.bat` / `Restore.bat`.

**(e) Limits.** Streamer mode closes programs — anything not in the layout dies. Game launchers are brittle: the **EFT Play click is image-verified**, not API-driven (the BSG launcher is CefSharp and exposes nothing to UI Automation), so a launcher update needs `assets/eft-play-ref.png` recaptured; **Deadlock needs mods already applied** — the preflight checks `gameinfo.gi` for the addons search path and warns rather than silently running vanilla.

---

## 21. Discord bot

**(a)** Community server automation wired to real stream state. **(b)** `discord-bot/bot.py`, `bot-control.ps1`, `setup.ps1`, `blueprint.json`, `SETUP.md`, `README.md`, `WALKTHROUGH.md`, `.env` (gitignored).

**(c)** Runs beside the overlay server and talks to it over HTTP, so answers are state-derived rather than guessed. Slash commands: `/live` (title, game, scene, uptime, Twitch state), `/clip [game]` (pulls a random highlight and uploads an excerpt or thumbnail), `/clips` (index counts by game), `/goals`, `/checklist`, plus staff-only `/purge`, `/timeout`, `/kick` with a `#mod-log` audit channel. `watch_stream()` polls the server and announces going live into `#live`; `--provision` builds the whole category/channel/role structure from `blueprint.json`.

**(d) Operate.** `python bot.py` / `python bot.py --provision` / `python bot.py --check`, or the three `.bat` toggles. **(e)** Needs a bot token in `.env` (not in git) and the overlay server running.

---

## 22. Brand assets

**(a)** Generates and stores channel art — avatars, banners, thumbnails, the vector DG mark and a YouTube description template. **(b)** `brand/`, `brand/generators/make_assets.py`, `trace_mark.py`, `dg-vector.json`, `youtube-description.md`; the promo mark matte is `brand/avatars/03-avatar-duotone-1024.png`.

**(c)** `make_assets.py` renders the set from the vector mark; `trace_mark.py` derives the vector from a raster. **(d)** `python brand/generators/make_assets.py`; `python promo/make_mark.py` for the transparent promo mark. **(e)** Licensing: `streamer-mode/assets/eft_a.png` is a fan-hosted EFT wordmark (CC BY-NC — fine as personal key art, do not redistribute); `dl_logo.png` is the official Deadlock logo from Steam's CDN.

---

## 23. Install, backup, recovery

**(a)** Provisions a machine, protects what git cannot hold, and documents rebuild order. **(b)** `install.ps1`, `backup.ps1`, `SETUP.md`, `RECOVERY.md`, `obs/scenes/DikGun.json`.

**(c)** `install.ps1` runs staged: winget software, OBS plugins, python deps, config, **scene collection from `obs/scenes/DikGun.json`**, YAMNet, deck profile — each stage skipping what already exists. `backup.ps1` copies exactly what git deliberately omits: `overlay-server/config.json`, the three scanner indexes, and the OBS scene collection. `RECOVERY.md` states the rebuild order and what a clone cannot give you (channel config, indexes, Discord token, YAMNet, hundreds of GB of media).

**(d) Operate.**

```powershell
powershell -File install.ps1 -Check        # report only (default)
powershell -File install.ps1 -All          # install and wire up
powershell -File install.ps1 -Scenes       # restore the OBS scene collection
powershell -File backup.ps1 -Dest "E:\dikgun-backup"
```

**(e) Limits.** The scene collection is committed as a snapshot — a scaffold rebuild from `build_scenes.py` is *not* identical to the hand-tuned original, so the snapshot is the real recovery path.

---

## 24. OBS scene inventory

Horizontal (12): `SHOW_Clips`, `SHOW_StartingSoon`, `SHOW_BRB`, `SHOW_Ending`, `JC_Talking`, `CODE_Live`, `GAME_Apex`, `GAME_Deadlock`, `GAME_Tarkov`, `SCREEN_Work`, `SCREEN_Browser`, `SCREEN_Files`.

Vertical (11, on the Aitum canvas): `Vertical Scene`, `V_GAME_Apex`, `V_GAME_Deadlock`, `V_GAME_Tarkov`, `V_CODE_Live`, `V_JC_Talking`, `V_SCREEN_Work`, `V_SHOW_StartingSoon`, `V_SHOW_BRB`, `V_SHOW_Ending`, `V_SHOW_Clips`.

---

## 25. Config surface

`DEFAULT_CONFIG` (authoritative): `autoclip`, `youtube{videoId, verticalVideoId, chatEnabled}`, `alerts{duration_ms,max_queue,sound}`, `starting_soon{minutes,headline,subline}`, `goals{followers,subs}`, `labels`, `chat{max_messages}`, `obs{url}`, `twitch{channel,enabled}`, `stream{title,game}`, `topic{text,label}`, `panic{scene,muteSources}`, `commands{enabled}`, `hints`, `counters{deaths,kills}`, `poll`, `deadlock{...}`. Read but not defaulted: `viewers.youtubeApiKey` (now in `config.example.json`).

---

## 26. Cross-cutting operational rules

1. **OBS caches overlay pages.** Editing `overlay-server/public/` needs more than a restart: pull → restart server → `pwsh -File obs-build/refresh-scene-sources.ps1`, which appends `&cb=<timestamp>` to every scene browser source URL. That is the only reliable way to drop OBS's cached copy.
2. **OBS must be closed for scene-collection writes.** It reads once at startup, holds it in memory and overwrites on save. Correct order: stop OBS (unsaved) → write → start OBS.
3. **Secrets never enter the repo.** No stream keys, tokens or passwords. `config.json` is gitignored (`config.example.json` is committed); OBS/Aitum keys live under `%APPDATA%\obs-studio`; the obs-websocket password is read at runtime.
4. **Index safety.** Scanners take an exclusive lock and write atomically — concurrent scans previously did load-modify-write and silently dropped each other's entries.
5. **Paths baked into scripts:** Python `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`, ffmpeg on PATH (`C:\ffmpeg\ffmpeg.exe`), Tesseract at `C:\Program Files\Tesseract-OCR\`, Streamer.bot at `%USERPROFILE%\Apps\Streamer.bot\`.

---

## 27. Known open items (deferred)

| # | Item | Detail |
|---|---|---|
| 1 | **YouTube OAuth route** | `GET /oauth/youtube/callback` was never implemented. Superseded in practice by Innertube (`yt_chat.py`), so official chat is not needed |
| 2 | **YouTube viewer API key** | Still required (`youtube.videoId` + `viewers.youtubeApiKey`); no unauthenticated path for unlisted live streams |
| 3 | **BF6 / The Finals live templates** | Auto-clipping only has a live template for Apex (Deadlock reuses `find_kill_badge`) |
| 4 | ~~**9:16 vertical promo export**~~ | **FIXED** — `--vertical` renders 1080×1920: gameplay is centre-cropped, letter cards and the outro are fitted and padded on the brand ink so the DIKGUN word is never cut. Verified 1080×1920, cards padded (top-band std 1.63 vs 18–50 for gameplay), horizontal output unchanged |
| 5 | **Outro beats hand-set** | `0.30, 2.00` hardcoded, ~80–120 ms off the actual kicks |
| 6 | **TikTok live** | Not blocked, but manual per session: Aitum Multistream takes a TikTok RTMP key (1080×1920, H264, 60 fps, CBR 8000 kbps, keyframe 2 s) which **expires every session** and must be pasted in the Aitum dock; Multistream exposes no start/stop hotkey, so it is one click, not part of `/api/live`. Alternative: OBS Virtual Camera → TikTok Live Studio (video only; give Live Studio the mic and desktop audio directly or you get doubled audio) |
| 7 | **Auto-clip not live-tested** | Validated on recorded footage at real poll rate; never fired a real replay buffer |
| 8 | **No betting / economy** | No points, currency or wagering system exists |
| 9 | **`--prefer` starves the rotation** | R6/Deadlock get little when BF6 is favoured across five games |
| 10 | **Aitum vertical scenes are API-invisible** | No scene-item read/write; all vertical edits are on-disk only |
| 11 | **YouTube horizontal automation** | Blocked: Aitum Multistream exposes no start/stop hotkey |
| 12 | **OBS plugin deck keys dead** | `com.gabilan.obs` fails its websocket handshake |
| 13 | **No TTS** | Follower-gated TTS never built |
| 14 | **Deadlock damage readout removed** | Panel layout varies between modes; OCR could not be made reliable |
| 15 | **BF6 marker precision is low** | 353 hits → 60 windows, only 2 human-confirmed |
| 16 | **Tarkov has no kill detection** | Explicitly out of scope |
| 17 | **Twitch panels outstanding** | Schedule, Discord invite and donation panels not built |
| 18 | **Per-game scene auto-switching** | Manual; the dashboard tells you which `GAME_*`/`V_GAME_*` to be on but does not switch |
| 19 | **Auto-clip → index sidecars** | Auto-clipped files are not yet written into the scanner indexes, so promos cannot see them until a rescan |

---

## 28. Known duplication & planned consolidation

Found by a full-repo audit. Ranked by impact:

1. ~~**Repo ↔ live-work fork.**~~ **FIXED.** All scripts now resolve paths from their own location (`$PSScriptRoot` / `Path(__file__)`), so a repo clone runs its own code; `backup.ps1` keeps both trees' `config.json` (as `config.json` + `config-alt.json`) instead of silently picking one.
2. ~~**`build_vertical.py` destroys manual scene work.**~~ **FIXED.** It no longer deletes the scenes it generates: it preserves scene UUIDs and hotkeys (F13–F21 bindings survive), keeps scenes it does not own (`V_SHOW_Clips`, `Vertical Scene`), and re-attaches manually added items — unknown sources (HUD mask, input overlay) and extra cropped copies (the Apex/Deadlock/Tarkov HUD insets) — remapping input UUIDs in kept scenes. Writes are now atomic (temp file + `os.replace`) with a timestamped backup, and it is idempotent (verified: two runs produce identical items, 0 dangling refs).
3. **The three scanners are ~60–70 % the same file.** `duration()`, `frames()`, `segments_from_hits()`, index load/save, the CLI scaffold and the 0.5 s dedup are near-identical in `kill_scan.py` / `marker_scan.py` / `laughter_scan.py` (plus a fourth variant in `server.py:_play_windows`). Plan: a shared `scanlib.py` with thin game-specific callbacks; fold `kill_scan` into `marker_scan`'s dual-signal mode.
4. **ffprobe/ffmpeg/audio helpers duplicated ~9×.** ffprobe-duration appears in 9 places; ffmpeg→wav→float32 decode 4×; peak/onset detection 2×; `C:\ffmpeg` hard-coded 5× while install checks PATH. Plan: one media helper module shared by overlay-server, promo and discord-bot.
5. **Two OBS websocket clients** (Python async vs PowerShell) both implement the v5 handshake + auth hash and both read the password from OBS's plugin config. Defensible (different runtimes), but the auth logic is written twice; `reload_apex_hud.ps1` adds a third OBS-control mechanism.
6. **Three scene-collection editors** (`build_scenes.py`, `build_vertical.py`, `add_apex_hud_insets.py`) each hand-write the scene-item dict and the `pos_rel` formula. Plan: one `obs-build/scenelib.py`.
7. **Overlay URL table duplicated** — `refresh-scene-sources.ps1` hard-codes URLs that `build_vertical.py` also constructs, and it targets sources (`OVL_StartingSoon_V`, `OVL_BRB_V`, `OVL_Deadlock_V`…) that no generator creates. Plan: one JSON table consumed by both.
8. **Three drifting config-default copies** (`DEFAULT_CONFIG`, live `config.json`, `config.example.json`). Plan: generate the example from `DEFAULT_CONFIG`.
9. **Deck profile has three sources of truth** (checked-in folder, generator GUID, `manifest.template.json` rendered by nothing, plus a stray `.bak`). Key counts disagree across docs (15 vs 16 vs 17).
10. **Dead code / dangling references** — `read_damage`/`KILL_THRESHOLD` imports, `_capture_scoreboard` (superseded), `SERVER` and `if accent_before or True:` in promo_build, duplicate loaders in deadlock_hud, `blender_letters.py` references, redundant `except (RuntimeError, Exception)`.

Also flagged: `duration()` silently returns 0.0 on probe failure (garbage windows follow); scanner main loops lack `try/finally` around `scan_lock.release`; the HUD watchers screenshot `SRC_Capture_Deadlock` even while playing Apex; `laughter_scan --one` fails without the default root existing; `build_vertical.py` writes the collection non-atomically (no temp file); `.env` and `__pycache__` sit physically in the checkout (gitignored, but an accidental-commit risk).

---

## 29. Docs index

| Doc | Covers |
|---|---|
| `README.md` | this file — features and operation |
| `SOCIAL-STRATEGY.md` | launch plan and posting strategy for X / YouTube / TikTok |
| `SETUP.md` | machine setup and wiring order |
| `RECOVERY.md` | what git gives you, restore order, backup |
| `obs-build/BUILD-SPEC.md` | vertical scene constants and layout spec |
| `obs-build/STREAMDECK.md` | deck key layout |
| `obs-build/DISPLAY-GUIDE.md` | monitor/peripheral presets |
| `promo/CHEATSHEET.md` | every promo flag with copy-paste recipes |
| `promo/README.md` | promo pipeline internals |
| `overlay-server/README.md` | server quick-start (some sections stale) |
| `discord-bot/` README / SETUP / WALKTHROUGH | bot provisioning and commands |
| `streamdeck/README.md` | deck install |
| `brand/README.md` | art generation |
