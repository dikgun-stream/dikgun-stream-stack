"""
Deadlock HUD reader.

Everything here works on a frame that OBS ALREADY CAPTURED for the broadcast.
No memory access, no DLL injection, no process inspection, no DirectX hooking --
just pixels from the same source the stream is made of. That keeps it a display
tool rather than anything a game or anti-cheat would treat as a cheat.

Two readers:

  read_scoreboard()  press Tab -> the panel shows K/D/A under each player's
                     name. We find the player's own name, then read the three
                     numbers beneath it.

  find_kill_badge()  a red circular "KILL" badge pops on screen after a kill.
                     Template match it, with a cooldown so one popup cannot be
                     counted repeatedly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps
import pytesseract

_TESS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.path.exists(_TESS):
    pytesseract.pytesseract.tesseract_cmd = _TESS

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "streamer-mode", "assets")
KILL_TEMPLATE = os.path.join(ASSETS, "tpl_kill.png")

# --- scoreboard geometry, calibrated at 1920x1080 -------------------------
# The panel draws each player as a COLUMN: name, hero, then a "K D A" header
# and the three totals beneath. These offsets were measured from a real frame.
NAME_BAND = (300, 118, 1620, 162)      # x0, y0, x1, y1
KDA_TOP, KDA_BOTTOM = 168, 208
KDA_HALF_WIDTH = 44                    # half-width of the three-number row


@dataclass
class KDA:
    kills: int | None
    deaths: int | None
    assists: int | None
    raw: str = ""

    @property
    def complete(self) -> bool:
        return None not in (self.kills, self.deaths, self.assists)


def _prep(img: Image.Image, scale: int = 6) -> Image.Image:
    g = img.convert("L")
    g = g.resize((g.width * scale, g.height * scale), Image.LANCZOS)
    return ImageOps.autocontrast(g)


def find_player_column(frame: Image.Image, player_name: str) -> int | None:
    """Return the x centre of the player's scoreboard column, or None."""
    if not player_name:
        return None
    x0, y0, x1, y1 = NAME_BAND
    band = frame.crop((x0, y0, x1, y1))
    scale = 3
    data = pytesseract.image_to_data(
        _prep(band, scale), config="--psm 6",
        output_type=pytesseract.Output.DICT,
    )
    needle = player_name.strip().lower()
    best = None
    for i, text in enumerate(data["text"]):
        t = (text or "").strip().lower()
        if not t or len(t) < 3:
            continue
        # tolerate OCR noise: accept a prefix match or a close-enough length
        if t in needle or needle.startswith(t) or _similar(t, needle):
            width = data["width"][i] // scale
            if width < 20:          # a stray glyph, not a name
                continue
            left = x0 + data["left"][i] // scale
            best = left + width // 2
            break
    return best


def _similar(a: str, b: str) -> bool:
    """Cheap similarity: same length+-1 and 70% shared characters in order."""
    if abs(len(a) - len(b)) > 2:
        return False
    common = sum(1 for c in a if c in b)
    return common >= max(3, int(len(b) * 0.7))


def read_kda(frame: Image.Image, column_x: int) -> KDA:
    """Read the three numbers under the player's column.

    Primary method is character clustering: it handled 4 of 5 real frames. The
    numbers are separated by slashes ("1 / 1 / 1"), and a digit-only whitelist
    drops them, which occasionally collapses the row into a single token -- so
    when clustering cannot find three groups we fall back to reading the text
    normally and pulling three numbers out of it.
    """
    import re

    # The row's vertical position shifts slightly between scoreboard variants,
    # so try a few bands before giving up.
    bands = ((KDA_TOP, KDA_BOTTOM), (KDA_TOP - 6, KDA_BOTTOM - 6),
             (KDA_TOP + 6, KDA_BOTTOM + 6), (KDA_TOP - 12, KDA_BOTTOM - 12),
             (KDA_TOP + 12, KDA_BOTTOM + 12))
    for top, bottom in bands:
        row = frame.crop((column_x - KDA_HALF_WIDTH, top,
                          column_x + KDA_HALF_WIDTH, bottom))
        scale = 6
        big = _prep(row, scale)

        # --- primary: cluster digit character boxes by horizontal gaps
        boxes = pytesseract.image_to_boxes(
            big, config="--psm 7 -c tessedit_char_whitelist=0123456789")
        chars: list[tuple[str, int, int]] = []
        for line in boxes.splitlines():
            p = line.split()
            if len(p) >= 5:
                chars.append((p[0], int(p[1]), int(p[3])))   # char, left, right
        chars.sort(key=lambda c: c[1])

        if chars:
            groups: list[list[tuple[str, int, int]]] = [[chars[0]]]
            for prev, nxt in zip(chars, chars[1:]):
                if (nxt[1] - prev[2]) > 18 * scale // 6:
                    groups.append([nxt])
                else:
                    groups[-1].append(nxt)
            values = ["".join(c[0] for c in g) for g in groups]
            if len(values) == 3:
                try:
                    nums = [int(v) for v in values]
                    return KDA(nums[0], nums[1], nums[2], " ".join(values))
                except ValueError:
                    pass

        # --- fallback: read the text as-is and extract three numbers
        for cfg in ("--psm 7", "--psm 6"):
            txt = pytesseract.image_to_string(big, config=cfg).strip()
            nums = re.findall(r"\d+", txt)
            if len(nums) >= 3:
                return KDA(int(nums[0]), int(nums[1]), int(nums[2]), " ".join(nums[:3]))

    return KDA(None, None, None, "")


def read_scoreboard(frame_path: str, player_name: str) -> KDA | None:
    try:
        frame = Image.open(frame_path).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    col = find_player_column(frame, player_name)
    if col is None:
        return None
    return read_kda(frame, col)


# --------------------------------------------------------------- kill badge
def _load_kill_template() -> np.ndarray | None:
    if not os.path.exists(KILL_TEMPLATE):
        return None
    tpl = cv2.imread(KILL_TEMPLATE)
    return tpl if tpl is not None and tpl.size else None


_KILL_TPL = None
ASSIST_TEMPLATE = os.path.join(ASSETS, "tpl_assist.png")
_ASSIST_TPL = None
KILL_THRESHOLD = 0.72
# The two badges sit in the same screen slot, so the templates cross-match at
# ~0.46 while the correct one hits 1.00 -- 0.72 splits them cleanly.
SEARCH_BAND = (0.25, 0.15, 0.75, 0.45)


def _match(tpl, region) -> float:
    if tpl is None or region.size == 0:
        return 0.0
    if tpl.shape[0] > region.shape[0] or tpl.shape[1] > region.shape[1]:
        return 0.0
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    return float(res.max())


def find_badge(frame_path: str) -> tuple[str | None, float]:
    """Return ('kill'|'assist'|None, best score) for the centre-screen badge."""
    global _KILL_TPL, _ASSIST_TPL
    if _KILL_TPL is None:
        _KILL_TPL = _load_kill_template()
    if _ASSIST_TPL is None:
        _ASSIST_TPL = _load(ASSIST_TEMPLATE)

    img = cv2.imread(frame_path)
    if img is None:
        return None, 0.0
    h, w = img.shape[:2]
    x0, y0, x1, y1 = SEARCH_BAND
    region = img[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]

    k = _match(_KILL_TPL, region)
    a = _match(_ASSIST_TPL, region)
    if k >= KILL_THRESHOLD and k >= a:
        return "kill", k
    if a >= KILL_THRESHOLD:
        return "assist", a
    return None, max(k, a)


def _load(path: str):
    if not os.path.exists(path):
        return None
    tpl = cv2.imread(path)
    return tpl if tpl is not None and tpl.size else None


def find_kill_badge(frame_path: str) -> float:
    """Return the best template-match score for the KILL badge (0 if no frame)."""
    global _KILL_TPL
    if _KILL_TPL is None:
        _KILL_TPL = _load_kill_template()
        if _KILL_TPL is None:
            return 0.0
    img = cv2.imread(frame_path)
    if img is None:
        return 0.0
    h, w = img.shape[:2]
    x0, y0, x1, y1 = SEARCH_BAND
    region = img[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
    return _match(_KILL_TPL, region)


# ------------------------------------------------------------- item grid
# Calibrated against two known purchases (Monster Rounds + Extra Stamina):
# both then rank #1 at 0.93 / 0.92. The grid fills DOWN a column before moving
# right, so item index i sits at column i//2, row i%2.
ITEM_ORIGIN = (26, 946)
ITEM_COL_W = 62      # measured from detected card boxes (was 65, which drifted
ITEM_ROW_H = 62      # a few px per column and broke every column but the first)
ITEM_SIZE = 54
ITEM_SLOTS = 12
ITEM_MATCH_MIN = 0.82      # true picks score ~0.92-0.95; 0.80 let noise through
ITEM_MATCH_MARGIN = 0.05   # best must also clearly beat the runner-up
ITEM_FILLED_STD = 25.0     # above this a slot holds art; empty slots sit under 15
UNKNOWN_ITEM = "Unknown"
ITEM_SIG = 32

_ITEM_LIB: tuple[np.ndarray, np.ndarray] | None = None
_ITEM_FILES: dict[str, str] = {}


def _item_signature(img) -> np.ndarray | None:
    if img is None or img.size == 0:
        return None
    g = cv2.cvtColor(cv2.resize(img, (ITEM_SIG, ITEM_SIG),
                                interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    g = g.astype(np.float32)
    g -= g.mean()
    n = np.linalg.norm(g)
    return (g / (n if n > 1e-6 else 1)).ravel()


def _load_item_library() -> tuple[np.ndarray, np.ndarray] | None:
    """Build a normalised signature matrix from the downloaded shop card art."""
    global _ITEM_LIB
    if _ITEM_LIB is not None:
        return _ITEM_LIB
    libdir = os.path.join(ASSETS, "items_shop")
    manifest = os.path.join(libdir, "manifest.json")
    if not os.path.exists(manifest):
        return None
    import json

    with open(manifest, encoding="utf-8") as fh:
        entries = json.load(fh)
    rows, names = [], []
    for entry in entries:
        img = cv2.imread(os.path.join(libdir, entry["file"]), cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        if img.ndim == 3 and img.shape[2] == 4:
            a = img[:, :, 3:4].astype(float) / 255.0
            img = (img[:, :, :3].astype(float) * a + 120 * (1 - a)).astype(np.uint8)
        h, w = img.shape[:2]
        # the HUD shows a centre-cropped square of the portrait card, so match
        # across a few crop levels rather than assuming one
        for frac in (0.6, 0.75, 0.9, 1.0):
            cw = int(min(w, h) * frac)
            if cw <= 0:
                continue
            x0 = (w - cw) // 2
            y0 = max(0, (h - cw) // 2)
            sig = _item_signature(img[y0:y0 + cw, x0:x0 + cw])
            if sig is not None:
                rows.append(sig)
                names.append(entry["name"])

    # Learned templates: crops taken straight from the live HUD for items whose
    # store art correlates badly (the HUD dims and tier-tints the card, which
    # drops correlation to ~0.52 even when the art is identical). These match
    # near 1.0 because they are the same rendering.
    huddir = os.path.join(ASSETS, "items_hud")
    hudman = os.path.join(huddir, "manifest.json")
    if os.path.exists(hudman):
        with open(hudman, encoding="utf-8") as fh:
            for entry in json.load(fh):
                img = cv2.imread(os.path.join(huddir, entry["file"]), cv2.IMREAD_UNCHANGED)
                if img is None:
                    continue
                sig = _item_signature(img)
                if sig is not None:
                    rows.append(sig)
                    names.append(entry["name"])

    if not rows:
        return None
    _ITEM_FILES.clear()
    for entry in entries:
        _ITEM_FILES.setdefault(entry["name"], entry["file"])
    _ITEM_LIB = (np.vstack(rows), np.array(names))
    return _ITEM_LIB


def read_items(frame_path: str) -> list[str] | None:
    """Return the item names in the bottom-left grid, or None if unreadable.

    None matters: the shop screen draws the grid at a different offset, so a
    frame taken while shopping is unreadable rather than empty. Callers must
    treat None as "no new information" and keep the previous reading, otherwise
    opening the shop would wipe the item tower.
    """
    lib = _load_item_library()
    if lib is None:
        return None
    matrix, names = lib
    img = cv2.imread(frame_path)
    if img is None:
        return None

    ox, oy = ITEM_ORIGIN
    found: list[dict] = []
    unmatched_filled = 0
    for idx in range(ITEM_SLOTS):
        col, row = idx // 2, idx % 2
        x = ox + col * ITEM_COL_W
        y = oy + row * ITEM_ROW_H
        patch = img[y:y + ITEM_SIZE, x:x + ITEM_SIZE]
        if patch.shape[0] != ITEM_SIZE or patch.shape[1] != ITEM_SIZE:
            continue
        grey = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        std = float(grey.std())
        sig = _item_signature(patch)
        matched = None
        if sig is not None:
            scores = matrix @ sig
            if scores.size > 1:
                order = np.argsort(scores)[-2:]
                best = int(order[-1])
                runner = float(scores[order[0]])
            else:
                best, runner = int(scores.argmax()), 0.0
            top = float(scores[best])
            if top >= ITEM_MATCH_MIN and (top - runner) >= ITEM_MATCH_MARGIN:
                name = str(names[best])
                matched = {"name": name, "file": _ITEM_FILES.get(name, "")}
        if matched:
            found.append(matched)
        elif std >= ITEM_FILLED_STD:
            # The slot may hold a card we cannot name (the asset library covers
            # only 181 of 250 purchasable items) -- but on sparse grids an empty
            # slot also catches bright game background, so this is provisional.
            found.append({"name": UNKNOWN_ITEM, "file": "", "_maybe": True})

    # Slots cannot be classified as empty by contrast: in gameplay the world
    # shows through them, so they look "filled". Trust the matches instead --
    # at least one confident hit means we are reading a real grid; none means
    # the frame is unreadable (shop screen) and the caller keeps its last value.
    if not found:
        return None
    confident = sum(1 for e in found if not e.get("_maybe"))
    # No confident match at all -> we are not looking at a grid we understand
    # (shop screen, end-game screen). Must be None, not [], or the caller would
    # treat it as "you own nothing" and wipe the tower.
    if confident == 0:
        return None
    # Guessing wrong is worse than showing 10 of 12: a sparse grid producing
    # phantom entries would misrepresent what you own. Only trust placeholders
    # when the grid is dense enough that a stray bright slot is implausible.
    if confident < 8:
        return [e for e in found if not e.get("_maybe")]
    return [{k: v for k, v in e.items() if k != "_maybe"} for e in found]
# The damage readout is not simply present when Tab is held: it has to be
# toggled, and clicking it cycles between INCOMING and OUTGOING for the current
# life. So we read the mode as well as the number and only treat OUTGOING as
# "damage dealt".
DAMAGE_HEADER = (1635, 244, 1822, 288)     # tight box around "INCOMING/OUTGOING DAMAGE"
DAMAGE_VALUE = (1600, 320, 1910, 375)      # "Total Damage: N"
_PREFIX_IN = None
_PREFIX_OUT = None
PREFIX_MARGIN = 0.12
# Whole-header template matching does NOT work here: both strings share
# "...ING DAMAGE" and cross-match at 0.92. Only the leading letters differ, so
# OCR of a tight crop is the primary signal and a prefix match is the check.


def _ocr(img: Image.Image, box, scale: int, cfg: str) -> str:
    c = img.crop(box)
    g = c.convert("L").resize((c.width * scale, c.height * scale), Image.LANCZOS)
    return pytesseract.image_to_string(ImageOps.autocontrast(g), config=cfg).upper().strip()


def _prefix_vote(frame: Image.Image) -> str | None:
    """Template match just the first few letters. Returns a mode only on a clear
    margin, so a near-tie reports 'unknown' instead of guessing."""
    global _PREFIX_IN, _PREFIX_OUT
    if _PREFIX_IN is None:
        _PREFIX_IN = _load(os.path.join(ASSETS, "hdr_prefix_incoming.png"))
    if _PREFIX_OUT is None:
        _PREFIX_OUT = _load(os.path.join(ASSETS, "hdr_prefix_outgoing.png"))
    if _PREFIX_IN is None or _PREFIX_OUT is None:
        return None
    x0, y0, x1, y1 = DAMAGE_HEADER
    region = np.array(frame.crop((x0, y0, x1, y1)).convert("RGB"))
    region = cv2.cvtColor(region, cv2.COLOR_RGB2BGR)

    def score(tpl):
        if tpl.shape[0] > region.shape[0] or tpl.shape[1] > region.shape[1]:
            return 0.0
        return float(cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED).max())

    a, b = score(_PREFIX_IN), score(_PREFIX_OUT)
    if a - b >= PREFIX_MARGIN:
        return "incoming"
    if b - a >= PREFIX_MARGIN:
        return "outgoing"
    return None


def read_damage(frame_path: str) -> tuple[str | None, int | None]:
    """Return (mode, total_damage), mode being 'incoming' | 'outgoing' | None.

    Both words appear in the same place and look similar, so the mode is decided
    by OCR of a tight crop, cross-checked against a prefix template match. If the
    two disagree, or neither is decisive, the answer is None -- a wrong
    incoming/outgoing label would put a false number on stream, which is worse
    than showing nothing.
    """
    try:
        frame = Image.open(frame_path).convert("RGB")
    except Exception:  # noqa: BLE001
        return None, None

    # PROOF OF PRESENCE FIRST: the panel always contains a "Total Damage:" line.
    # Without it we are not looking at the panel, and the header region may hold
    # unrelated HUD text that OCR could mistake for "INCOMING" -- which happened
    # on the end-game screen before this guard existed.
    val = _ocr(frame, DAMAGE_VALUE, 4, "--psm 6")
    import re
    if "DAMAGE" not in val and "T0TAL" not in val and "TOTAL" not in val:
        return None, None
    m = re.search(r"T[O0]TAL\s+DAMAGE\s*:?\s*([0-9O,]+)", val)
    value = None
    if m:
        try:
            value = int(m.group(1).replace(",", "").replace("O", "0"))
        except ValueError:
            value = None

    head = _ocr(frame, DAMAGE_HEADER, 6, "--psm 7")
    has_in = "INCOMING" in head
    has_out = "OUTGOING" in head
    ocr_mode = "incoming" if (has_in and not has_out) else (
        "outgoing" if (has_out and not has_in) else None)

    vote = _prefix_vote(frame)
    if ocr_mode and vote:
        mode = ocr_mode if ocr_mode == vote else None       # disagreement -> unknown
    else:
        mode = ocr_mode or vote

    if mode is None:
        return None, None
    return mode, value
