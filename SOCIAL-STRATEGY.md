# Launch & Social Strategy — X, YouTube, TikTok

Everything below assumes one goal: **turn short-form attention into Twitch follows and live viewers.** Every post is a funnel step, not a thing that lives alone. "Launch" is not a single stream — it is a two-week period where the machine that already exists (auto-clips, promos, brand kit, Discord) gets pointed at new people on purpose.

---

## 1. What you already have that most streamers do not

This is the actual advantage — the stack produces the hard part automatically:

| Asset | Source | Frequency it can support |
|---|---|---|
| Verified kill clips | auto-clipper / scanner windows (real kills, ~6–20 s, lead-in before the kill) | **multiple per session** |
| Branded promos (DIKGUN stingers, kill-on-beat, outro) | `promo_build.py` | 1–2 per week |
| Brand kit (mark, palette, thumbnails, vector DG) | `brand/` | every post, instantly |
| Live polls + topic card | overlay server | engagement posts on X |
| Discord community | `discord-bot/` | where the funnel lands |

The strategy is: **the machine makes the clips, you spend your time on the words and the replies.** Replies win.

## 2. Platform roles — they are not the same job

| Platform | Job | Content | CTA |
|---|---|---|---|
| **TikTok** | Reach. Volume. New eyes. | Raw 9:16 kill clips, 12–45 s, hook in the first 1.5 s. Vertical VOD/auto-clips are already 9:16 — post them *without* re-cropping | On-screen handle + "live every [day/time] on Twitch". No clickable links (until you have 1k followers / bio link) |
| **YouTube** | Search + longevity. Two lanes: **Shorts** (same clips as TikTok) and the **main channel** (full promos, highlight compilations, VOD highlights, stream recaps) | Shorts: same clips. Main: promos as hero uploads; clips compiled per game ("The Best of Apex — Week 1") | Description line 1: Twitch link + schedule. Pinned comment asking a question |
| **X** | Identity and conversation. This is where "engagement" actually happens for a streamer | 2–3 posts/day: one clip, one text post (opinion/question/poll), one reply-storm participation. The dashboard's polls become X polls | Link in bio, not in the post (X punishes link-posts) |

One clip goes everywhere — but **never the same caption**. TikTok gets the energy line, Shorts gets the searchable title, X gets the joke or the question.

## 3. The launch window (14 days, then steady state)

### Week 1 — Tease (no stream announced yet)

- **Day 1–2:** Post 2 clips/day from test-stream footage. No CTA beyond the handle. Goal: warm the algorithm on your content type. Title formula: `[Game] + [moment in plain words] + [outcome]` — "Apex: 3 kills in 8 seconds, all with one magazine".
- **Day 3:** X announcement post: "The [day] streams were practice. [Launch date] is the real one. [What the stream is: one sentence, no adjectives]." Pin it.
- **Day 4–6:** One promo (full DIKGUN edit) to YouTube main channel + 30 s cutdown to Shorts/TikTok. Reply to every comment within the hour for the first 3 hours after posting.

### Week 2 — Launch

- **Day 7 (launch day):** YouTube Premiere of the best promo at a time you name 48 h in advance. X countdown post 1 h before. Stream starts with the Starting Soon scene already counting down.
- **Launch stream itself:** the engagement tools are already in the scene: `!hint`, `!vote` polls, `!hype`. Call them out on mic twice an hour — "what map next, chat decides" is the single best live-interaction loop the stack has.
- **Day 8–14:** Post 1 clip/day minimum, reply to everything, X poll every other day, and one "aftermath" post with a screenshot of chat choosing the poll.

### Steady state (forever after)

- **1 clip/day**, 5–6 days/week. Batching: one sitting cuts a week of clips with `promo_build` + the auto-clip folder; schedule them.
- **1 hero upload/week** to YouTube main.
- **2–3 X posts/day** — only one of them promotional.
- Consistency beats virality. A clip that does nothing still trains the algorithm and feeds the next one.

## 4. The engagement mechanics (this is what "engagement" concretely means)

1. **First hour = reply hour.** Platforms boost posts with early comment velocity. When you post, stay in the app for 60 minutes replying to *every* comment. This one habit outperforms any tag or trend.
2. **Pin a question, not a statement.** Pinned comment: "Which loadout would you have taken here?" — pinned questions generate comment chains.
3. **End every clip with a question on screen** for 1 second: "wrong gun?" / "rate this clutch". Questions are the cheapest comment generator that exists.
4. **X polls are free engagement** — and your dashboard already speaks poll. Re-post stream poll results as screenshots: "Chat voted. They chose [map]. They were wrong." (conflict is engagement).
5. **Reply as the brand, not the person, when it's funny** — the DIKGUN voice (flat, dry, constructivist) is a differentiator. Keep it.
6. **Community posts on YouTube** (poll: "next promo song") between uploads keep the channel alive with zero editing.
7. **Stitch/Duet the replies.** When someone comments something good, answer with a 15-second video reply. Reply-videos are the highest-converting format on TikTok.

## 5. Hook rules (the first 1.5 seconds decide everything)

- Never open with "hey guys". Open with the outcome or the moment: the kill shot, the clutch start, the drop.
- **Text on screen within 0.5 s.** The auto-clip lead-in gives you ~4 s before the kill — that lead-in is where the caption text goes, not dead air.
- Caption formula: `[stakes] + [result]` — "I had 1 HP" / "3 squads pushed" / "the lobby chose violence".
- One idea per clip. A clip with two moments gets split into two clips.
- Sound: keep game audio + your reaction; add music only if it *rides* the beats (the promo generator does this — the raw clips do not need it).

## 6. Metadata that actually matters

- **YouTube titles:** `[Game] — [plain description] (clip)` for search. Tags: game, "shorts", your name. Thumbnails exist in `brand/` — use them consistently; a thumbnail style that never changes is worth more than a clever one.
- **Descriptions:** Twitch link + schedule in line 1; the `brand/youtube-description.md` template already exists — use it verbatim every upload.
- **TikTok:** hashtags 3–5, one being your own (#dikgun) so a discoverable feed assembles itself over time.
- **X:** no hashtags in the main text; one at the end if it helps a community (e.g. #apexlegends) — never more.

## 7. What to build next to make this automatic

Ranked by how much time it returns:

1. **9:16 export in `promo_build.py`** (`--vertical`): render the same cut cropped to 1080×1920 for Shorts/TikTok. The vertical Aitum recordings are already 9:16, so this is for *branded* promo cutdowns. Until then: raw clips to short-form, promos to YouTube main only.
2. **Auto-clip → auto-index:** write auto-clip sidecars into the scanner indexes (open item #19) so a session's clips are postable the same day without a rescan.
3. **Scheduled uploads via APIs:** YouTube Data API uploads are fully automatable (the `youtube` config already exists for viewers). X API v2 posting works with their keys. **TikTok's Content Posting API requires an approved developer application — not automatable yet; posting there stays manual** unless you win approval.
4. **Clip review queue:** a dashboard page listing today's auto-clips with a one-click "post this" → which writes the caption file for the upload step. The editing is solved; the *choosing* is the remaining bottleneck.

## 8. Rules of thumb

- **Never announce a schedule you will not keep.** "Live every [day] at [time]" is a promise; the whole funnel depends on it.
- **Don't beg.** "Follow me" kills clips. The handle on screen and the link in the description do the asking.
- **Post the loss too.** Failures get more engagement than clean wins — a "worst death of the week" clip out-performs most clutch clips.
- **One platform's comment section is where the person is.** The algorithm moves viewers; replies convert them. The first-hour reply habit is the strategy; everything else is supply.
- **Measure what converts, not what goes viral:** follows-per-post and show-up rate at stream start beat view counts.

---

*Where this connects to the stack: `SOCIAL-STRATEGY.md` assumes the promo generator (`promo/CHEATSHEET.md`), the auto-clipper (`/api/autoclip/*`), the clips pipeline (`/api/clips/highlights`), the Discord bot, and the brand kit. The two missing pieces before full automation are the 9:16 promo export and the TikTok posting path — both listed in `README.md` open items #4 and #6.*
