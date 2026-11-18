# OBS + Streamer.bot Build Spec

**Owner:** JR — Twitch account **`DikGun`**
**Scene collection:** `DikGun`
**Drafted:** 2026-11-17
**Backup of prior config:** `obs-build/backup-2026-11-17_1207/`

---

## 0. Locked decisions

| Decision | Value |
|---|---|
| Multi-stream engine | **Aitum Multistream 1.0.9** + **Aitum Vertical 1.6.7** (both free, GPL 2.0) |
| Retired | `obs-multi-rtmp` v0.7.3, and **SE.Live** — uninstalled, since it cannot coexist with Aitum Vertical |
| Automation | **Streamer.bot** + Speaker.bot (C# for custom work) |
| Alerts / tips / goals / labels | **Built locally** — HTML overlays fed by Streamer.bot's WebSocket |
| Private multi-chat | Social Stream Ninja or CrossChat OBS (read-only, off-stream) |
| TikTok LIVE | **Blocked** — 591 followers, needs 1,000. Clip pipeline instead. |
| Short-form: YouTube | **Fully automated** — API uploads are unrestricted as of Nov 2026 |
| Short-form: TikTok | **Manual post** — public API posting is closed to personal tools (see §10) |
| OBS version | **33.0.1** |
| Canvases | `libobs-main` (16:9) · `Aitum Vertical` = `c70dca39-1325-4243-9e11-14d60cb38f29` (9:16) |

**Why SE.Live rather than obs-multi-rtmp:** the installed SE.Live build advertises, per its own
release notes, per-output frame size, per-canvas and per-output bitrate/encoder settings,
per-canvas transitions, per-canvas replay buffer, linked scenes across canvases, and
**YouTube "Horizontal + Vertical/Shorts"** as a single destination. That is the whole
requirement set. obs-multi-rtmp v0.7.3 predates all of it.

---

## 1. Destination matrix

| # | Destination | Canvas | Frame | FPS | Bitrate |
|---|---|---|---|---|---|
| 1 | **Twitch** (primary) | Horizontal | 1920×1080 | 60 | 6000 CBR |
| 2 | YouTube (main) | Horizontal | 1920×1080 | 60 | 6000 CBR |
| 3 | **YouTube Shorts** (vertical live) | Vertical | 1080×1920 | 30 | 4000 CBR |
| 4 | Twitch 9:16 | Vertical | 1080×1920 | 30 | — *(blocked, see §9)* |

**Total: ~20 Mbps** against 73 Mbps measured upload. Comfortable.

### The vertical destination question

TikTok is out. So the vertical canvas has two candidate homes:

- **YouTube Shorts** — supported natively by the installed SE.Live build, no approval gate. **Recommended.**
- **Twitch 9:16 (Dual Format)** — better for Twitch mobile retention, but SE.Live's own FAQ
  states streamers "must apply for Twitch's Dual Output Beta… approval is managed directly by
  Twitch." SE.Live's newer docs claim it is available to all streamers. **Sources conflict** —
  verify in Twitch Creator Dashboard → Settings → Stream before relying on it.

Either way: **build the vertical canvas once**, then point it at whatever vertical destination
is available. The canvas is the durable asset; destinations are configuration.

---

## 2. Canvas architecture

> **Gotcha worth remembering:** OBS reads the *active* scene collection from **`user.ini`**
> (`[Basic] SceneCollection` / `SceneCollectionFile`), **not** `global.ini`. Editing `global.ini`
> alone is silently ignored — OBS will happily create a fresh empty collection instead.

| Canvas | UUID | Size | Status |
|---|---|---|---|
| `libobs-main` | `6c69626f-…` | 1920×1080 | exists, populated |
| `🚀 SE.Live: Vertical` | `bfb4ec26-…` | 1080×1920 | **exists, empty** |

Use SE.Live **linked scenes** so the vertical canvas reuses sources rather than duplicating them.

---

## 3. Scene tree

### Horizontal canvas

| Scene | Purpose |
|---|---|
| `GAME_Tarkov` | Raid gameplay |
| `GAME_Deadlock` | Matches |
| `GAME_Apex` | Matches |
| `CODE_Live` | IDE capture + coding widgets |
| `JC_Talking` | Full-cam conversation / podcast |
| `SHOW_StartingSoon` | Countdown + cam |
| `SHOW_BRB` | BRB art + chat |
| `SHOW_Ending` | Thanks + socials |

**Prefixes are load-bearing, not cosmetic.** Streamer.bot reads the current scene name via its
OBS `Get Current Scene` sub-action, then branches on the prefix (`GAME_` / `CODE_` / `JC_`).
That is how one command returns different output depending on what is live. Renaming a scene
breaks commands.

### Vertical canvas — per subject

Geometry (1080×1920). Platform UI safe band ≈ **y 210–1480**, **x 60–960**: the top
strip is username/audio, the right rail is engagement buttons, the bottom is caption/CTA.

| Band | Content |
|---|---|
| `y 210–818` | Gameplay / code / screen — **full width 1080**, 16:9 letterboxed (32% of height; TikTok asks for ≥25%) |
| `y 850–1120` | Camera 480×270, centred |
| `y 420–1028` | Camera full-width 1080×608 (talking scenes) |
| overlays | Full-canvas browser sources; their page content self-positions inside the safe band |

| Scene | Sources |
|---|---|
| `V_GAME_Tarkov` | Tarkov capture + cam + alerts + chat |
| `V_GAME_Deadlock` | Deadlock capture + cam + alerts + chat |
| `V_GAME_Apex` | Apex capture + cam + alerts + chat |
| `V_CODE_Live` | IDE capture + cam + chat |
| `V_JC_Talking` | cam full-width + goals + chat |
| `V_SCREEN_Work` | Hisense monitor capture + chat |
| `V_SHOW_StartingSoon` | cam + countdown |
| `V_SHOW_BRB` | BRB art (16:9 centred) + chat |
| `V_SHOW_Ending` | cam + goals + chat |

**Never crop gameplay to portrait.** You lose the information that makes it watchable —
letterbox it and fill the remaining space deliberately with camera, chat, and labels.

Vertical overlays are **separate browser sources** (`OVL_*_V`) rendered at 1080×1920, not
the 1920×1080 ones scaled down, so their internal positioning is correct for the canvas.
Chat on vertical uses `?bottom=470` to stay clear of the platform's bottom UI band.

### Fixes to the existing tree

- `MultiStream Starting Soon` currently displays the **Be Right Back** overlay. There is no
  countdown widget anywhere. Broken first impression.
- Drop the `MultiStream` prefix — only one destination target exists.

---

## 4. Source inventory

### Capture (shared across scenes)

| Source | Type | Notes |
|---|---|---|
| `SRC_Capture_Tarkov` | `game_capture` | **Requires OBS as administrator** — see §8 |
| `SRC_Capture_Deadlock` | `game_capture` | |
| `SRC_Capture_Apex` | `game_capture` | |
| `SRC_Capture_Desktop` | `monitor_capture` | Fallback when game capture fails |
| `SRC_Capture_IDE` | `window_capture` (WGC) | For coding |
| `SRC_Cam` | `dshow_input` | **Currently 640×480@30 — raise it** |

### Overlays — split the fused source

The current single 1920×1080 browser source (`In-Game with Alerts`) bundles everything. Split
into independently movable sources, each its own StreamElements overlay URL:

| Source | Contains |
|---|---|
| `OVL_Alerts` | Alert box only |
| `OVL_Chat_Twitch` | **Twitch chat only** (see §7) |
| `OVL_Goal` | Goal / donation / follower bar |
| `OVL_Labels` | Latest sub/follower/tip ticker |
| `OVL_Stats_<game>` | Per-game stats widget |
| `OVL_Timer` | Raid timer / Pomodoro |

### Coding-specific widgets

- Code as **re-rendered text** (Stream Code VS Code extension), not a window capture.
- Session timer with a **task line** (Pomodoro Pro, wall-clock anchored).
- Counters: bugs fixed, tests passing.

---

## 5. Audio architecture

### Buses

| Bus | Contents |
|---|---|
| `Mic` | `wasapi_input_capture` |
| `Desktop` | `wasapi_output_capture` |
| `TTS` | Speaker.bot via virtual cable |
| `Alerts` | Alert SFX |
| `Music` | Optional bed |

Per-output audio-track routing is handled in the SE.Live multistream dock.

### Mic filter chain (currently **no filters at all**)

```
Noise Suppression (RNNoise) → Noise Gate → Compressor → Limiter
```

- NVIDIA Audio Effects SDK is **not installed**, so NVIDIA noise removal is unavailable. RNNoise
  is built in and is the correct choice here.
- Add ducking so `Desktop` dips under `Mic` and `TTS`.

---

## 6. Encoder & recording

| Setting | Value |
|---|---|
| Encoder | NVENC H.264 (Ampere has **no AV1 encode**) |
| Rate control | CBR |
| Keyframe interval | 2 s |
| Preset | p5 (p6 if encoding lag stays at 0) |
| Profile | High |
| Look-ahead / psycho-visual | On |

### Recording — this is the clip pipeline's source material

| Setting | Value |
|---|---|
| Format | **MKV** (crash-safe; remux to MP4 after) |
| Path | `Q:\Game Clips` (1051 GB free) |
| Bitrate | 20–40 Mbps (quality master, not delivery) |
| Replay buffer | enabled, per-canvas (SE.Live supports this) |

Record the **horizontal** canvas at high bitrate as the master. Do not use the live vertical
render as the clip source — it is composed for live viewing, not for editing.

---

## 7. Chat & TTS compliance

### Hard rule

Twitch ToS §11 requires, while simulcasting, that you **do not** use services that
"combine activity from other platforms or services on your Twitch stream… such as merging chat."
Enforced in practice (a streamer received a formal warning in Feb 2026 for exactly this).

**Therefore:**

| Where | What chat is allowed |
|---|---|
| `OVL_Chat_Twitch` (any canvas, any destination) | **Twitch chat only** — always |
| Private second monitor / mod dashboard | Combined Twitch + YouTube (+ TikTok when live) |
| TikTok / non-Twitch-only encodes | Combined is permissible, but not needed today |

### Follower TTS — no tool ships this; assemble it

1. **Trigger:** Streamer.bot Twitch **Follow** event.
2. **Gate 1 (idempotency):** read persisted user var `ttsFreeUsed%user%`. If set → stop.
3. **Write the flag BEFORE speaking** — crash-proof, not replayable.
4. **Gate 2 (anti-farm):** reject accounts younger than N days.
5. **Speak a template**, e.g. "Welcome {user}." A follow carries no message to read.
6. **Optional:** open a 15-second window for one `!tts <message>` on the same flag.
7. **Queue + cooldown** so a follow wave cannot stack 40 voices.

### TTS safety controls (mandatory, not optional)

Creators are liable for what a TTS tool says. You are opening a microphone to strangers.

- Speaker.bot bad-word filter pass-through: **ON**
- ~200-character cap, reject URLs
- Per-user cooldown; global rate limit
- `!tts skip` / `!tts off` / `!tts volume`
- Word list lives in Speaker.bot, not in a channel command

---

## 8. Required fixes to the current install

| Fix | Why |
|---|---|
| Configure `Settings → Stream` for Twitch | `service.json` is empty `rtmp_custom`; the native stream output currently fails instantly |
| Reconnect Twitch as **DikGun** | `basic.ini` holds a stored token for `pathtopowerpodcast` that expired **Sep 2023** — it is a different account entirely, not just a stale credential |
| Rebind Desktop Audio + Mic/Aux | Both pointed at device GUIDs that no longer exist (Windows `0x80070490`). Fixed to follow the Windows default. |
| Run OBS as administrator | EFT runs under BattlEye elevated; `D3D11 GPU priority setup failed (not admin?)` |
| Clear the stale graphics hook | `update_hook_file: failed to update graphics-hook64.dll (32)` |
| Raise webcam to 720p60 or 1080p30 | Currently captured at 640×480 |
| Delete obs-multi-rtmp | Conflicting fan-out system |
| Cap EFT fps at ~144–165 | Uncapped game starves NVENC → encoding lag |
| Trim startup apps | Memory Compression 5.3 GB and 11.6 GB free at baseline |

---

## 9. Open items

- **Twitch Dual Output approval** — confirm in Creator Dashboard → Settings → Stream.
- **YouTube live eligibility** — needs phone verification + 24 h wait; Shorts live is vertical-only
  and appears in the Shorts feed in the YouTube mobile app only.
- **TikTok LIVE** — re-evaluate at 1,000 followers.
- **Clip pipeline** — separate research pass in progress; will append as §10.

---

## 10. Clip pipeline

### The gate that decides the whole architecture

| Gate | Status (Nov 2026) | Consequence |
|---|---|---|
| **TikTok public posting via API** | **Blocked** | Unaudited clients may only post `SELF_ONLY`, and **the account must be private** while posting. TikTok's audit guidelines explicitly reject *"a utility tool to help upload contents to the account(s) you or your team manages."* | TikTok = render locally, post manually (one tap) |
| **YouTube uploads** | **Open** | Upload cost fell to ~1 unit (Dec 2025); `videos.insert` got its own 100/day bucket (Jun 2026); the unverified-project privacy lock was **reversed Nov 2 2026** | Fully automate YouTube Shorts |
| **Twitch portrait clips** | Open *if* Dual Format is active | `GET /helix/clips/downloads` returns `portrait_download_url` | Vertical framing solved for free — no face tracking needed |

**This is the key strategic read:** YouTube is fully automatable, TikTok is not. That is a
*distribution permission* problem, not a code problem, and it is not worth engineering around.

### Architecture

```
OBS (continuous Hybrid MP4, CQP 18, mic on its own track)
  │
  ├─ obs-websocket :4455 → GetRecordStatus (ms offset), InputVolumeMeters (50 ms)
  │
Streamer.bot (:8080 ws, :7474 http)
  │  triggers: !clip · channel reward · sub · raid · hype train
  ▼
Pipeline service (localhost)  →  review UI :5051  →  faster-whisper  →  ffmpeg  →  publish
```

### Recording & timestamp alignment

| Rule | Detail |
|---|---|
| Format | **Hybrid MP4**, not MKV — crash-tolerant, no remux step, ffmpeg-readable |
| Quality | CQP 18–20, NVENC, 1080p60 — archive quality, not the 6 Mbps stream |
| Source of truth | **Continuous recording**, not the replay buffer |
| Alignment | Poll `GetRecordStatus.outputDuration` **at trigger time** for the ms offset into the file |
| Never trust | `vod_offset` — it is `null` for minutes after a live clip is created |
| Replay buffer | Keep as the "save that" button, but it saves *between keyframes* — users report 21–29 s for a requested 30 s. Use `ffprobe` on the actual file, never the nominal length. |

Transcribe the **mic track only**. This single decision removes most Whisper errors.

### Highlight detection, ranked by value per hour of work

1. **Clip velocity** — viewers clipping the same 90 s is free crowd-sourced curation
2. **Chat rate on unique chatters** — raw message count just detects one spammer
3. **Audio RMS/peak** via `InputVolumeMeters` (50 ms; a high-volume event you must subscribe to)
4. Whisper keyword hits ("clip that", "no way")
5. Game events — last, because it is OCR or per-title APIs and does not generalise

**Realistic expectation:** 40–60% of auto-detected moments are postable. Budget 30–60 s of
review per candidate. The goal is *one 10-minute review pass per stream*, not zero humans.

### Vertical render

- **If portrait clips exist:** use `portrait_download_url`, then add only a caption band and hook
  text inside the safe zone. Zero vision code.
- **Fallback:** split-screen — gameplay on top, camera below — using the **hardcoded cam rect from
  our scene layout**. Never detect the cam; we already know where we put it.
- **Avoid:** center-crop (upscales 1.78× and looks soft) and blur-pad (reads as a lazy repost).

### Captions

`faster-whisper` `large-v3` fp16 with `word_timestamps=True` — about 3–6 s for a 60 s clip on the
3080. Render as **ASS karaoke** (per-word highlight), not `drawtext`. 2–4 words per screen, thick
outline, 1–2 lines, positioned clear of face and HUD.

### Publishing

| Platform | Method | Notes |
|---|---|---|
| YouTube | `videos.insert`, `youtube.upload` scope, resumable | Short = ≤3 min + vertical/square. No `#Shorts` needed. Handle `uploadLimitExceeded` (per-channel, un-purchasable) by backing off to 1/day. |
| TikTok | **Render to `Outbox\TikTok\` + `caption.txt`** | Manual one-tap post. Optional later: `video.upload` inbox scope → drafts, 5 pending/24 h, no audit, no private-account requirement. |

### Build order

1. **One evening** — `!clip` → `CreateClip` → poll → download portrait → ffmpeg → outbox. Ship one post.
2. **Two evenings** — captions + YouTube automation.
3. **One week** — detection + review UI. *This is the part worth streaming; it is the most interesting code and the most visible result.*
4. **Polish** — per-game split-screen templates, hook text from transcript, `loudnorm` to −14 LUFS.

### Failure modes to design for

| Failure | Mitigation |
|---|---|
| `portrait_download_url` is `null` | Fall back to landscape split-screen; **count it** — always-null means the vertical canvas isn't running |
| Clip never appears after 60 s | Cut the local recording at the known offset |
| Offset drift | `GetRecordStatus` at trigger; handle `RecordFileChanged` by resetting origin |
| Whisper hallucination over music | VAD gate + mic-only + discard high `no_speech_prob` |
| Duplicate posts on retry | Unique constraint on `(platform, clip_id)` — non-negotiable |

---

## 11. Build order

1. Fix `Settings → Stream`; remove obs-multi-rtmp.
2. Build horizontal scene tree with correct prefixes.
3. Split the overlay source in the StreamElements dashboard.
4. Mic filter chain + audio buses.
5. Build + link vertical canvas.
6. Set per-output encoder/bitrate in SE.Live multistream.
7. Streamer.bot: OBS connection → TTS safety layer → follower gate → commands.
8. Clip pipeline.
9. Custom overlays via Streamer.bot WebSocket.

### Acceptance tests

- [ ] Both destinations live simultaneously, 0 dropped frames, 0 encoding lag
- [ ] Vertical frame readable on a phone at arm's length
- [ ] All sources restore after an OBS restart
- [ ] Twitch scenes contain **no** non-Twitch chat
- [ ] `!stats` returns correct output for each `GAME_` / `CODE_` / `JC_` scene
- [ ] Follower TTS fires exactly once per account, including follow→unfollow→refollow
