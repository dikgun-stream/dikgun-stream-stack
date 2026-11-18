"""
Anonymous Twitch IRC client -> overlay events.

Why this exists: reading Twitch chat and most channel events needs **no
authentication**. An anonymous `justinfan` connection to the IRC gateway gives us
chat messages, subs, resubs, gift subs, raids and bits. That covers 5 of the 6
alert types with zero OAuth and zero GUI setup.

What it CANNOT do is followers -- Twitch does not announce follows in IRC, so
that one still requires EventSub (Streamer.bot or our own app credentials).

Read-only by design: we never send messages, only PONG and JOIN.
"""

from __future__ import annotations

import asyncio
import logging
import random

import websockets

log = logging.getLogger("twitch")

IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
CAPABILITIES = [
    "CAP REQ :twitch.tv/membership",
    "CAP REQ :twitch.tv/tags",
    "CAP REQ :twitch.tv/commands",
]


def parse_tags(line: str) -> tuple[dict, str]:
    """Split an IRCv3 `@tags rest` line into (tags, rest)."""
    if not line.startswith("@"):
        return {}, line
    tagpart, _, rest = line.partition(" ")
    tags: dict[str, str] = {}
    for pair in tagpart[1:].split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            tags[k] = v
        else:
            tags[pair] = ""
    return tags, rest


def unescape(value: str) -> str:
    return (value.replace(r"\s", " ")
                 .replace(r"\:", ";")
                 .replace(r"\\", "\\")
                 .replace(r"\r", "\r")
                 .replace(r"\n", "\n"))


class TwitchIRC:
    def __init__(self, channel: str, emit) -> None:
        self.channel = channel.lower().lstrip("#")
        self.emit = emit            # async callback(event: dict)
        self.connected = False
        self.last_error: str | None = None

    async def run(self) -> None:
        backoff = 3.0
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.warning("IRC error: %s", exc)
            finally:
                self.connected = False

            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.7, 30.0)

    async def _session(self) -> None:
        async with websockets.connect(IRC_URL, ping_interval=None) as ws:
            nick = f"justinfan{random.randint(10000, 99999)}"
            await ws.send(f"PASS {nick}")
            await ws.send(f"NICK {nick}")
            for cap in CAPABILITIES:
                await ws.send(cap)
            await ws.send(f"JOIN #{self.channel}")
            self.connected = True
            self.last_error = None
            log.info("Twitch IRC joined #%s as %s", self.channel, nick)

            async for raw in ws:
                for line in str(raw).splitlines():
                    if not line:
                        continue
                    if line.startswith("PING"):
                        await ws.send("PONG :tmi.twitch.tv")
                        continue
                    await self._handle(line)

    async def _handle(self, line: str) -> None:
        tags, rest = parse_tags(line)

        if "PRIVMSG" in rest:
            await self._privmsg(tags, rest)
        elif "USERNOTICE" in rest:
            await self._usernotice(tags, rest)

    # ---------------------------------------------------------------- chat
    async def _privmsg(self, tags: dict, rest: str) -> None:
        try:
            body = rest.split(" :", 1)[1]
        except IndexError:
            return
        user = unescape(tags.get("display-name") or tags.get("login") or "unknown")
        bits = tags.get("bits")

        if bits and bits.isdigit() and int(bits) > 0:
            await self.emit({
                "kind": "event", "type": "cheer",
                "user": user, "amount": int(bits), "message": body,
            })
            return

        await self.emit({
            "kind": "event", "type": "chat",
            "channel": "chat", "user": user, "message": body,
            "mod": tags.get("mod") == "1",
            "badge": tags.get("mod") == "1" and "MOD" or (tags.get("subscriber") == "1" and "SUB" or ""),
        })

    # ------------------------------------------------------------- notices
    async def _usernotice(self, tags: dict, rest: str) -> None:
        msg_id = tags.get("msg-id", "")
        user = unescape(tags.get("display-name") or tags.get("login") or "unknown")
        text = rest.split(" :", 1)[1] if " :" in rest else ""

        if msg_id == "raid":
            viewers = tags.get("msg-param-viewerCount", "0")
            await self.emit({
                "kind": "event", "type": "raid",
                "user": unescape(tags.get("msg-param-displayName", user)),
                "amount": int(viewers) if viewers.isdigit() else 0,
                "message": text,
            })
        elif msg_id == "sub":
            months = tags.get("msg-param-cumulative-months", "1")
            await self.emit({
                "kind": "event", "type": "sub", "user": user,
                "amount": int(months) if months.isdigit() else 1,
                "message": text,
            })
        elif msg_id == "resub":
            months = tags.get("msg-param-cumulative-months", "1")
            await self.emit({
                "kind": "event", "type": "resub", "user": user,
                "amount": int(months) if months.isdigit() else 1,
                "message": text,
            })
        elif msg_id == "subgift":
            recipient = unescape(tags.get("msg-param-recipient-display-name", "someone"))
            await self.emit({
                "kind": "event", "type": "giftsub",
                "user": recipient, "amount": 1, "message": f"gifted by {user}",
            })
