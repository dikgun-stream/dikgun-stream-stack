# DikGun Discord bot

A self-hosted bot that runs on the same machine as the overlay server and answers
with **real state** instead of guesses: the actual stream title and game, the
real clip pool, whether the stream is up right now.

It exists because the dashboard already knows all of that, and a generic bot
cannot.

## What it can and cannot do

**I cannot create the Discord server for you.** Creating a server needs your
Discord account; there is no way for me to do it on your behalf, and no API bot
can create a guild it is not already in.

So the split is:

| Step | Who |
|---|---|
| Create the server | you, ~2 minutes |
| Invite this bot | you, one link |
| Build channels and roles | `bot.py --provision`, automated |
| AutoMod rules | you, by hand (Discord limitation, see below) |
| Run the bot | automated, runs alongside the overlay server |

## Setup

### 1. Create the application

1. <https://discord.com/developers/applications> -> **New Application**, name it `DikGun`
2. Left sidebar -> **Bot** -> **Reset Token** -> copy it (you only see it once)
3. On the same page, under **Privileged Gateway Intents**, enable:
   - **Message Content Intent** (required - without it the bot cannot read anything)
   - **Server Members Intent** (required for welcome messages)
4. Left sidebar -> **Installation** -> **Install Link** -> set to **Discord Provided Link**

Then copy `.env.example` to `.env` and paste the token.

### 2. Invite it

Guild install URL, replacing `YOUR_APP_ID`:

```
https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=1101659241494
```

That permission integer covers: manage channels and roles (needed once, for
provisioning), manage messages, moderate members, kick, ban, view audit log,
send messages, embed links, attach files, read message history, mention everyone.

If you would rather grant less, use **Administrator** for the provisioning run
and then reduce the bot's role to just the moderation permissions.

### 3. Build the server

```powershell
cd discord-bot
python bot.py --provision
```

This reads `blueprint.json` and creates:

```
WELCOME        rules, announcements, introductions, reaction-roles
THE STREAM     live, clips, chat, lfg
GAMES          deadlock, tarkov, apex, siege, other-games
VOICE          Lobby, Game 1, Game 2
STAFF          mod-log, staff-chat        (hidden from @everyone)
```

Roles: **Moderator**, **Subscriber**, **Clip Star**, **Member**.

It is idempotent - anything already existing by name is skipped, so you can edit
the blueprint and re-run.

### 4. Run it

```powershell
python bot.py --check      # verifies token + overlay server, changes nothing
python bot.py              # run it
```

Keep it running the way you keep `server.py` running.

## Commands

| Command | What it does |
|---|---|
| `/live` | Title, game, scene, uptime, and whether you are live |
| `/clip [game]` | Cuts a 20s excerpt from a random highlight and uploads it |
| `/clips` | Clip pool size by game |
| `/goals` | Follower/sub goal bars |
| `/checklist` | Pre-flight checklist progress |
| `/purge`, `/timeout`, `/kick` | Moderation, staff only, logged to `#mod-log` |

It also announces in `#live` when the stream goes up, and greets new members in
`#introductions`.

### Why clips are cut rather than linked

The indexed clips are **full recordings** - 310MB to 3.8GB each. Two things
follow:

- Linking `http://127.0.0.1:7475/...` **cannot work**: Discord renders media from
  its own servers, so a localhost URL fails silently.
- Uploading the original is impossible.

So `/clip` runs ffmpeg and posts a 20-second extract instead. Measured: **0.47MB,
about 2 seconds to encode.** If encoding fails it falls back to a still frame.

## Third-party bots worth adding

The custom bot deliberately does not reimplement things off-the-shelf bots do
better, or things only Discord itself can do:

| Need | Use | Why not custom |
|---|---|---|
| Twitch live alerts | [Streamcord](https://unstore.io/discover/best-streamcord-alternatives/) | free tier covers 10 streamers, <30s latency |
| AutoMod | Discord built-in | **bots cannot create AutoMod rules** - it is a server setting |
| Leveling / XP | Carl-bot or MEE6 | not worth maintaining |
| Tickets | Astero or similar | [comparison](https://discorder.tools/blog/best-discord-bots-for-streamers/) |

Streamcord's free tier is genuinely enough for one channel. It becomes limiting
only past ten streamers, or when you want YouTube/Kick alerts with per-stream
filters.

## AutoMod - the part that has to be manual

**A bot cannot create AutoMod rules.** They are server settings, not bot
permissions. Do these by hand in *Server Settings -> Safety Setup -> AutoMod*:

| Rule | Trigger | Action |
|---|---|---|
| Slurs and threats | keywords (your list) | Block message + timeout 10 min |
| Invite links | `discord.gg/`, `discord.com/invite/`, `dis.gd/` | Block + alert `#mod-log` |
| Spam | 12 messages / 10 seconds | Block + timeout 5 min |
| Mention flood | 8 mentions | Block + alert `#mod-log` |

Standard advice from [AutoMod guides](https://peakbot.pro/blog/best-discord-automod-settings)
is to start spam and mention rules as **alert-only** for a week, watch how often
they fire, then turn on the timeout. Setting them straight to punishment on day
one usually catches legitimate people.

Leave the built-in **"Block suspected spam"** and **"Block malware links"**
toggles on - they are free and catch most raids.

## Notes

- `.env` is gitignored. The token must never be committed.
- `--check` runs without a token and tells you exactly what is missing.
- The bot only needs plain HTTP to the overlay server; no credentials cross it.
