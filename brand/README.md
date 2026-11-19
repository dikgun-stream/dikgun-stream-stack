# DikGun brand

The DG ligature is a D and a G that overlap: a rectangular D with a wedge cut
through it, and a rounded G that cuts into the D's right edge.

It was supplied as a 300x300 JPEG in which the mark occupies only about
198x104 pixels. Upscaling that to banner size gives soft edges and colour
fringing, so it is traced once to polygon geometry and every asset is rendered
from that geometry. Nothing in this folder is an upscaled bitmap.

## Handles

| Platform | Handle |
|---|---|
| Twitch | DikGun |
| YouTube | DikGun |
| X | DikGunLIVE |
| TikTok | personal account, to be converted |
| Discord | not supplied yet |

Use one avatar everywhere. That consistency is most of what "organised socials"
means in practice - someone who finds you on X and then on Twitch should see the
same mark and not wonder whether it is the same person.

## Palette

| Name | Hex | Used for |
|---|---|---|
| paper | `#ECE4D2` | backgrounds, matches the overlay scenes |
| ink | `#161412` | type and the mark |
| signal red | `#C8161D` | accents, the G in the duotone |

## Layout

```
brand/
  avatars/      4 variants x 1024/512/300/70/32
  banners/      twitch 1200x480, youtube 2048x1152 + 2 proof images
  thumbnails/   1280x720 cover per game, plus variety
  generators/   trace_mark.py, make_assets.py, dg-vector.json, dg-source.jpg
  dg-mark.svg   vector master
```

## Avatars

| File | Use |
|---|---|
| `01-avatar-cream` | default, matches the scene paper |
| `02-avatar-ink` | dark contexts, YouTube dark theme |
| `03-avatar-duotone` | the branded one - D in ink, G in signal red |
| `04-avatar-white` | plain white, for print or bright sites |

Twitch and YouTube crop avatars to a circle, so the mark is kept around 72% of
the width. It stays legible at 32px, which is the browser-tab size.

## Banners

**YouTube banners are cropped hard.** Only the centre **1235x338** is guaranteed
visible on phones and TVs. Everything in the YouTube banner is deliberately
inside that band. Two proof images ship with it:

- `youtube-safearea-check.png` draws the safe band as a blue box
- `youtube-mobile-crop-preview.png` is literally what a phone shows

If you edit the banner, keep the lockup inside that band or it will be cut. The
first attempt here put the footer outside it: fine on a desktop, broken on
mobile.

The Twitch banner has no such constraint, which is why it carries the URL strip
and the YouTube one does not.

## Thumbnails

One 1280x720 cover per game plus `variety`, all from one template: red field,
ink type panel, DG mark, LIVE NOW chip.

Long names overflowed the diagonal and "Battlefield 6" orphaned the 6 onto a
second line before the type fitter was fixed. It now prefers a single line and
only wraps when a single line would fall below a legible size.

### Why every VOD showed Siege cover art

YouTube generates a thumbnail from the stream when it ends if none was set, and
for gaming streams it falls back to the game cover art. That is why finished
streams showed Siege art regardless of what was played.

There is no "default thumbnail for all future live streams" in YouTube Studio,
so it is per stream:

1. **Before going live** - Studio -> Live Control Room -> edit thumbnail ->
   upload the matching file from `thumbnails/`
2. It carries over to the VOD when the stream ends
3. For past streams - Studio -> Content -> hover -> Edit -> Thumbnail

Automating it needs a Google OAuth client with
`https://www.googleapis.com/auth/youtube.upload` and `thumbnails.set` per video.
The dashboard holds no YouTube credentials today: title and category go through
Streamer.bot, which owns those tokens.

## Regenerating

```powershell
cd brand/generators
python trace_mark.py --in dg-source.jpg --out dg-vector.json   # optional: re-trace
python make_assets.py --all
```

`make_assets.py` also accepts `--avatars`, `--banners` and `--thumbnails` to
build one group. Output is deterministic - the grain is seeded, so re-running
produces byte-identical files and a clean `git status`.

Edit `URLS`, `GAMES_A`, `GAMES_B` or `THUMB_GAMES` at the top of
`make_assets.py` to change the copy, then re-run.

The trace is 3 shapes and 88 points, so it is small enough to inspect by hand if
you ever need to adjust the mark itself.
