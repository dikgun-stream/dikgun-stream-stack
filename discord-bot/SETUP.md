# Bot application setup — exact values

Everything to paste into the Discord Developer Portal, in the order it appears.

## General Information

| Field | Value |
|---|---|
| **App Icon** | `icon/icon-a-red-cream.png` (1024x1024 PNG, under 1 MB) |
| **Name** | `DikGunBot` — already set, leave it |
| **Description** | see below |
| **Tags** | `Utility`, `Moderation`, `Community`, `Fun`, `Streaming` |
| **Application ID** | `1573094178739724339` — public, fine to share |
| **Public Key** | public by design. Not a secret. **Not** the bot token. |
| **Interactions Endpoint URL** | **leave blank** |
| **Linked Roles Verification URL** | **leave blank** |
| **Terms of Service URL** | leave blank for now |
| **Privacy Policy URL** | leave blank for now |

### Description (282 characters, limit 400)

```
Companion bot for the DikGun stream. It answers with what the channel actually
knows: /live shows the real title, game and uptime; /clip cuts a 20 second
excerpt from the highlight pool; /clips, /goals and /checklist read straight
from the dashboard. Staff commands log to #mod-log.
```

Paste it as one paragraph — the line breaks above are only for readability here.

### Why those fields stay blank

**Interactions Endpoint URL** — this switches the bot from Gateway (outbound
websocket) to HTTP interactions. Setting it without a running HTTPS server means
Discord's verification PING fails within 3 seconds and the application stops
receiving interactions entirely. This bot uses Gateway, so it stays empty.

**Terms of Service / Privacy Policy** — only needed when the bot is verified or
public at scale. For a personal server they add nothing.

**Public Key** — worth saying plainly because it looks like a secret: it is the
key Discord uses to *verify* signatures, and it is public by design. The token on
the Bot tab is the one to protect.

## Icon options

`icon/icon-options.png` shows all three at full size plus circular 64px and 32px
previews, which is how the icon actually appears in the member list.

| File | Look |
|---|---|
| `icon-a-red-cream.png` | **recommended** — cream DG on signal red, separates the bot from your own avatar |
| `icon-b-ink-cream.png` | ink field, cream DG, red corner (the corner is lost to the circular crop) |
| `icon-c-cream-ink.png` | paper field, ink DG, red rule |

## Invite link

Correct permissions for this bot, `1102062021846`:

```
https://discord.com/oauth2/authorize?client_id=1573094178739724339&scope=bot+applications.commands&permissions=1102062021846
```

That covers view/send/embed/attach, manage messages, manage channels and roles
(needed once for `--provision`), kick, ban, timeout, view audit log, mention
everyone and use slash commands.

## Remaining steps, in order

1. **Bot** tab -> **Reset Token** -> copy it into `.env` as `DISCORD_TOKEN`
2. Same page -> **Privileged Gateway Intents** -> enable
   **Message Content Intent** and **Server Members Intent**
3. **Installation** tab -> install link -> **Discord Provided Link**
4. Invite it with the URL above
5. Right-click your server with Developer Mode on -> **Copy Server ID** ->
   `.env` as `DISCORD_GUILD_ID`
6. `python bot.py --provision`
7. `python bot.py`

`python bot.py --check` validates steps 1 and 5 without connecting to Discord.

**Never paste the bot token into chat, a commit, or a screenshot.** It is
equivalent to full control of the bot.
