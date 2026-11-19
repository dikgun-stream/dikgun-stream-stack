# DikGun brand — DG mark

The DG ligature is a D and a G that overlap: a rectangular D with a wedge cut
through it, and a rounded G that cuts into the D's right edge. It was supplied
as a 300x300 JPEG, which is soft. This pack is traced to vector so it is crisp
at every size.

## Handles

| Platform | Handle |
|---|---|
| Twitch | DikGun |
| YouTube | DikGun |
| X | DikGunLIVE |
| TikTok | personal account, to be converted |
| Discord | (not supplied) |

Use one avatar everywhere. That consistency is most of what "organised socials"
actually means - a viewer who finds you on X and then Twitch should see the same
mark without wondering if it is the same person.

## Files

`exports/` holds every variant at 1024, 512, 300, 70 and 32 pixels.

| Variant | Use |
|---|---|
| 01-avatar-cream | default. matches the scene paper colour |
| 02-avatar-ink | dark contexts, YouTube dark theme |
| 03-avatar-duotone | the branded one - D in ink, G in signal red |
| 04-avatar-white | plain white background, for print or bright sites |

`dg-mark.svg` is the true vector master. Use it for anything larger than 1024px,
for panels, or for print. It is one `<polygon>` per shape, so it can be
recoloured by editing the single `fill` attribute.

## Palette

| Name | Hex |
|---|---|
| paper | #ECE4D2 |
| ink | #161412 |
| signal red | #C8161D |

These match the overlay scenes, so the channel page and the stream look like the
same brand.

## Sizing notes

- Twitch crops avatars to a circle, so keep the mark inside ~56% of the width if
  the background is not flat. Variants 01-04 are safe.
- 70x70 is what chat shows. The mark is still legible there; do not add detail.
- 32x32 is the browser tab. Verified legible.

## Regenerating

The trace lives in `brand/dg-vector.json` (polygon points, 3 shapes, 88 points).
Re-render at any size by scaling the polygons - there is no raster upscaling
anywhere in this pipeline.

## Banners

| Platform | File | Size | Notes |
|---|---|---|---|
| Twitch | `banners/twitch-banner-1200x480.png` | 1200x480 | Twitch profile banner |
| YouTube | `banners/youtube-banner-2048x1152.png` | 2048x1152 | 16:9 channel banner |

**YouTube banners are cropped hard.** Only the centre **1235x338** is guaranteed
visible on phones and TVs. Everything in the YouTube banner is deliberately
inside that band -- `youtube-safearea-check.png` draws the box, and
`youtube-mobile-crop-preview.png` is literally what a phone shows. If you edit
the banner, keep the lockup inside that band or it will be cut.

The Twitch banner has no such constraint, which is why it carries the URL strip
and the YouTube one does not.

## Thumbnails

`thumbnails/` has a 1280x720 cover per game plus `variety`:

    Deadlock, Escape from Tarkov, Apex Legends, Rainbow Six Siege,
    Battlefield 6, The Finals, Hunt Showdown, Hell Let Loose,
    R.E.P.O., PUBG, Variety

All one template: red field, ink type panel, DG mark, LIVE NOW chip. Upload the
matching one per stream so every VOD reads as the same channel.

### Why every VOD showed Siege cover art

YouTube auto-generates a thumbnail when a stream ends if none was set, and for
gaming streams it falls back to the game's cover art. That is why every finished
stream showed Siege art regardless of what was actually played.

Fix, per stream:

1. **Before going live** -- YouTube Studio -> Live Control Room -> the stream ->
   edit thumbnail -> upload the matching file from `thumbnails/`.
2. That thumbnail carries over to the VOD when the stream ends.
3. For past streams: YouTube Studio -> Content -> hover the video -> Edit ->
   Thumbnail -> upload.

There is no "default thumbnail for all future live streams" setting in YouTube
Studio, so this is per-stream.

### Automating it

Setting thumbnails by API needs a Google OAuth client with the
`https://www.googleapis.com/auth/youtube.upload` scope, plus `thumbnails.set`
on each video. The dashboard does not hold YouTube credentials today -- title
and category go through Streamer.bot, which owns those tokens. Automating
thumbnails means adding a Google OAuth flow, which is a separate piece of work.
