# DikGun Overlay Server

Local overlay + control system for OBS. No cloud, no accounts, no external
services — everything runs on `127.0.0.1`.

## Run it

```bat
start.bat
```

or directly:

```bat
python -m uvicorn server:app --host 127.0.0.1 --port 7475
```

Then open the dashboard: **http://127.0.0.1:7475/**

Dependencies (already installed): `fastapi`, `uvicorn`.

## What's in it

| Piece | Purpose |
|---|---|
| `server.py` | FastAPI app: overlay pages, event fan-out, OBS proxy |
| `obs_client.py` | Small async obs-websocket v5 client (auth + reconnect) |
| `config.json` | Live settings, editable from the dashboard Settings tab |
| `public/dashboard.html` | Test + control UI |
| `public/overlays/*.html` | The overlays themselves |

## Overlays

| Overlay | URL | Shows |
|---|---|---|
| Alerts | `/overlay/alerts` | Follow / sub / gift / cheer / raid / tip cards |
| Chat | `/overlay/chat` | Scrolling chat lines, per-user colours |
| Goals | `/overlay/goals` | Follower + sub progress bars |
| Starting soon | `/overlay/starting-soon` | Countdown + headline |

Add each in OBS as a **Browser Source**: 1920×1080, and **uncheck**
"Shutdown source when not visible" so it keeps its WebSocket connection.

## Dashboard tabs

- **Test** — fire any event by hand, live-scaled previews of every overlay, event log
- **Overlays** — show/hide OBS sources per scene, copy browser-source URLs
- **OBS** — switch scenes, toggle stream/record, live FPS / CPU / lag stats
- **Settings** — alert duration, queue size, sound, countdown minutes, goal targets

Settings save to `config.json` and push to overlays instantly.

## HTTP API

Anything that can POST JSON can drive this — including Streamer.bot.

```
POST /api/event
{ "type": "follow", "user": "Alice" }
{ "type": "cheer",  "user": "Bob", "amount": 500, "message": "nice" }
{ "type": "chat",   "channel": "chat", "user": "Carol", "message": "hi" }
{ "type": "timer",  "channel": "starting-soon", "amount": 5 }
```

Other endpoints:

```
GET  /api/status            server + OBS + overlay connection counts
GET  /api/config            current settings
PUT  /api/config            merge settings
GET  /api/obs/scenes        scene list + current
POST /api/obs/scene         {"name": "GAME_Tarkov"}
GET  /api/obs/stats         fps / cpu / lag / stream + record state
POST /api/obs/stream        {"action": "start" | "stop" | "toggle"}
POST /api/obs/record        {"action": "start" | "stop" | "toggle"}
GET  /api/obs/sources?scene=NAME
POST /api/obs/source_visibility  {"scene": "...", "source": "...", "visible": true}
```

## WebSocket

Overlays connect to `/ws?role=overlay&ch=alerts`. The dashboard connects to
`/ws?role=control`. Messages are JSON with a `kind` field:

- `config` — full settings pushed on connect and on every save
- `event` — one overlay event
- `obs_event` — forwarded OBS events (scene change, stream state) to the dashboard

## Wiring Streamer.bot (next)

Streamer.bot should act as the event source. Any of its HTTP / C# actions can
POST to `http://127.0.0.1:7475/api/event` with the body shapes above. That keeps
platform auth in Streamer.bot and all rendering here.

## Notes

- The server binds `127.0.0.1` only — nothing is exposed to the network.
- OBS websocket password is read from OBS's own config, not stored here.
- If an overlay shows nothing, check `/api/status`; if its count is 0 the browser
  source isn't connected (wrong URL, or "shutdown when not visible" is on).
