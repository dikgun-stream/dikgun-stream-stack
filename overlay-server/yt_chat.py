"""YouTube live chat, without OAuth.

Why Innertube
-------------
The official liveChatMessages.list endpoint needs an OAuth token with
yt.readonly and a published app. YouTube's own web client uses an internal
JSON endpoint instead - `youtubei/v1/live_chat/get_live_chat` - which works
with only the video id: the watch page carries a continuation token, and every
poll returns the next one.

What this buys
--------------
- no OAuth flow, no token refresh, no client secret to publish
- raw author + text + badges; no YouTube logo or branding anywhere, so a
  message rendered through the chat pipeline is indistinguishable from a
  Twitch message (the user's point: clean it before it lands on stream)

Caveat
------
This is an undocumented endpoint. It changes occasionally; the module fails
loudly with a status string instead of pretending to read chat.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any

INNERTUBE = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
WEB_CONTEXT = {
    "context": {"client": {"clientName": "WEB",
                           "clientVersion": "2.20240101.00.00"}},
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_TOKEN_RE = re.compile(
    r'"liveChatRenderer".{0,600}?"continuation":"([^"]+)"')


def _post(url: str, payload: dict, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA,
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_token(video_id: str) -> str | None:
    """First continuation token from the watch page's initial data."""
    url = f"https://www.youtube.com/watch?v={video_id}&hl=en"
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=15.0) as resp:
        html = resp.read().decode("utf-8", "replace")
    m = _TOKEN_RE.search(html)
    return m.group(1) if m else None


def _run_text(run) -> str:
    if isinstance(run, dict):
        return run.get("text") or ""
    return str(run)


def parse_actions(data: dict) -> list[dict]:
    """Extract chat messages from one poll response."""
    out: list[dict] = []
    actions = (data.get("continuationContents", {})
               .get("liveChatContinuation", {})
               .get("actions") or [])
    for act in actions:
        item = act.get("addChatItemAction", {}).get("item", {})
        msg = item.get("liveChatTextMessageRenderer")
        if not msg:
            continue
        author = (msg.get("authorName") or {}).get("simpleText") or "?"
        runs = msg.get("message", {}).get("runs") or []
        text = "".join(_run_text(r) for r in runs).strip()
        if not text:
            continue
        badges = msg.get("authorBadges") or []
        is_mod = any("MODERATOR" in str(b) for b in badges)
        is_owner = any("OWNER" in str(b) for b in badges)
        ts_us = (msg.get("timestampUsec") or 0)
        out.append({
            "user": author,
            "text": text,
            "is_mod": is_mod or is_owner,
            "ts": float(ts_us) / 1e6 if ts_us else time.time(),
            "id": msg.get("id") or str(ts_us),
        })
    return out


def poll(token: str, timeout: float = 15.0) -> tuple[list[dict], str | None, str]:
    """One chat poll. Returns (messages, next_token, status)."""
    try:
        data = _post(INNERTUBE, {**WEB_CONTEXT, "continuation": token},
                     timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return [], None, f"poll failed: {exc}"
    cont = (data.get("continuationContents", {})
            .get("liveChatContinuation", {}))
    if cont is None:
        return [], None, "live chat not available"
    nxt = (cont.get("continuations") or [{}])[0]
    nxt = (nxt.get("liveChatReplayContinuationData")
           or nxt.get("invalidationContinuationData")
           or nxt.get("timedContinuationData")
           or nxt.get("reloadContinuationData")
           or {}).get("continuation")
    return parse_actions(data), nxt, "ok"
