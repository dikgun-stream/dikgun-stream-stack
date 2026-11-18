"""
Chat command engine.

Runs entirely on the anonymous IRC feed we already have, so it needs no auth,
no bot account and no cloud service. Commands are read-and-react: we cannot
*send* to chat (the anonymous IRC connection is read-only), so all feedback is
delivered as system lines on the chat overlay and as overlay effects.

Commands
    !hint <text>      drop an idea into the Hint Jar instead of spoiling chat
    !hint             show usage
    !hints            how many hints are waiting (no text leaked)
    !vote <n>         vote in the active poll
    !death / !kill    increment a counter (mods only)
    !hype             one-shot overlay pulse (rate limited globally)

The Hint Jar is the important one: viewers *want* to help, and a streamer who
says "no backseating" makes them feel ignored. A jar turns that urge into a
consent-based resource the streamer draws from on their own terms.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable


class ChatEngine:
    def __init__(self, config: dict[str, Any], save: Callable[[], None]) -> None:
        self.cfg = config
        self.save = save
        self._cooldown: dict[tuple[str, str], float] = {}
        self._hype_last = 0.0

    # ------------------------------------------------------------------ config
    @property
    def hints(self) -> list[dict]:
        return self.cfg.setdefault("hints", [])

    @property
    def counters(self) -> dict[str, int]:
        return self.cfg.setdefault("counters", {})

    @property
    def poll(self) -> dict:
        return self.cfg.setdefault("poll", {"active": False, "question": "", "votes": {}, "options": []})

    # --------------------------------------------------------------- helpers
    def _cool(self, user: str, cmd: str, seconds: float) -> bool:
        """True if the user may run `cmd` now."""
        key = (user.lower(), cmd)
        now = time.time()
        if now - self._cooldown.get(key, 0) < seconds:
            return False
        self._cooldown[key] = now
        return True

    @staticmethod
    def _sys(text: str) -> dict:
        return {"kind": "event", "type": "system", "channel": "chat",
                "user": " ", "message": text, "ts": time.time()}

    # ---------------------------------------------------------------- handle
    def handle(self, user: str, message: str, is_mod: bool = False) -> list[dict]:
        """Parse one chat line. Returns overlay events to broadcast."""
        msg = (message or "").strip()
        if not msg.startswith("!"):
            return []

        parts = msg[1:].split(None, 1)
        if not parts:
            return []
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        handler = getattr(self, f"_cmd_{cmd}", None)
        if not handler:
            return []
        try:
            return handler(user, arg, is_mod) or []
        except Exception:  # noqa: BLE001 - a bad command must never kill the feed
            return []

    # ------------------------------------------------------------------ hints
    def _cmd_hint(self, user: str, arg: str, is_mod: bool) -> list[dict]:
        if not arg:
            return [self._sys("!hint <idea> — goes in the jar, not chat")]
        if len(arg) > 240:
            return [self._sys("hint too long — keep it under 240 chars")]
        if not self._cool(user, "hint", 45):
            return []          # silent; don't spam the overlay with rejections

        self.hints.append({
            "id": uuid.uuid4().hex[:8],
            "user": user,
            "text": arg,
            "ts": time.time(),
            "revealed": False,
        })
        if len(self.hints) > 200:
            self.hints[:] = self.hints[-200:]
        self.save()
        return [self._sys(f"hint saved ✦ ({len(self.hints)} in the jar)")]

    def _cmd_hints(self, user: str, arg: str, is_mod: bool) -> list[dict]:
        n = len([h for h in self.hints if not h["revealed"]])
        return [self._sys(f"{n} hint(s) waiting in the jar")]

    def unrevealed(self) -> list[dict]:
        return [h for h in self.hints if not h["revealed"]]

    def reveal(self, hint_id: str | None = None) -> dict | None:
        """Pop the oldest hint (or a specific one) and mark it revealed."""
        pool = self.unrevealed()
        if not pool:
            return None
        target = next((h for h in pool if h["id"] == hint_id), pool[0]) if hint_id else pool[0]
        target["revealed"] = True
        self.save()
        return target

    def clear_hints(self, only_revealed: bool = False) -> int:
        before = len(self.hints)
        self.hints[:] = [h for h in self.hints if not (h["revealed"] or not only_revealed)]
        self.save()
        return before - len(self.hints)

    # ------------------------------------------------------------------- poll
    def _cmd_vote(self, user: str, arg: str, is_mod: bool) -> list[dict]:
        p = self.poll
        if not p.get("active"):
            return [self._sys("no poll running")]
        options: list[str] = p.get("options", [])
        if not options:
            return [self._sys("poll has no options")]

        raw = (arg or "").strip()
        if not raw:
            return [self._sys("!vote <option>")]

        # Resolve to the ORIGINAL option casing -- storing the lowercased form
        # made every vote fall out of the tally.
        chosen = None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                chosen = options[idx]
        if chosen is None:
            chosen = next((o for o in options if o.lower() == raw.lower()), None)
        if chosen is None:
            return [self._sys(f"options: {', '.join(options)}")]

        p.setdefault("votes", {})[user.lower()] = chosen   # one vote per user, changeable
        self.save()
        return [self._sys(f"vote recorded: {chosen}")]

    def start_poll(self, question: str, options: list[str]) -> dict:
        p = self.poll
        p.update({"active": True, "question": question,
                  "options": [o for o in options if o][:6], "votes": {}})
        self.save()
        return p

    def end_poll(self) -> dict:
        p = self.poll
        p["active"] = False
        self.save()
        return p

    def poll_results(self) -> dict:
        p = self.poll
        counts: dict[str, int] = {o: 0 for o in p.get("options", [])}
        for v in (p.get("votes") or {}).values():
            if v in counts:
                counts[v] += 1
        return {"question": p.get("question", ""), "active": p.get("active", False),
                "counts": counts, "total": sum(counts.values())}

    # --------------------------------------------------------------- counters
    def _cmd_death(self, user: str, arg: str, is_mod: bool) -> list[dict]:
        return self._bump("deaths", is_mod)

    def _cmd_kill(self, user: str, arg: str, is_mod: bool) -> list[dict]:
        return self._bump("kills", is_mod)

    def _bump(self, name: str, is_mod: bool) -> list[dict]:
        if not is_mod:
            return []                    # counters are mod-only, by design
        self.counters[name] = int(self.counters.get(name, 0)) + 1
        self.save()
        return [{"kind": "event", "type": "counter", "channel": "counters",
                 "user": name, "message": str(self.counters[name]), "ts": time.time()}]

    # ------------------------------------------------------------------- hype
    def _cmd_hype(self, user: str, arg: str, is_mod: bool) -> list[dict]:
        now = time.time()
        if now - self._hype_last < 25:
            return []
        self._hype_last = now
        return [{"kind": "event", "type": "hype", "channel": "alerts",
                 "user": user, "message": "", "ts": now}]
