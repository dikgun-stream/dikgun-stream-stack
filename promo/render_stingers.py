"""Render the DIKGUN stinger cards as flat 2D frames (no 3D).

Replaces the Blender approach. The brief simplified to: show the accumulated word,
adding one letter per stage, with the letters lined up and steady.

Output layout is unchanged (stingers/<word>/f_0001.png), so promo_build.py needs
no changes: it encodes these PNGs, adds a silent audio track, and splices them
into the cut.

Design decisions, all to kill the alignment problems the 3D version had:

* FLAT 2D. No camera, no perspective, no ground plane - so nothing can be clipped
  by a surface or foreshortened out of alignment.
* LEFT-ALIGNED AND CONSTANT SIZE. Earlier letters never move when a new one
  appears; the word simply grows to the right. Centring the group each stage would
  shift every previous letter by half a glyph, which reads as misalignment when
  the clips are intercut.
* SHARED BASELINE. Every glyph is drawn from the same baseline with the same font
  size, so the word aligns by construction rather than by tuning.
* Brand colours: DIK in cream, GUN in red, as requested for the end card.

Usage:
    python render_stingers.py                 # all six stages
    python render_stingers.py --stage 3       # just DIK
"""

from __future__ import annotations

import argparse
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(HERE, "stingers")

W, H = 1920, 1080
FPS = 60
FRAMES = 42              # 0.70s - about one beat at 120bpm

LETTERS = "DIKGUN"
INK = (22, 20, 18)
CREAM = (236, 228, 210)
RED = (200, 22, 29)

FONT_PATH = r"C:\Windows\Fonts\ariblk.ttf"
SIZE = 300               # constant across stages so letters never change weight
TRACK = int(SIZE * 0.16)   # glyphs were touching at 0.06
MARGIN_X = 170
POP_FRAMES = 6           # frames for the newest letter to snap in
SHAKE_FRAMES = 5


def font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, SIZE)


def colour_for(i: int) -> tuple[int, int, int]:
    return CREAM if i < 3 else RED


def background() -> Image.Image:
    """Ink backdrop with a soft radial lift, so the flat card is not dead grey."""
    img = Image.new("RGB", (W, H), INK)
    glow = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(glow)
    cx, cy = W * 0.5, H * 0.5
    for r in range(1100, 0, -14):
        v = int(30 * (1 - r / 1100.0))
        d.ellipse([cx - r, cy - r * 0.62, cx + r, cy + r * 0.62], fill=v)
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img = Image.composite(Image.new("RGB", (W, H), (34, 30, 28)), img, glow)
    return img


def ease_out_back(p: float, s: float = 1.9) -> float:
    p = max(0.0, min(1.0, p))
    return 1 + (s + 1) * (p - 1) ** 3 + s * (p - 1) ** 2


def layout(word: str) -> tuple[list[float], int, int]:
    """Left-aligned x per glyph, plus baseline y and cap height."""
    f = font()
    adv = [f.getlength(c) for c in word]
    x = MARGIN_X
    xs = []
    for a in adv:
        xs.append(x)
        x += a + TRACK
    asc, desc = f.getmetrics()
    baseline = int(H / 2 + (asc - desc) / 2) - int(SIZE * 0.10)
    return xs, baseline, asc


def draw_glyph(layer: Image.Image, ch: str, x: float, baseline: int,
               colour: tuple[int, int, int], scale: float = 1.0,
               alpha: float = 1.0) -> None:
    f = font()
    if scale == 1.0 and alpha >= 0.999:
        ImageDraw.Draw(layer).text((x, baseline), ch, font=f, fill=colour, anchor="ls")
        return

    tw = int(f.getlength(ch) + 40)
    th = int(SIZE * 1.7)
    tile = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((20, th - int(SIZE * 0.28)), ch, font=f,
                              fill=colour + (255,), anchor="ls")
    if scale != 1.0:
        nw, nh = max(1, int(tw * scale)), max(1, int(th * scale))
        tile = tile.resize((nw, nh), Image.LANCZOS)
        x -= (nw - tw) / 2.0
        baseline_off = th - nh
    else:
        baseline_off = 0
    if alpha < 0.999:
        a = tile.split()[3].point(lambda v: int(v * alpha))
        tile.putalpha(a)
    layer.alpha_composite(tile, (int(x) - 20, int(baseline) - th + int(SIZE * 0.28) + baseline_off))


def render_stage(n: int) -> str:
    word = LETTERS[:n]
    xs, baseline, _ = layout(word)
    name = word
    path = os.path.join(OUT_ROOT, name)
    os.makedirs(path, exist_ok=True)
    for f in os.listdir(path):
        if f.endswith(".png"):
            os.remove(os.path.join(path, f))

    bg = background()
    rng = [0.0, 0.62, -0.48, 0.30, -0.18, 0.09]
    for frame in range(1, FRAMES + 1):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        # letters already landed
        for i in range(n - 1):
            draw_glyph(layer, word[i], xs[i], baseline, colour_for(i))

        # newest letter snaps in
        p = min(1.0, (frame - 1) / float(POP_FRAMES))
        scale = 0.55 + 0.45 * ease_out_back(p)
        alpha = min(1.0, p * 2.2)
        i = n - 1

        # impact glow behind the new letter, only while it is landing
        if 0.15 < p < 1.0:
            g = (p - 0.15) / 0.85
            rad = int(SIZE * (0.9 - 0.5 * g))
            glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow)
            cxg = int(xs[i] + font().getlength(word[i]) / 2)
            gd.ellipse([cxg - rad, baseline - rad * 1.5, cxg + rad, baseline + rad * 0.6],
                       fill=(*RED, int(120 * (1 - g))))
            glow = glow.filter(ImageFilter.GaussianBlur(60))
            layer.alpha_composite(glow)

        draw_glyph(layer, word[i], xs[i], baseline, colour_for(i), scale, alpha)

        out = bg.copy().convert("RGBA")
        # hard impact shake, decaying
        if POP_FRAMES <= frame < POP_FRAMES + SHAKE_FRAMES:
            k = frame - POP_FRAMES
            amp = 16 * (1 - k / float(SHAKE_FRAMES))
            out.alpha_composite(layer, (int(rng[k % len(rng)] * amp), int(rng[::-1][k % len(rng)] * amp * 0.6)))
        else:
            out.alpha_composite(layer)

        out.convert("RGB").save(os.path.join(path, f"f_{frame:04d}.png"))

    print(f"[cards] stage {n} '{name}' -> {path}  ({FRAMES} frames)")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    global OUT_ROOT
    if args.out:
        OUT_ROOT = args.out if os.path.isabs(args.out) else os.path.join(HERE, args.out)
    os.makedirs(OUT_ROOT, exist_ok=True)
    stages = [args.stage] if args.stage else list(range(1, len(LETTERS) + 1))
    for n in stages:
        render_stage(n)
    print("[cards] done")


if __name__ == "__main__":
    main()
