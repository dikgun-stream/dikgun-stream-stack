# Display Guide — what to show, and how to keep it clean

Researched 2026-11-17. Two questions: what *should* be on screen per content
type, and how do you show your computer without showing your life.

---

## 1. What each genre actually puts on screen

| Content | Frame | Cam | Chat on stream? | Supporting widgets |
|---|---|---|---|---|
| **Just Chatting** | cam, browser, or video — fills frame | large, or full-frame | **Yes — essential.** Fullscreen viewers keep chat visible | topic/segment card, socials, follower ticker |
| **Gameplay** (Tarkov / Deadlock / Apex) | game, full-frame | small corner | Usually not, or tiny | alerts only; keep the corners clear of HUD and crosshair |
| **Coding** | editor + terminal | small corner | Yes, small | task line, session timer, language/project badge, "bugs fixed" counter |
| **Screen share / browsing** | **one window**, not the desktop | small corner | Yes | a "now showing" label so viewers know what changed |
| **Starting Soon** | countdown | optional | Yes | schedule, socials, what's coming up |
| **BRB** | static art | no | Yes | reason + rough ETA |

Two rules that generalise:

1. **Chat on stream matters most in low-motion content.** In Just Chatting and
   coding, the viewer has no game to look at, so chat is the second channel of
   information. In gameplay it competes with the game and usually loses.
2. **Every overlay element must be earning its pixels during action.** Aim for
   the frame to be readable in one second.

Sources: [chat overlay practice](https://dotesports.com/streaming/news/how-to-add-a-chat-overlay-to-stream-with-obs-and-slobs),
[code readability guidance](https://github.com/calesthio/openmontage/blob/fecb9a1e3cc23ef84de30b272059457a427f16f8/skills/creative/screen-recording.md)
(18–22 px minimum for code at 1080p delivery).

---

## 2. Showing a computer without showing your desktop

### The wrong default

**Display Capture** is what most people reach for and it is the source of most
stream leaks: taskbar, desktop icons, Discord toasts, and OBS itself.
[OBS will even capture its own window](https://obsproject.com/forum/threads/moving-to-another-window-or-tab-removes-the-tab-im-recording.188123/)
unless *Settings → General → Hide OBS windows from screen capture* is on.

**Window Capture** grabs exactly one window and nothing else — no desktop
clutter, no taskbar. It is the correct default for browser, app, and file
content. ([OBS window capture guide](https://obs-versions.com/blog/obs-window-capture-guide))

### The trap nobody mentions

**A browser only renders its visible tab.** A background tab cannot be captured
because it isn't being painted. So browser content must live in its **own
window**, not a tab you switch away from. That one fact reshapes how you arrange
browsing for stream.

### The setup for this machine — three monitors, three jobs

```
ZOWIE XL LCD   1920x1080 @240Hz  primary   ->  PERSONAL ONLY
Hisense        1920x1080 @ 60Hz            ->  STREAM MONITOR (captured)
HP P204        1600x900  @ 60Hz            ->  optional second workspace
```

The Hisense is **exactly 1920×1080**, the same as the OBS canvas, so a monitor
capture of it maps 1:1 with no scaling at all. That makes it the cleanest
possible "blank environment": a physical display where only stream-relevant
windows exist.

**Discipline is the whole mechanism.** OBS captures that monitor faithfully —
including anything you drag onto it. Treat it like a stage: nothing appears
there unless it is meant to be seen.

Checklist for that monitor:

- Turn **off** "Show my taskbar on all displays"
  (Settings → Personalisation → Taskbar → Taskbar behaviours)
- Windows notifications appear on the **primary** display (ZOWIE), so they don't
  leak — but enable Do Not Disturb anyway
- Never drag personal windows onto it, even "for a second"

### When you want one window, not a monitor

Two additional scenes use **Window Capture (WGC)** instead, for showing a single
app while your other screens stay private:

- `SCREEN_Browser` — one browser window + corner cam + chat
- `SCREEN_Files` — one Explorer window + corner cam

Window Capture keeps working when the window is behind others — but **not** when
it is minimised.

### Optional: a truly blank virtual monitor

If you ever want a display that has no physical existence, the
[Virtual Display Driver](https://github.com/nefarius/Virtual-Display-Driver)
adds an Indirect Display Driver monitor you can capture like any other. Worth
knowing about; **not needed here** because the Hisense already fills that role,
and it avoids installing a display driver.

---

## 3. Browser hygiene

Three practical options, best first:

1. **A dedicated browser profile** — a separate Brave/Chrome profile with no
   personal bookmarks, history, saved passwords, or extensions. Point a shortcut
   at it and use it only for stream.
2. **App-mode windows** — chromeless, no tabs, no address bar. Ideal for putting
   a page on screen:
   ```
   brave.exe --app="https://example.com"
   ```
3. **Opera GX Streaming Profile** — a turnkey browser profile built for exactly
   this: hides bookmarks, suppresses notifications, separates personal data
   ([announcement](https://press.opera.com/2025/10/28/your-stream-your-rules-opera-gx-launches-fully-customizable-and-private-streaming-mode/)).

Also worth knowing: [HideDesktopApps](https://pypi.org/project/hide-desktop-apps/0.4.0/)
hides desktop icons, the taskbar, and all windows on a hotkey — useful if you
ever do need a full-desktop shot.

---

## 4. OBS 33 gotcha worth remembering

`monitor_capture` no longer takes an integer index for `monitor_id`. It takes
the **monitor device path**, e.g.:

```
\\?\DISPLAY#HEC0000#5&3683e59d&1&UID4353#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}
```

Passing `1` silently captures nothing (a black frame, no error). Get the real
values from OBS's monitor list, not from the Windows display order.

---

## 5. Scenes that now exist

| Scene | Source | Use |
|---|---|---|
| `SCREEN_Work` | Hisense monitor capture | Dedicated stream desktop |
| `SCREEN_Browser` | window capture + cam + chat | Show one browser window |
| `SCREEN_Files` | window capture + cam | Show one Explorer window |
| `CODE_Live` | IDE window capture | Coding (already existed) |

Set `SRC_Capture_Browser` and `SRC_Capture_Files` to the target window once, in
OBS source properties — they are empty placeholders until you pick a window.
