"""Render the outro: full username, then the real DG mark, tagline, fade to black.

2D like the stingers. Beats are supplied so the two big moments land on kicks:

    t=0.30   DIKGUN snaps in            (on a kick)
    t=2.00   the real mark forms        (on a kick)
    t=2.60   "BACK AND BETTER THAN EVER" fades in
    t=6.80 -> 8.00  fade to black

Changes from the previous version, all from review notes:

* NO SHAKE, NO PULSE. The letters used to jitter back and forth and shake on
  impact; both are gone. The name simply holds, then pulls together.
* THE REAL MARK, NOT TYPEFACE LETTERS. During the pull-together the typeface D and
  G fade out while the actual logo's D and G halves slide in from slightly apart
  and converge. The halves come from make_mark.py, which mattes the brand avatar
  (the SVG is three polygons with no holes handling, which produced the seams and
  artefacts in the middle of the mark).
* Split halves are full-frame-sized images with alpha masks, so both are pasted at
  the same origin minus the convergence offset and stay pixel-aligned.

Usage:
    python render_outro.py --out outro_frames --dur 8.0 --beats 0.30,2.00
"""

from __future__ import annotations

import argparse
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

W, H = 1920, 1080
FPS = 60

INK = (22, 20, 18)
CREAM = (236, 228, 210)
RED = (200, 22, 29)

NAME = "DIKGUN"
TAGLINE = "BACK AND BETTER THAN EVER"

FONT_HEAVY = r"C:\Windows\Fonts\ariblk.ttf"
FONT_BOLD = r"C:\Windows\Fonts\arialbd.ttf"

NAME_SIZE = 232
NAME_TRACK = 28
TAG_SIZE = 52
TAG_TRACK = 17

MARK_LEFT = os.path.join(HERE, "mark_left.png")
MARK_RIGHT = os.path.join(HERE, "mark_right.png")
MARK_W = 640.0          # drawn width of the finished mark, in px
MARK_Y_LIFT = 0.045     # fraction of H, lifted so the tagline has room


def background() -> Image.Image:
    img = Image.new("RGB", (W, H), INK)
    glow = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(glow)
    cx, cy = W * 0.5, H * 0.5
    for r in range(1150, 0, -14):
        d.ellipse([cx - r, cy - r * 0.62, cx + r, cy + r * 0.62], fill=int(28 * (1 - r / 1150.0)))
    glow = glow.filter(ImageFilter.GaussianBlur(95))
    return Image.composite(Image.new("RGB", (W, H), (32, 28, 26)), img, glow)


def name_layout():
    f = ImageFont.truetype(FONT_HEAVY, NAME_SIZE)
    adv = [f.getlength(c) for c in NAME]
    total = sum(adv) + NAME_TRACK * (len(NAME) - 1)
    x = (W - total) / 2.0
    xs = []
    for a in adv:
        xs.append(x)
        x += a + NAME_TRACK
    asc, desc = f.getmetrics()
    baseline = int(H / 2 + (asc - desc) / 2) + int(NAME_SIZE * 0.20)
    return xs, baseline, total


def draw_tagline(layer, alpha: float, lift: float = 0.0):
    f = ImageFont.truetype(FONT_BOLD, TAG_SIZE)
    total = sum(f.getlength(c) for c in TAGLINE) + TAG_TRACK * (len(TAGLINE) - 1)
    x = (W - total) / 2.0
    y = int(H * 0.690 + lift)
    tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    for ch in TAGLINE:
        d.text((x, y), ch, font=f, fill=CREAM + (255,), anchor="ls")
        x += f.getlength(ch) + TAG_TRACK
    if alpha < 0.999:
        tile.putalpha(tile.split()[3].point(lambda v: int(v * alpha)))
    layer.alpha_composite(tile)


_MARK_CACHE: dict = {}


def _mark_layers():
    if "l" not in _MARK_CACHE:
        _MARK_CACHE["l"] = Image.open(MARK_LEFT).convert("RGBA")
        _MARK_CACHE["r"] = Image.open(MARK_RIGHT).convert("RGBA")
    return _MARK_CACHE["l"], _MARK_CACHE["r"]


def draw_mark(layer, scale: float = 1.0, alpha: float = 1.0, spread: float = 0.0):
    """Draw the real mark. `spread` separates the D and G halves in px."""
    left, right = _mark_layers()
    mw = int(MARK_W * scale)
    mh = int(mw * left.height / float(left.width))
    ox = int((W - mw) / 2.0)
    oy = int((H - mh) / 2.0 - H * MARK_Y_LIFT)

    for img, sign in ((left, -1.0), (right, 1.0)):
        tile = img.resize((mw, mh), Image.LANCZOS)
        if alpha < 0.999:
            tile = tile.copy()
            tile.putalpha(tile.split()[3].point(lambda v: int(v * alpha)))
        layer.alpha_composite(tile, (ox + int(sign * spread), oy))


def ease_out_back(p: float, s: float = 1.7) -> float:
    p = max(0.0, min(1.0, p))
    return 1 + (s + 1) * (p - 1) ** 3 + s * (p - 1) ** 2


def ease_in_cubic(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return p ** 3


def render(dur: float, name_t: float, logo_t: float, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.endswith(".png"):
            os.remove(os.path.join(out_dir, f))

    frames = int(round(dur * FPS))
    xs, baseline, total = name_layout()
    fh = ImageFont.truetype(FONT_HEAVY, NAME_SIZE)
    widths = [fh.getlength(c) for c in NAME]

    collide_start = logo_t - 0.30
    SPREAD = 58.0                  # how far apart the two halves start, px
    tag_t = logo_t + 0.60
    fade_start = dur - 1.20

    bg = background()

    for fr in range(frames):
        t = fr / float(FPS)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        if t < name_t:
            pass
        elif t < collide_start:
            # the name holds, perfectly still
            p = min(1.0, (t - name_t) / 0.16)
            sc = 0.62 + 0.38 * ease_out_back(p)
            tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dr = ImageDraw.Draw(tile)
            for i, ch in enumerate(NAME):
                col = CREAM if i < 3 else RED
                dr.text((xs[i], baseline), ch, font=fh, fill=col + (255,), anchor="ls")
            if sc < 0.999:
                nw, nh = int(W * sc), int(H * sc)
                tile = tile.resize((nw, nh), Image.LANCZOS)
                layer.alpha_composite(tile, (int((W - nw) / 2), int((H - nh) / 2)))
            else:
                layer.alpha_composite(tile)
        else:
            p = min(1.0, (t - collide_start) / max(0.05, logo_t - collide_start))

            # I K U N shrink away
            others = max(0.0, 1.0 - p * 1.9)
            if others > 0.02:
                tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                for i in (1, 2, 4, 5):
                    tw = int(widths[i] + 80)
                    th = int(NAME_SIZE * 1.8)
                    sub = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
                    ImageDraw.Draw(sub).text(
                        (40, th - int(NAME_SIZE * 0.25)), NAME[i], font=fh,
                        fill=(CREAM if i < 3 else RED) + (255,), anchor="ls")
                    sh = 1.0 - 0.5 * p
                    sub = sub.resize((max(1, int(tw * sh)), max(1, int(th * sh))), Image.LANCZOS)
                    sub.putalpha(sub.split()[3].point(lambda v: int(v * others)))
                    layer.alpha_composite(sub, (int(xs[i] - 40), int(baseline - th + NAME_SIZE * 0.25)))
                layer.alpha_composite(tile)

            # the typeface D and G bow out as the real halves arrive
            typed = max(0.0, 1.0 - p * 1.7)
            if typed > 0.02:
                tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                dr = ImageDraw.Draw(tile)
                dr.text((xs[0], baseline), NAME[0], font=fh, fill=CREAM + (255,), anchor="ls")
                dr.text((xs[3], baseline), NAME[3], font=fh, fill=RED + (255,), anchor="ls")
                tile.putalpha(tile.split()[3].point(lambda v: int(v * typed)))
                layer.alpha_composite(tile)

            # the real mark: halves converge
            mark_in = min(1.0, p * 1.45)
            spread = SPREAD * (1.0 - ease_in_cubic(p))
            msc = 1.10 - 0.10 * ease_in_cubic(p)
            draw_mark(layer, scale=msc, alpha=mark_in, spread=spread)

            if t >= logo_t:
                # settle to final size
                q = (t - logo_t) / 0.26
                draw_mark(layer, scale=1.10 - 0.10 * min(1.0, q))

        flash = 0.0
        if t >= logo_t:
            q = t - logo_t
            if q < 0.34:
                flash = 0.80 * (1 - q / 0.34) ** 2

        if t >= tag_t:
            ap = min(1.0, (t - tag_t) / 0.45)
            draw_tagline(layer, ap, lift=(1 - ap) * 18)

        out = bg.copy().convert("RGBA")
        out.alpha_composite(layer)
        if flash > 0:
            out = Image.alpha_composite(
                out, Image.new("RGBA", (W, H), (255, 246, 232, int(255 * flash))))

        if t >= fade_start:
            f = min(1.0, (t - fade_start) / max(0.01, dur - fade_start))
            out = Image.alpha_composite(
                out, Image.new("RGBA", (W, H), (0, 0, 0, int(255 * (f ** 1.6)))))

        out.convert("RGB").save(os.path.join(out_dir, f"f_{fr + 1:04d}.png"))

    print(f"[outro] {frames} frames -> {out_dir}  (name {name_t}s, mark {logo_t}s)")
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="outro_frames")
    ap.add_argument("--dur", type=float, default=8.0)
    ap.add_argument("--beats", default="0.30,2.00", help="name time,mark time")
    a = ap.parse_args()
    bt = [float(v) for v in a.beats.split(",")]
    out = a.out if os.path.isabs(a.out) else os.path.join(HERE, a.out)
    render(a.dur, bt[0], bt[1], out)


if __name__ == "__main__":
    main()
