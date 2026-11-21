"""Render the outro: full username, collide to the DG mark, tagline, fade to black.

2D like the stingers - no 3D, no camera. Beats are supplied so the two big moments
land on kicks:

    t=0.30   DIKGUN snaps in            (on a kick)
    t=2.00   D and G slide together     (on a kick); I K U N vanish
    t=2.18   impact -> the DG mark, flash and shake
    t=2.60   "BACK AND BETTER THAN EVER" fades in
    t=6.80-> 8.00  fade to black

The mark is the real brand artwork: brand/dg-mark.svg is three <polygon>
elements, so it is drawn directly with ImageDraw.polygon rather than rasterised
from an SVG, which would need a renderer Blender cannot supply and PIL does not
have.

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

# brand/dg-mark.svg, viewBox 0 0 1188 627
MARK_BOX = (1188.0, 627.0)
MARK_POLYS = [
    "12,236 9,244 10,624 396,624 445,612 488,596 534,571 573,541 522,461 503,413 "
    "492,370 486,372 470,400 439,429 405,446 381,453 178,454 171,449 170,380 "
    "173,375 319,252 329,240 327,234 16,233",
    "571,86 568,79 543,60 484,28 426,9 386,3 11,3 0,8 1,18 99,168 393,170 422,180 "
    "449,198 471,220 489,250 495,243 510,188 527,150 547,116",
    "1186,3 1180,0 812,2 752,15 718,28 675,50 630,83 596,119 566,162 541,217 "
    "527,287 530,361 548,423 571,469 613,524 660,563 718,596 780,615 850,624 "
    "1159,624 1172,619 1178,605 1174,238 1167,231 864,233 859,236 861,244 "
    "874,252 1011,373 1014,381 1016,449 1008,457 821,464 791,459 768,451 "
    "731,425 701,390 684,345 682,297 691,259 716,216 744,190 784,170 1087,168 1186,13",
]


def parse_polys():
    out = []
    for s in MARK_POLYS:
        # SVG polygon points are "x,y x,y ..." - commas AND spaces separate
        nums = [float(v) for v in s.replace(",", " ").split()]
        out.append([(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)])
    return out


POLYS = parse_polys()


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
    adv = [f.getlength(c) for c in TAGLINE] if False else None
    total = sum(f.getlength(c) for c in TAGLINE) + TAG_TRACK * (len(TAGLINE) - 1)
    x = (W - total) / 2.0
    y = int(H * 0.685 + lift)
    tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    for ch in TAGLINE:
        d.text((x, y), ch, font=f, fill=CREAM + (255,), anchor="ls")
        x += f.getlength(ch) + TAG_TRACK
    if alpha < 0.999:
        a = tile.split()[3].point(lambda v: int(v * alpha))
        tile.putalpha(a)
    layer.alpha_composite(tile)


def draw_mark(layer, scale: float, alpha: float = 1.0):
    """The DG monogram, centred, drawn from the brand SVG polygons."""
    mw = 620.0 * scale
    mh = mw * (MARK_BOX[1] / MARK_BOX[0])
    ox = (W - mw) / 2.0
    oy = (H - mh) / 2.0 - H * 0.045
    k = mw / MARK_BOX[0]
    tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    # The brand mark is D in ink and G in red (see brand/avatars). On a dark
    # frame an ink D would vanish, so the D is inverted to cream - the red G is
    # kept, which also matches the DIK-cream / GUN-red rule used in the cards.
    # Polygons 0 and 1 are the D (x 0..573); polygon 2 is the G (x 527..1186).
    for i, poly in enumerate(POLYS):
        pts = [(ox + px * k, oy + py * k) for px, py in poly]
        d.polygon(pts, fill=(RED if i == 2 else CREAM) + (255,))
    if alpha < 0.999:
        a = tile.split()[3].point(lambda v: int(v * alpha))
        tile.putalpha(a)
    layer.alpha_composite(tile)


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

    collide_start = logo_t - 0.22      # slide begins shortly before impact
    slide_span = 1.15                  # how far D and G travel, in px at full
    tag_t = logo_t + 0.60
    fade_start = dur - 1.20

    # meeting point for D (idx 0) and G (idx 3): the front of the card
    centre_x = W / 2.0
    d_left = xs[0]
    g_left = xs[3]
    d_target = centre_x - widths[0] / 2.0
    g_target = centre_x - widths[3] / 2.0

    bg = background()
    rng = [0.0, 0.7, -0.55, 0.35, -0.2, 0.1]

    for fr in range(frames):
        t = fr / float(FPS)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        shake = (0, 0)

        if t < logo_t - 0.001 or True:
            pass

        if t < name_t:
            # brief void before the name lands
            pass
        elif t < collide_start:
            # full name, snapped in
            p = (t - name_t) / 0.16
            sc = 0.6 + 0.4 * ease_out_back(min(1.0, p))
            # pulse with the ongoing drop
            sc *= 1.0 + 0.012 * math.sin(t * 14.0)
            tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            for i, ch in enumerate(NAME):
                col = CREAM if i < 3 else RED
                ImageDraw.Draw(tile).text((xs[i], baseline), ch, font=fh, fill=col + (255,), anchor="ls")
            if sc != 1.0:
                nw, nh = int(W * sc), int(H * sc)
                tile = tile.resize((nw, nh), Image.LANCZOS)
                layer.alpha_composite(tile, (int((W - nw) / -2), int((H - nh) / -2)))
            else:
                layer.alpha_composite(tile)
        else:
            # the merge
            p = min(1.0, (t - collide_start) / max(0.05, logo_t - collide_start))
            e = ease_in_cubic(p)
            dx = (d_target - d_left) * e
            gx = (g_target - g_left) * e
            others_alpha = max(0.0, 1.0 - p * 1.9)

            tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dr = ImageDraw.Draw(tile)
            # the letters that vanish
            if others_alpha > 0.02:
                for i in (1, 2, 4, 5):
                    ch = NAME[i]
                    shrink = 1.0 - 0.55 * p
                    tw = int(widths[i] + 80)
                    th = int(NAME_SIZE * 1.8)
                    sub = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
                    ImageDraw.Draw(sub).text((40, th - int(NAME_SIZE * 0.25)), ch, font=fh,
                                             fill=(CREAM if i < 3 else RED) + (255,), anchor="ls")
                    sub = sub.resize((max(1, int(tw * shrink)), max(1, int(th * shrink))), Image.LANCZOS)
                    a = sub.split()[3].point(lambda v: int(v * others_alpha))
                    sub.putalpha(a)
                    layer.alpha_composite(sub, (int(xs[i] - 40), int(baseline - th + NAME_SIZE * 0.25)))
            # D and G converge
            dr.text((d_left + dx, baseline), NAME[0], font=fh, fill=CREAM + (255,), anchor="ls")
            dr.text((g_left + gx, baseline), NAME[3], font=fh, fill=RED + (255,), anchor="ls")
            layer.alpha_composite(tile)

            if t >= logo_t:
                # impact: the mark replaces the letters
                q = (t - logo_t)
                msc = 1.14 - 0.14 * min(1.0, q / 0.30)
                draw_mark(layer, msc)
                k = int(q * FPS)
                if k < 6:
                    amp = 18 * (1 - k / 6.0)
                    shake = (int(rng[k % len(rng)] * amp), int(rng[::-1][k % len(rng)] * amp * 0.6))

        # flash on the impact
        flash = 0.0
        if t >= logo_t:
            q = t - logo_t
            if q < 0.34:
                flash = 0.85 * (1 - q / 0.34) ** 2

        if t >= tag_t:
            ap = min(1.0, (t - tag_t) / 0.45)
            draw_tagline(layer, ap, lift=(1 - ap) * 18)

        out = bg.copy().convert("RGBA")
        out.alpha_composite(layer, shake)
        if flash > 0:
            ov = Image.new("RGBA", (W, H), (255, 246, 232, int(255 * flash)))
            out = Image.alpha_composite(out, ov)

        if t >= fade_start:
            f = min(1.0, (t - fade_start) / max(0.01, dur - fade_start))
            black = Image.new("RGBA", (W, H), (0, 0, 0, int(255 * (f ** 1.6))))
            out = Image.alpha_composite(out, black)

        out.convert("RGB").save(os.path.join(out_dir, f"f_{fr + 1:04d}.png"))

    print(f"[outro] {frames} frames -> {out_dir}  (name {name_t}s, logo {logo_t}s)")
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="outro_frames")
    ap.add_argument("--dur", type=float, default=8.0)
    ap.add_argument("--beats", default="0.30,2.00",
                    help="name time,logo time in seconds")
    a = ap.parse_args()
    bt = [float(v) for v in a.beats.split(",")]
    out = a.out if os.path.isabs(a.out) else os.path.join(HERE, a.out)
    render(a.dur, bt[0], bt[1], out)


if __name__ == "__main__":
    main()
