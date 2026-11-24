# TikTok LIVE Studio — programmatic control (RE notes)

Goal: drive LIVE Studio from our own stack so TikTok can join the multi-platform
setup without rebuilding overlays by hand.

Status: **schema extracted, transport pending.** Awaiting one app restart with a
debug port.

---

## What was installed / found

| Thing | Value |
|---|---|
| App | `C:\Program Files\TikTok LIVE Studio\1.40.2\TikTok LIVE Studio.exe` |
| Runtime | Electron + `@bytegecko/node-client` 1.6.4 (ByteDance's libobs binding) |
| Payload | `resources\app\` — **unpacked JS** (no asar), main logic in `electron\main-bundle.js` (7.3 MB) |
| User data | `%APPDATA%\TikTok LIVE Studio` (940 MB) |
| Local service ports | probed from `LS_PORT_LIST = [28189, 39728, 34246, 42205, 38534, 40825, 40622]`; only **28189** is listening, answers HTTP 404, no WS upgrade |
| CDP | not enabled in the running instance (must relaunch with `--remote-debugging-port`) |

## The API is fully documented inside the bundle

The RPC layer is protobuf-based; every service and message is present as a JSON
descriptor in `main-bundle.js`. Extracted by `_extract.js` → `descriptors.json`
(214 KB) and `namespaces.json`:

**42 namespaces, ~36 services.** The ones that matter:

| Namespace | Methods we need |
|---|---|
| `ls_layout` | `CreateScene`, `CloneScene`, `RemoveScene`, `SwitchScene`, `SceneInfo`, `EnumVisuals`, `CreateCanvas` (`ls_canvas`) |
| `ls_source` | `CreateSource`, `DestorySource`, `PauseSource`, `ResumeSource`, `ReopenSource` |
| `ls_browser` | `Create`, `CreateFromStore`, `GetBrowserInfo` |
| `ls_visual` | `SetVisualUniAttr`, `GetVisualUniAttr`, `GetVisualSnapshot`, `MoveOrder`, `Select` |
| `ls_camera` / `ls_game` / `ls_window` / `ls_monitor` / `ls_image` / `ls_fav` | `Create`, `Enum*`, `Select` |
| `ls_rtmp` | `StartStream`, `StopStream`, `GetActiveStatistic` |
| `ls_state` | 122 push-event callbacks (`OnVisualCreated`, `OnStateChangeEvent`, …) |

Key enum — **browser sources are a first-class source type**:

```
SOURCE_TYPE_NONE=0 WINDOW=1 GAME=2 MONITOR=3 FAV=4 IMAGE=5
CAMERA=6 ANALOG=7 BROWSER=8 BYTELINK=9 GRAFFITI=10 RTC=11 VIRTUAL_CAMERA=12
```

Message shapes (from `descriptors.json`):

```jsonc
// ls_source.CreateSource
{ "sourceType": "SOURCE_TYPE", "sourceInfo": "SourceInfo" }     -> { "sourceId": string }
// SourceInfo is a oneof of: cameraInfo, analogInfo, virtualCameraInfo, gameInfo,
// windowInfo, monitorInfo, favInfo, imageInfo, browserInfo, bytelinkInfo,
// graffitiInfo, rtcInfo
// ls_layout.CreateScene
{ "sceneInfo": "SceneInfo" }                                    -> { "sceneId": string, "canvasIds": [string] }
// ls_browser.Create
{ "visualUniAttr": "ls_visual.VisualUniAttr", "createParam": "CreateParam" }
                                                                -> { "visualId": string, "sourceId": string }
// ls_visual.SetVisualUniAttr
{ "visualId": string, "visualUniAttr": "VisualUniAttr" }        -> { "visualUniAttr": ... }
```

`VisualUniAttr` is the transform (position/scale/crop/visible/lock) — the same
fields the OBS importer maps (`scaleX`, `scaleY`, `x`, `y`, `rotation`,
`crop.left/top/right/bottom`).

## The transport problem, and the way around it

The JS calls look like `ge.call.ls_rtc.Start({...}, "withoutResponse")`, where
`ge` is a client provided by the native addon. That binary transport is the one
piece not worth reimplementing.

**Instead we drive the app's own JS over CDP** — restart with
`--remote-debugging-port=9222`, then `Runtime.evaluate` against the main window:

1. `probe_cdp.py` finds the renderer target and locates the webpack chunk
   (`self.webpackChunk_ttfe_tiktok_live_studio`) plus any exposed `call`-style
   surfaces.
2. From there, pull the app's own client module via the webpack require
   (`webpackChunk.push` trick) and call `ls_layout` / `ls_source` / `ls_visual`
   the same way the app does.
3. Wrap that in `livestudio_client.py` for our stack:
   `ensure_scene(name)`, `create_browser_source(scene, url, rect)`,
   `create_camera(...)`, `create_game_capture(...)`, `start_stream()`.

## Why this is worth it vs the importer

The built-in importer (`importConfig`, reads `%APPDATA%\slobs-client\basic.ini` +
`SceneCollections\*.json`) only maps:

```
image_source, monitor_capture, text_gdiplus, ffmpeg_source, dshow_input, window_capture
```

— **no browser_source, no game_capture**. That is why a faithful clone was
impossible by import, and why driving the API directly is the better route: it
can create `SOURCE_TYPE_BROWSER` sources pointing at our overlay URLs.

## Commands

```powershell
cd C:\Users\JR\rev\livestudio
powershell -File launch-debug.ps1 -Force     # close + relaunch with CDP on 9222
python probe_cdp.py                          # read-only recon
```

## Risks / caveats

- Version-fragile: re-check `descriptors.json` after each LIVE Studio update
  (re-run `_extract.js` against the new `main-bundle.js`).
- CDP requires the app to be launched with the flag; the normal shortcut does not
  have it.
- Automated control may fall outside TikTok's acceptable use; keep it to local
  scene/source management, and do not automate anything that inflates metrics.
- The app's own UI stays in charge of login and going live; we only manage
  scenes/sources unless we deliberately call `ls_rtmp.StartStream`.


---

## BREAKTHROUGH — the app's own API is reachable (verified)

CDP attaches and the app's service layer is exposed on the first page target:

```js
__studio_runtime__.features.studio.service   // 76 service namespaces
  .source   .layout   .streamSetting   .live   .browserSourceManager   ...
```

Calls are proxied to the native service over ServiceCore IPC. Unknown methods
throw `ServiceCore BUG: <Service>.<name> is not a function`, which makes
existence probing safe and unambiguous.

**Verified working**

| Probe | Result |
|---|---|
| `source.getCurrentSceneSourceParamsSnapshot()` | `[]` (real data) |
| `source.createScene({name:'X'})` | `{"code":0}` — **scene created** |

**Real method table** (recovered from the renderer's own call sites):

```js
source.createScene(payload, ?, ?, ?)        // (name + options)
source.deleteScene(id) / duplicateScene(id)
source.updateSceneInfo(id, patch) / updateOrientation(o)
source.getCurrentScene() / getSceneById(id)
source.getCurrentSceneSourceParamsSnapshot()
source.addSource(sceneId, payload)
source.removeSource(sceneId, sourceId)
source.updateSource(sceneId, sourceId, opts)
source.updateSource(sceneId, {payload}, opts)
source.updateSourceTransform(sourceId, transform, syncToSdk, recordHistory)
source.addCameraSourceToScene(sceneId, params)
source.copySource / replaceSource / rotateSource / flipSourceHorizontal
source.centerSource / stretchSource / toggleVisible / toggleLock
source.reloadBrowserSourceWin(id)          // <- browser sources exist
source.updateCurrScene(id) / clearAllScene / deleteAllScene
source.createSceneTemplateSource({...}) / removeSceneTemplateSource(id)
sceneModeTools.createBlankScene(ctx, {name, switchToNewScene})
```

Exact proto payloads for the lower-level calls are in `descriptors.json`
(e.g. `ls_layout.AddVisuals{canvasInfo:{sceneInfo,canvasId}, visualIds[]}`,
`ls_layout.RemoveScene{sceneInfos:[{layoutMode, sceneId}]}`,
`ls_source.CreateSource{sourceType, sourceInfo}` with `SOURCE_TYPE_BROWSER=8`).

**Remaining unknowns** (next session):
1. Scene enumeration — `enumVisuals` needs a JS-level shape not yet matched;
   needed to get scene ids for `deleteScene`/`RemoveScene`.
2. `addSource` payload for a browser source (rect + URL fields).
3. Transform mapping from our OBS geometry into `updateSourceTransform`.

**Cleanup owed:** two test scenes were created while proving `createScene` —
`DSH_TEST_<ts>` and `DSH_TEST_<ts>_B`. Delete them in the LIVE Studio UI, or
remove them programmatically once scene enumeration works.


---

## Session 2 — what else was proven

**Editor state (dual canvas).** The studio runs two canvases at once — `Portrait`
and `Landscape` — each with its own source list. Verified live:

```
Portrait : Alert, Goal, Game capture, Camera, Image
Landscape: Alert, Chatbox, Goal, Camera, Game capture, Image
```

`source.getCurrentSceneSourceParamsSnapshot()` returns the current scene's
sources with `id`, `name`, `type` (lowercase: `alert`, `goal`, `game`, `camera`,
`image`, `chatbox`), `visualId`, `payload` (including `cx`, `cy`, `zoom`).

**`source.addSource` contract** (from the app's own call sites):

```js
source.addSource(
  { name, type, payload: {...}, track, transform },
  { sync, recordHistory, syncSourceParams }        // -> { id, result }
)
```

**A Link (URL) source exists in the UI.** The add-source dialog (`html/popupEntry`
window) lists: Game capture, Camera, Display capture, Phone camera, Window
capture, Image, Text, **Link**, Video, Cast, Capture card, Stream relay, plus
TikTok's own widgets. So arbitrary-URL sources are a supported user feature.

**Trying to create one through the API returns `result: false`.** A link
descriptor `{name, type:'link', payload:{url, cx, cy, zoom}}` gets an id back but
does not attach. The app's own browser-source class (`BrowserBaseVisual`) does
more work:

```
_initBrowserWin()  -> callMain('initBrowserWin', visualInfo)   // privileged bridge
                   -> { token, capture_dll, device_id, webviewWebContentId }
startCaptureWebview / startCaptureBrowserWindow
sourceInfo = { browserInfo: { token, captureDll, deviceId } }
ls_source.CreateSource(SOURCE_TYPE_BROWSER, sourceInfo)
```

`callMain` is reachable from the renderer via webpack module **690861**
(`__dsh_req` was captured by pushing a chunk to
`webpackChunk_ttfe_tiktok_live_studio`), so the token flow is the next thing to
replicate.

**Before building that**, confirm the payoff cheaply: add a Link source by hand
with `http://127.0.0.1:7475/overlay/chat` and check whether the overlay actually
renders inside LIVE Studio. If it does not (localhost blocked, mixed-content,
sandbox), the automation is pointless.

**UI automation note:** `element.click()` is not enough for the dialog — React
needs a full pointer sequence (`pointerover/enter/down`, `mouseup`, `click`) —
and the add-source dialog lives in its own target (`html/popupEntry`), while the
studio canvas is `html/main/index.html`. Browser widgets get their own targets
(`html/browser/index.html?id=<visualId>`), which is a handy way to confirm a
browser visual actually spawned.


---

## IT WORKS — TikTok can show our overlays

Verified end to end on 2026-11-24.

**The blocker and the fix:** LIVE Studio's "Add link" source rejects
`http://127.0.0.1/...` ("Enter correct URL", Add disabled). It accepts a public
**HTTPS** URL. So overlays reach TikTok through:

```
overlay server :7475  →  public_shim.py :7480  →  cloudflared quick tunnel
                     →  LIVE Studio Link source (type "browser")
```

Confirmed working: a Link source pointing at the tunnel created `chat: 1` on the
overlay server's hub — the page loaded, executed and opened its websocket.

**Security posture.** `public_shim.py` forwards only overlay pages, `/assets`,
`/items` and the overlay websocket (allowlisted channels). The whole `/api` tree
- `/api/panic`, `/api/obs/*`, `/api/event` - returns 404 through the tunnel, so
the public URL cannot be used to drive the stream. The page path carries a random
token; browser sources are read-only views of data that is already public on the
stream.

**Geometry.** Landscape canvas 1920x1080 = `track 0`; portrait 1080x1920 =
`track 1`. A Link source is `type: "browser"` (NOT "link" - using "link" returns
`{result:false}`). Payload created by the UI:

```json
{"url": "...", "cx": 800, "cy": 600, "enableAudio": true,
 "enableCustomResolution": false, "zoom": 3.8017840169239308}
```

with `transform {translateX, translateY, rotateAngle, scaleX, scaleY, flipH,
flipV, size:{cx,cy}, preview_cx, preview_cy}`.

## Tools

| File | Purpose |
|---|---|
| `lsclient.py` | CDP client: attaches to the studio window, calls the service layer |
| `livestudio_sync.py` | list / add / update-url / remove overlay Link sources |
| `public_shim.py` | read-only public facade over the overlay server |
| `start-tiktok-overlays.ps1` | one command: server + shim + tunnel + repoint sources |
| `launch-debug.ps1` | relaunch LIVE Studio with `--remote-debugging-port=9222` |
| `descriptors.json` / `namespaces.json` | 42 namespaces of proto schema |

### livestudio_sync.py — verified commands

```powershell
python livestudio_sync.py --list
python livestudio_sync.py --add --pages "chat,alerts,topic,goals" --tracks "0,1" --base <url> --token <tok>
python livestudio_sync.py --update-url <new-tunnel-url> --token <tok>
python livestudio_sync.py --remove --page goals
```

All four exercised: add created 6 sources (`result:true` each), `--update-url`
rewrote all 8 and restored them, `--remove` deleted both goals sources, and the
final scene holds **8 Link sources** (chat/alerts/topic/goals x 2 canvases).

### Daily workflow

```powershell
powershell -File launch-debug.ps1            # once per app start (UAC prompt)
powershell -File start-tiktok-overlays.ps1   # starts everything, repoints sources
```

Quick-tunnel hostnames change every run, which is why `--update-url` exists and
runs automatically at the end of the start script. A named cloudflare tunnel on
your own domain would remove that step.

## Still open

- **Placement**: default transform leaves sources small and stacked; either drag
  once in the studio (persists), or copy a native widget's transform (e.g. the
  `chatbox` source) so our chat lands exactly where TikTok's chat sits.
- The 3-window CDP surface means the studio canvas is `html/main/index.html`,
  the add-source dialog is `html/popupEntry/index.html`, and each browser widget
  is its own `html/browser/index.html?id=...` target - useful for verifying that
  a browser visual actually spawned.


---

## GEOMETRY SOLVED (measured 2026-11-24)

The mirror needs OBS canvas pixels -> TL transform. Two measurements with a
magenta probe source (`/solid/FF00FF` on the shim) nailed it.

**What the API wants:** `source.updateSourceTransform(id, OBJ, sync, record)`
where OBJ is a **SOURCE-shaped object**, not a bare transform:

```js
{ transform: { translateX, translateY, scaleX, scaleY, rotateAngle,
               flipH, flipV, size: {cx, cy}, preview_cx, preview_cy },
  visible: true,
  clip: {x, y, z, w} }        // crop: left, top, right, bottom
```

Passing a bare transform is silently ignored (reads `a.scaleX` off undefined and
falls back to scale 1 / translate 0) - that cost a calibration cycle.

**Coordinate space:** values are in the space declared by `preview_cx/preview_cy`.
Declare them as the CANVAS size (1920x1080 or 1080x1920) and then:

```
translateX, translateY  = canvas px of the source's TOP-LEFT
scaleX                  = target_width  / cx      (cx = the source's own size)
scaleY                  = target_height / cy
```

Measured proof: requested translate (960,540) with preview 1920x1080 stored as
(336,189) and moved the blob +337,+189 preview px, on a 673px-wide preview
(673/1920 = 0.3505). Requested scale 0.5 stored as 0.175 = 0.5 x 0.3505. A
400x300 page at scale 0.5 measured 66x48 preview px (200x150 canvas px x 0.3505
= 70x53, minus page borders). All consistent.

So OBS geometry maps 1:1: `x,y,bounds` -> `translateX,translateY,scaleX,scaleY`.
`clip` is the crop, which is what the Apex/Deadlock/Tarkov HUD insets need.

**Remaining for the builder:** verify a `game` source binds to the running game
(created with an empty payload it succeeded), verify `clip` crops it, and confirm
scene create/switch (`createScene`, `updateCurrScene`).
