# Step-by-step walkthrough

Written for doing it once, slowly. If you would rather not edit files by hand,
run `setup.ps1` and skip to [Part 5](#part-5-provision-and-run) — it asks for the
two values and writes them for you.

---

## Part 1 — Find the folder

Press **Win + R**, paste this, press Enter:

```
C:\Users\JR\Documents\deepseek-harness\default-workspace\dikgun-stream\discord-bot
```

You should see these files. If you do not see `.env`, it means Explorer is hiding
it — see the note below.

```
.env                 <- the one you edit
.env.example         <- a template, do not edit
blueprint.json       <- channel and role layout
bot.py               <- the bot
SETUP.md             <- reference sheet
setup.ps1            <- guided setup
icon/                <- the app icon
```

### Why you could not find `.env`

It did not exist. `.env.example` is the template that ships in the repo, and you
are meant to copy it to `.env`. The dot at the front also makes it easy to
overlook, and Windows hides file extensions by default, so `.env.example` can
display as just `env.example`.

To see extensions: in Explorer, **View** tab -> tick **File name extensions**.

**`.env` now exists** — it was created for you. Open it with Notepad.

---

## Part 2 — Get the bot token

1. Go to <https://discord.com/developers/applications>
2. Click your application, **DikGunBot**
3. In the left sidebar, click **Bot**
4. Near the top, click **Reset Token**, then **Yes, do it**
5. Click **Copy**

> The token is shown **once**. If you close that box without copying, reset it
> again. This is the only value in the whole setup that is genuinely secret.

While you are on the **Bot** page, scroll down to **Privileged Gateway Intents**
and turn **both** of these on:

- ✅ **Message Content Intent**
- ✅ **Server Members Intent**

Click **Save Changes**. Skipping this is the single most common reason a bot
appears online and then ignores every command.

---

## Part 3 — Put the token in `.env`

Open `.env` in Notepad. Find this line:

```
DISCORD_TOKEN=
```

Paste the token immediately after the `=` so it looks like:

```
DISCORD_TOKEN=MTU3MzA5NDE3ODczOTcyNDMzOQ.Gxxxxx.xxxxxxxxxxxxxxxxxxxxxxxx
```

Rules that matter:

- **No spaces** before or after `=`
- **No quotes** around the token
- **One line only** — the token must not wrap

Save with **Ctrl + S**.

---

## Part 4 — Get your server ID

1. In Discord, click the **gear icon** (User Settings) at the bottom left
2. Scroll down to **Advanced**
3. Turn **Developer Mode** on
4. Close settings
5. **Right-click your server icon** -> **Copy Server ID**

Back in `.env`:

```
DISCORD_GUILD_ID=123456789012345678
```

Save again.

> Turning on Developer Mode adds a "Copy ID" option to every right-click menu.
> Without it the menu has no Copy Server ID entry at all — that is why people get
> stuck here.

---

## Part 5 — Check it

Open PowerShell in that folder:

```powershell
cd C:\Users\JR\Documents\deepseek-harness\default-workspace\dikgun-stream\discord-bot
python bot.py --check
```

Expected output:

```
  overlay   : http://127.0.0.1:7475
  overlay ok: live=False game='Escape from Tarkov'
  token     : present (72 chars)
  guild id  : 123456789012345678
```

If `token : MISSING`, the `.env` was not saved or has a typo.
If `overlay : UNREACHABLE`, start the overlay server first:

```powershell
cd ..\overlay-server
python -m uvicorn server:app --host 127.0.0.1 --port 7475
```

---

## Part 6 — Invite the bot

Open this URL in a browser and pick your server:

```
https://discord.com/oauth2/authorize?client_id=1573094178739724339&scope=bot+applications.commands&permissions=1102062021846
```

You need **Manage Server** permission on the server. After accepting, the bot
appears in the member list but stays offline until you run it.

---

## Part 7 — Provision and run

```powershell
python bot.py --provision
```

This builds the roles and channels from `blueprint.json`. You will see a running
list of what it created. It is safe to run twice — anything that already exists
by name is skipped.

Then run the bot:

```powershell
python bot.py
```

Leave that window open. In Discord, type `/live` in any channel. If nothing
appears, slash commands have not registered yet — see Troubleshooting.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `token : MISSING` | `.env` not saved, or spaces/quotes around the token |
| Bot online but ignores every command | **Message Content Intent** not enabled |
| Slash commands do not appear | Not synced yet. Commands are guild-scoped when `DISCORD_GUILD_ID` is set and appear in seconds; with it blank they are global and can take an hour. Restart the bot and reload Discord with **Ctrl + R**. |
| `I cannot kick that member` | The bot's role sits below the target's role. Server Settings -> Roles -> drag the bot's role higher. |
| `/clip` says file not reachable | `VIDEO_ROOT` / `VIDEOQ_ROOT` in `.env` do not match where the clips live. |
### `guilds: 0` and `403 Forbidden (error code: 50001)`

This means the bot logged in successfully but **is not in your server**. Those
are two separate things: logging in authenticates the bot, being invited puts it
somewhere. Discord cannot sync commands into a server the bot has never joined,
so it returns a bare 403.

Fix: open the invite URL from Part 6, pick your server, accept. Then press
**Ctrl + C** in the bot window and run `python bot.py` again. It should print the
server name and member count.

| `overlay : UNREACHABLE` | The overlay server is not running on port 7475. |

---

## The one thing to never do

**Do not paste the bot token into Discord, a chat, a commit, or a screenshot.**
Anyone with it controls the bot completely. If it leaks, Reset Token immediately.

`.env` is already listed in `.gitignore`, so it will not be committed.
