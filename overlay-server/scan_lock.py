"""Small helpers shared by the scan tools.

Two scans running at once used to clobber each other's index: both did
load-modify-write on the same JSON, so whichever finished last silently dropped
the other's entries. That is how 4 R6 files and 5 kill windows disappeared once
already.

`acquire()` takes an exclusive lock file so a second scan refuses to start, and
`atomic_write_json()` stops a half-written file from being left behind if the
process dies mid-save.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# a lock older than this is assumed to be from a killed process
STALE_AFTER = 6 * 60 * 60


def acquire(directory: Path, name: str) -> bool:
    """Take a per-tool lock. Returns False if another scan holds it."""
    lock = Path(directory) / f".{name}.lock"
    if lock.exists():
        try:
            age = time.time() - lock.stat().st_mtime
            pid = int(lock.read_text(encoding="utf-8").strip() or 0)
        except Exception:  # noqa: BLE001
            age, pid = STALE_AFTER + 1, 0
        if age < STALE_AFTER and _alive(pid):
            print(f"another {name} scan is already running (pid {pid}); refusing to start")
            return False
        try:
            lock.unlink()
        except OSError:
            pass
    try:
        with open(lock, "x", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return True
    except FileExistsError:
        print(f"another {name} scan grabbed the lock first; refusing to start")
        return False


def release(directory: Path, name: str) -> None:
    try:
        (Path(directory) / f".{name}.lock").unlink()
    except OSError:
        pass


def _alive(pid: int) -> bool:
    """True if the process still exists.

    os.kill(pid, 0) does not work as a liveness probe on Windows -- it raises
    for processes that are perfectly alive, and this function used to treat any
    exception as "alive", so a lock left by a killed scan blocked every later
    scan until someone deleted the file by hand.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def atomic_write_json(path: Path, data) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)
