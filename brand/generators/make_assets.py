#!/usr/bin/env python
"""Render every brand asset from the traced DG geometry.

Nothing here upscales a bitmap. The mark is drawn from polygons in
dg-vector.json at whatever size is needed, so the output is crisp at 32px and at
2048px alike. Re-run this after editing the tagline, the game list or the URLs.

    python make_assets.py --all
    python make_assets.py --avatars
    python make_assets.py --banners
    python make_assets.py --thumbnails

Outputs are written relative to the brand/ directory above this script.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BRAND = HERE.parent

# ---- brand palette, kept in step with the overlay scenes ---------------------
PAPER = (236, 228, 210)   # #ECE4D2
INK = (22, 20, 18)        # #161412
RED = (200, 22, 29)       # #C8161D
MUTED = (74, 69, 58)

F_IMPACT = r"C:\Windows\Fonts\impact.ttf"
F_BOLD = r"C:\Windows\Fonts\arialbd.ttf"
F_CONS = r"C:\Windows\Fonts\consolab.ttf"

URLS = "twitch.tv/dikgun   \u00b7   youtube.com/@dikgun   \u00b7   x.com/dikgunlive"
GAMES_A = "DEADLOCK \u00b7 TARKOV \u00b7 APEX LEGENDS \u00b7 R6 SIEGE"
GAMES_B = "BATTLEFIELD 6 \u00b7 THE FINALS \u00b7 HUNT \u00b7 HELL LET LOOSE"

THUMB_GAMES = [
    "Deadlock", "Escape from Tarkov", "Apex Legends", "Rainbow Six Siege",
    "Battlefield 6", "The Finals", "Hunt: Showdown", "Hell Let Loose",
    "R.E.P.O.", "PUBG", "Variety",
]

AVATAR_SIZES = (1024, 512, 300, 70, 32)


def _vector() -> dict:
    path = HERE / "dg-vector.json"
    if not path.is_file():
        raise SystemExit(f"missing {path} - run trace_mark.py first")
    return json.loads(path.read_text(encoding="utf-8"))


VEC = _vector()
VW, VH = VEC["w"], VEC["h"]


# ----------------------------------------------------------------- primitives
def mark(height_px: int, color=PAPER) -> Image.Image:
    """The DG mark as an RGBA layer, rendered from polygons."""
    scale = height_px / VH
    im = Image.new("RGBA", (int(VW * scale) + 2, int(height_px) + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for poly in VEC["polys"]:
        d.polygon([(x * scale + 1, y * scale + 1) for x, y in poly],
                  fill=color + (255,))
    return im


def split_mark() -> tuple[list, list]:
    """Split the traced shapes into the D and the G by centroid.

    The D is two disconnected shapes because the G's stroke cuts through it.
    This is what makes the duotone avatar possible.
    """
    mid = VW / 2
    left = [p for p in VEC["polys"] if np.mean([q[0] for q in p]) <= mid]
    right = [p for p in VEC["polys"] if np.mean([q[0] for q in p]) > mid]
    return left, right


GRAIN_SEED = 20260101


def paper(size: tuple[int, int], amount: float = 6.0) -> Image.Image:
    """Cream field with a little grain, so large flat areas are not dead flat.

    Seeded: an unseeded RNG made every regeneration differ by a few bytes, which
    defeats the point of committing generated assets.
    """
    w, h = size
    rng = np.random.default_rng(GRAIN_SEED + w * 7919 + h)
    noise = rng.normal(0, amount, (h, w)).astype(np.int16)
    base = np.array(Image.new("RGB", size, PAPER)).astype(np.int16)
    return Image.fromarray(np.clip(base + noise[..., None], 0, 255)
                           .astype(np.uint8)).convert("RGBA")


def fit(draw, text, path, max_w, start_px, min_px=8):
    """Largest font size at which the text fits inside max_w."""
    px = start_px
    while px > min_px:
        f = ImageFont.truetype(path, px)
        if draw.textlength(text, font=f) <= max_w:
            return f, px
        px -= 1
    return ImageFont.truetype(path, min_px), min_px


# -------------------------------------------------------------------- avatars
def build_avatars() -> None:
    out = BRAND / "avatars"
    out.mkdir(parents=True, exist_ok=True)
    left, right = split_mark()

    def render(size, bg, fg, frac=0.72, dual=False):
        c = Image.new("RGBA", (size, size), bg)
        mk_h = int(size * frac * VH / VW)
        if dual:
            for shapes, color in ((left, INK), (right, RED)):
                im = Image.new("RGBA", (int(VW * (mk_h / VH)) + 2, mk_h + 2), (0, 0, 0, 0))
                d = ImageDraw.Draw(im)
                s = mk_h / VH
                for poly in shapes:
                    d.polygon([(x * s + 1, y * s + 1) for x, y in poly], fill=color + (255,))
                c.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
        else:
            im = mark(mk_h, fg)
            c.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
        return c.convert("RGB")

    variants = {
        "01-avatar-cream": lambda s: render(s, PAPER, INK),
        "02-avatar-ink": lambda s: render(s, INK, PAPER),
        "03-avatar-duotone": lambda s: render(s, PAPER, INK, dual=True),
        "04-avatar-white": lambda s: render(s, (255, 255, 255), INK),
    }
    for name, fn in variants.items():
        for size in AVATAR_SIZES:
            fn(size).save(out / f"{name}-{size}.png")
    print(f"  avatars   : {len(variants) * len(AVATAR_SIZES)} files -> {out}")


# -------------------------------------------------------------------- banners
def build_twitch_banner() -> None:
    out = BRAND / "banners"
    out.mkdir(parents=True, exist_ok=True)
    W, H = 1200, 480
    footer = 0.185
    img = paper((W, H))
    d = ImageDraw.Draw(img)
    ftop = int(H * (1 - footer))

    # red wedge, top-right, clear of all type
    d.polygon([(W * 0.80, -H * 0.02), (W * 1.02, -H * 0.02),
               (W * 1.02, H * 0.42), (W * 0.895, H * 0.42)], fill=RED + (255,))
    # ink footer with an angled lead-in
    d.polygon([(0, H), (0, ftop), (W * 0.035, ftop), (W * 0.060, H)], fill=INK + (255,))
    d.rectangle([(W * 0.060, ftop), (W, H)], fill=INK + (255,))
    # red rule above the footer
    d.rectangle([(W * 0.045, ftop - int(H * 0.012)),
                 (W * 0.045 + W * 0.16, ftop - int(H * 0.004))], fill=RED + (255,))

    mk = mark(int(H * 0.38), INK)
    mx = int(W * 0.048)
    img.paste(mk, (mx, int((H * (1 - footer)) * 0.52 - mk.height * 0.5)), mk)

    tx = mx + mk.width + int(W * 0.032)
    avail = W - tx - W * 0.055
    f_big, _ = fit(d, "DIKGUN", F_IMPACT, avail, int(H * 0.30))
    d.text((tx, int(H * 0.10)), "DIKGUN", font=f_big, fill=INK)

    f_g, _ = fit(d, GAMES_A, F_BOLD, avail, int(H * 0.050))
    gy = int(H * 0.10) + f_big.size + int(H * 0.040)
    d.text((tx, gy), GAMES_A, font=f_g, fill=MUTED)
    d.text((tx, gy + f_g.size + int(H * 0.016)), GAMES_B, font=f_g, fill=MUTED)

    f_h, px = fit(d, URLS, F_CONS, W * 0.84, int(H * 0.046))
    d.text((int(W * 0.075), ftop + (H - ftop) // 2 - px // 2 - int(H * 0.004)),
           URLS, font=f_h, fill=PAPER)

    img.convert("RGB").save(out / "twitch-banner-1200x480.png")
    print(f"  banner    : twitch 1200x480 -> {out}")


def build_youtube_banner() -> None:
    """Everything important must live inside the centre 1235x338 safe band.

    That is all a phone or a TV shows. The first attempt here put the footer
    outside it, which looked fine on a desktop and broken on mobile.
    """
    out = BRAND / "banners"
    out.mkdir(parents=True, exist_ok=True)
    W, H = 2048, 1152
    SAFE_W, SAFE_H = 1235, 338
    sx0, sy0 = (W - SAFE_W) // 2, (H - SAFE_H) // 2
    sx1, sy1 = sx0 + SAFE_W, sy0 + SAFE_H

    img = paper((W, H))
    d = ImageDraw.Draw(img)

    # decoration only, and deliberately outside the safe band
    d.polygon([(0, H * 0.02), (W * 0.14, H * 0.02),
               (W * 0.05, H * 0.42), (0, H * 0.42)], fill=RED + (255,))
    d.polygon([(W * 0.86, H * 0.72), (W, H * 0.72),
               (W, H), (W * 0.78, H)], fill=RED + (255,))

    cy = (sy0 + sy1) // 2
    mk_h = int(SAFE_H * 0.60)
    mk = mark(mk_h, INK)
    mx = sx0 + int(SAFE_W * 0.025)
    img.paste(mk, (mx, cy - mk_h // 2), mk)

    tx = mx + mk.width + int(SAFE_W * 0.035)
    avail = sx1 - tx - int(SAFE_W * 0.02)

    f_big, bpx = fit(d, "DIKGUN", F_IMPACT, avail, int(SAFE_H * 0.40))
    f_sub, spx = fit(d, "DEADLOCK \u00b7 TARKOV \u00b7 APEX \u00b7 R6 \u00b7 THE FINALS",
                     F_BOLD, avail, int(SAFE_H * 0.115))
    f_sm, mpx = fit(d, "BATTLEFIELD 6 \u00b7 HUNT \u00b7 HELL LET LOOSE",
                    F_BOLD, avail, int(SAFE_H * 0.095))

    block = bpx + int(SAFE_H * 0.055) + spx + int(SAFE_H * 0.030) + mpx
    ty = cy - block // 2
    d.text((tx, ty), "DIKGUN", font=f_big, fill=INK)
    y2 = ty + bpx + int(SAFE_H * 0.045)
    d.text((tx, y2), "DEADLOCK \u00b7 TARKOV \u00b7 APEX \u00b7 R6 \u00b7 THE FINALS",
           font=f_sub, fill=MUTED)
    d.text((tx, y2 + spx + int(SAFE_H * 0.028)),
           "BATTLEFIELD 6 \u00b7 HUNT \u00b7 HELL LET LOOSE", font=f_sm, fill=MUTED)

    ry = sy1 - int(SAFE_H * 0.055)
    d.rectangle([(mx, ry), (sx1 - int(SAFE_W * 0.02), ry + int(SAFE_H * 0.014))],
                fill=RED + (255,))

    final = img.convert("RGB")
    final.save(out / "youtube-banner-2048x1152.png")

    check = final.copy()
    ImageDraw.Draw(check).rectangle([sx0, sy0, sx1, sy1], outline=(0, 180, 255), width=3)
    check.save(out / "youtube-safearea-check.png")
    final.crop((sx0, sy0, sx1, sy1)).save(out / "youtube-mobile-crop-preview.png")
    print(f"  banner    : youtube 2048x1152 (safe band {SAFE_W}x{SAFE_H}) -> {out}")


# ----------------------------------------------------------------- thumbnails
def _balanced(d, words, font, max_w):
    """Split into two lines with the smallest width difference."""
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        wa, wb = d.textlength(a, font=font), d.textlength(b, font=font)
        if wa <= max_w and wb <= max_w:
            diff = abs(wa - wb)
            if best is None or diff < best[0]:
                best = (diff, [a, b])
    return best[1] if best else None


def _fit_type(d, text, max_w, max_h, start, h):
    """A single line is preferred: wrapping a short name looks broken.

    Two bugs found by eye before this settled: long names overflowed the
    diagonal onto the red, and 'Battlefield 6' orphaned the 6 onto line two.
    """
    px = start
    while px > 24:
        f = ImageFont.truetype(F_IMPACT, px)
        if d.textlength(text.upper(), font=f) <= max_w:
            break
        px -= 2
    if px >= int(h * 0.135):
        return ImageFont.truetype(F_IMPACT, px), px, [text.upper()]

    words = text.upper().split()
    px = start
    while px > 24:
        f = ImageFont.truetype(F_IMPACT, px)
        two = _balanced(d, words, f, max_w)
        if two and int(px * 0.95) * 2 <= max_h:
            return f, px, two
        px -= 2
    return ImageFont.truetype(F_IMPACT, max(px, 24)), max(px, 24), [text.upper()]


def build_thumbnails() -> None:
    out = BRAND / "thumbnails"
    out.mkdir(parents=True, exist_ok=True)

    for game in THUMB_GAMES:
        W, H = 1280, 720
        img = Image.new("RGBA", (W, H), INK)
        d = ImageDraw.Draw(img)
        d.polygon([(0, 0), (W * 0.60, 0), (W * 0.44, H), (0, H)], fill=RED + (255,))
        d.polygon([(W * 0.56, 0), (W, 0), (W, H), (W * 0.40, H)], fill=INK + (255,))
        d.rectangle([(0, H * 0.88), (W, H)], fill=PAPER + (255,))

        mk = mark(int(H * 0.30), PAPER)
        img.paste(mk, (int(W * 0.045), int(H * 0.055)), mk)
        f_w = ImageFont.truetype(F_IMPACT, int(H * 0.085))
        d.text((int(W * 0.048), int(H * 0.075) + mk.height + int(H * 0.02)),
               "DIKGUN", font=f_w, fill=PAPER)

        left_limit, right = W * 0.485, W * 0.955
        f, px, lines = _fit_type(d, game, right - left_limit, H * 0.55,
                                 int(H * 0.27), H)
        lh, total = int(px * 0.95), len(lines) * int(px * 0.95)
        ty = H * 0.50 - total // 2
        for i, line in enumerate(lines):
            d.text((right - d.textlength(line, font=f), ty + i * lh),
                   line, font=f, fill=PAPER)

        f_c = ImageFont.truetype(F_BOLD, int(H * 0.055))
        cw = int(d.textlength("LIVE NOW", font=f_c)) + int(W * 0.045)
        d.rectangle([(W * 0.045, H * 0.915), (W * 0.045 + cw, H * 0.965)],
                    fill=RED + (255,))
        d.text((W * 0.045 + int(W * 0.022), H * 0.922), "LIVE NOW",
               font=f_c, fill=PAPER)
        f_h = ImageFont.truetype(F_CONS, int(H * 0.042))
        d.text((W * 0.045 + cw + int(W * 0.03), H * 0.922), "twitch.tv/dikgun",
               font=f_h, fill=INK)

        slug = game.replace(":", "").replace(" ", "-").lower()
        img.convert("RGB").save(out / f"thumb-{slug}.jpg", quality=92)
    print(f"  thumbnails: {len(THUMB_GAMES)} files -> {out}")


def build_contact_sheets() -> None:
    """Preview sheets, so the repo shows what a folder of assets looks like."""
    avatars = sorted((BRAND / "avatars").glob("*.png"))
    picks = [p for p in avatars if p.stem.endswith("-300")]
    if picks:
        tiles = []
        for p in picks:
            im = Image.open(p).convert("RGB").resize((300, 300))
            lab = Image.new("RGB", (300, 26), (20, 20, 20))
            ImageDraw.Draw(lab).text((8, 7), p.stem, font=ImageFont.truetype(
                F_CONS, 13), fill=(230, 230, 230))
            tile = Image.new("RGB", (300, 326), (20, 20, 20))
            tile.paste(lab, (0, 0))
            tile.paste(im, (0, 26))
            tiles.append(tile)
        while len(tiles) % 2:
            tiles.append(Image.new("RGB", (300, 326), (20, 20, 20)))
        rows = [Image.new("RGB", (600, 326), (20, 20, 20))]
        rows = []
        for i in range(0, len(tiles), 2):
            row = Image.new("RGB", (600, 326), (20, 20, 20))
            row.paste(tiles[i], (0, 0))
            row.paste(tiles[i + 1], (300, 0))
            rows.append(row)
        sheet = Image.new("RGB", (600, 326 * len(rows)), (20, 20, 20))
        for i, row in enumerate(rows):
            sheet.paste(row, (0, i * 326))
        sheet.save(BRAND / "contact-sheet-avatars.jpg", quality=92)
        print(f"  preview   : contact-sheet-avatars.jpg ({len(picks)} variants)")

    thumbs = sorted((BRAND / "thumbnails").glob("thumb-*.jpg"))
    thumbs = [t for t in thumbs if t.name != "thumb-contact-sheet.jpg"]
    if thumbs:
        cols = 3
        tw, th = 426, 240
        rows_n = (len(thumbs) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows_n * th), (20, 20, 20))
        for i, t in enumerate(thumbs):
            im = Image.open(t).convert("RGB").resize((tw, th))
            sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
        sheet.save(BRAND / "contact-sheet-thumbnails.jpg", quality=92)
        print(f"  preview   : contact-sheet-thumbnails.jpg ({len(thumbs)} games)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--avatars", action="store_true")
    ap.add_argument("--banners", action="store_true")
    ap.add_argument("--thumbnails", action="store_true")
    args = ap.parse_args()

    if not any((args.all, args.avatars, args.banners, args.thumbnails)):
        ap.print_help()
        return 1

    if args.all or args.avatars:
        build_avatars()
    if args.all or args.banners:
        build_twitch_banner()
        build_youtube_banner()
    if args.all or args.thumbnails:
        build_thumbnails()
    if args.all:
        build_contact_sheets()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
