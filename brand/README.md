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
