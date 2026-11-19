"""
Global keyboard/mouse state watcher for the input overlay.

Deliberately uses GetAsyncKeyState polling rather than SetWindowsHookEx.

A WH_KEYBOARD_LL hook is the classic way to do this, and it is also the kind of
thing BattlEye and EasyAntiCheat inspect - especially relevant here, since
Escape from Tarkov is one of the games being streamed. We only need to *display*
input, never intercept it, and polling reads public key state without hooking
anything. At 60 Hz it is fast enough for on-stream display; it can miss a tap
shorter than ~16 ms, which is not a visible problem for viewers.

Mouse buttons are polled the same way. Scroll wheel is not, because it has no
key state to read.
"""

from __future__ import annotations

import ctypes
import threading
import time
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
GetAsyncKeyState = user32.GetAsyncKeyState
GetAsyncKeyState.argtypes = [ctypes.c_int]
GetAsyncKeyState.restype = ctypes.c_short

# Only poll what the overlay can actually draw. Keeping this small keeps the
# per-frame cost negligible.
_KEYS: dict[int, str] = {
    0x08: "BACKSPACE", 0x09: "TAB", 0x0D: "ENTER", 0x1B: "ESC", 0x20: "SPACE",
    0xA0: "SHIFT", 0xA1: "SHIFT", 0xA2: "CTRL", 0xA3: "CTRL",
    0xA4: "ALT", 0xA5: "ALT",
    0x25: "LEFT", 0x26: "UP", 0x27: "RIGHT", 0x28: "DOWN",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
for _c in range(0x30, 0x3A):          # 0-9
    _KEYS[_c] = chr(_c)
for _c in range(0x41, 0x5B):          # A-Z
    _KEYS[_c] = chr(_c)
for _c in range(0x70, 0x7C):          # F1-F12
    _KEYS[_c] = f"F{_c - 0x6F}"

_MOUSE: dict[int, str] = {
    0x01: "MOUSE1",   # left
    0x02: "MOUSE2",   # right
    0x04: "MOUSE3",   # middle
    0x05: "MOUSE4",   # side 1
    0x06: "MOUSE5",   # side 2
}

ALL_KEYS: list[str] = sorted(set(_KEYS.values()) | set(_MOUSE.values()))


def _down(vk: int) -> bool:
    return bool(GetAsyncKeyState(vk) & 0x8000)


class InputWatcher:
    """Polls global key/mouse state and reports transitions."""

    def __init__(self, on_event: Callable[[str, str, bool], None], hz: int = 60) -> None:
        self.on_event = on_event
        self.period = 1.0 / max(1, hz)
        self._state: dict[str, bool] = {name: False for name in ALL_KEYS}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- public state, used to seed a freshly connected overlay
    @property
    def pressed(self) -> list[str]:
        return [k for k, v in self._state.items() if v]

    def snapshot(self) -> dict[str, bool]:
        return dict(self._state)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="input-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:  # noqa: BLE001 - a display aid must never crash the server
                pass
            time.sleep(self.period)

    def _poll_once(self) -> None:
        for vk, name in _KEYS.items():
            now = _down(vk)
            if now != self._state[name]:
                self._state[name] = now
                self.on_event("key", name, now)
        for vk, name in _MOUSE.items():
            now = _down(vk)
            if now != self._state[name]:
                self._state[name] = now
                self.on_event("mouse", name, now)
