#!/usr/bin/env python
"""DikGun Discord bot.

Runs next to the overlay server on the same machine and talks to it over HTTP, so
it can answer with real state instead of guesses: the current title and game,
the actual clip pool, whether the stream is up right now.

    python bot.py                # run it
    python bot.py --provision    # build the channel/role structure from blueprint.json
    python bot.py --check        # verify config and connectivity, then exit

Requires DISCORD_TOKEN in .env (see .env.example) and the Message Content
Intent enabled in the Discord Developer Portal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
OVERLAY = os.getenv("OVERLAY_BASE", "http://127.0.0.1:7475").rstrip("/")

# colour used for every embed, matching the scene palette
BRAND_RED = 0xC8161D
BRAND_PAPER = 0xECE4D2
BRAND_INK = 0x161412

LIVE_POLL_SECONDS = 60


# --------------------------------------------------------------------- overlay
class Overlay:
    """Thin async client for the local overlay/dashboard server."""

    def __init__(self, base: str) -> None:
        self.base = base
        self._session: aiohttp.ClientSession | None = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def get(self, path: str) -> dict | None:
        try:
            s = await self.session()
            async with s.get(f"{self.base}{path}") as r:
                if r.status != 200:
                    return None
                return await r.json()
        except Exception:  # noqa: BLE001
            return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


overlay = Overlay(OVERLAY)


# Where the two clip mounts live on disk. Discord fetches embed media from its
# own servers, so linking to http://127.0.0.1:7475 would silently fail - the bot
# has to read the file locally and upload it instead.
MOUNT_ROOTS = {
    "videos": os.getenv("VIDEO_ROOT", r"C:\Users\JR\Videos"),
    "videosq": os.getenv("VIDEOQ_ROOT", r"Q:\Game Clips"),
}


def clip_path(item: dict) -> Path | None:
    root = MOUNT_ROOTS.get(str(item.get("mount") or "videos"))
    if not root:
        return None
    rel = str(item.get("rel", "")).replace("\\", "/")
    path = Path(root) / Path(rel)
    return path if path.is_file() else None


def clip_frame(path: Path, at_seconds: float, out: Path) -> Path | None:
    """Grab a single frame as a small JPG, for when the clip is too big to attach."""
    import subprocess
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(at_seconds), "-i", str(path),
             "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", str(out)],
            check=False, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out if out.is_file() else None
    except Exception:  # noqa: BLE001
        return None


# Discord’s default upload ceiling is 10MB (25MB with boosts). Stay well under
# it and fall back to a still frame rather than failing the upload.
MAX_ATTACH_BYTES = 8 * 1024 * 1024


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# --------------------------------------------------------------------- bot
intents = discord.Intents.default()
intents.message_content = True      # required to read commands and chat
intents.members = True              # required for welcome / join events

bot = commands.Bot(command_prefix="!", intents=intents)
started_at = time.time()
_last_live = False


INVITE_URL = (
    "https://discord.com/oauth2/authorize?client_id={app}"
    "&scope=bot+applications.commands&permissions=1102062021846"
)


@bot.event
async def on_ready() -> None:
    print(f"  logged in as {bot.user} (id {bot.user.id})")
    print(f"  guilds: {len(bot.guilds)}   overlay: {OVERLAY}")

    # A bot can log in fine without being in any server. Syncing then fails with
    # a bare 403/50001 that says nothing useful, so say what is actually wrong.
    if not bot.guilds:
        print()
        print("  !! The bot is not in any server yet.")
        print("     Logging in is not the same as being invited.")
        print()
        print("     Open this and pick your server:")
        print("     " + INVITE_URL.format(app=bot.user.id if bot.user else "1573094178739724339"))
        print()
        if GUILD_ID:
            print(f"     DISCORD_GUILD_ID is set to {GUILD_ID}, but the bot cannot see it.")
        print("     Then stop this (Ctrl+C) and run it again.")
        print()
        return

    try:
        if GUILD_ID:
            guild = bot.get_guild(GUILD_ID)
            if guild is None:
                print(f"  !! DISCORD_GUILD_ID={GUILD_ID} is not one of the servers "
                      f"this bot is in:")
                for g in bot.guilds:
                    print(f"       {g.id}  {g.name}")
                print("     Fix DISCORD_GUILD_ID in .env, or leave it blank.")
            else:
                bot.tree.copy_global_to(guild=guild)
                await bot.tree.sync(guild=guild)
                print(f"  synced commands to {guild.name} ({guild.id})")
        else:
            await bot.tree.sync()
            print("  synced commands globally (can take up to an hour to appear)")
    except Exception as exc:  # noqa: BLE001
        print(f"  !! command sync failed: {exc}")

    for g in bot.guilds:
        print(f"  server: {g.name}  ({g.id})  {len(g.members)} members")

    if not watch_stream.is_running():
        watch_stream.start()


# ------------------------------------------------------------------ commands
@bot.tree.command(description="Current stream title, game and whether we are live")
async def live(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    st = await overlay.get("/api/live/status")
    if not st:
        await interaction.followup.send(
            "Overlay server is not reachable. Is `server.py` running?")
        return

    is_live = st.get("live")
    embed = discord.Embed(
        title="LIVE NOW" if is_live else "Offline",
        description=f"**{st.get('title') or 'no title set'}**",
        colour=BRAND_RED if is_live else BRAND_INK,
    )
    embed.add_field(name="Game", value=st.get("game") or "-", inline=True)
    embed.add_field(name="Scene", value=st.get("scene") or "-", inline=True)
    if is_live and st.get("uptime_ms"):
        embed.add_field(name="Uptime", value=fmt_time(st["uptime_ms"] / 1000),
                        inline=True)
    embed.add_field(name="Twitch", value=st.get("twitch") or "-", inline=False)
    await interaction.followup.send(embed=embed)


@bot.tree.command(description="Pull a random clip from the highlight pool")
@app_commands.describe(game="Optional: filter by game, e.g. Deadlock")
async def clip(interaction: discord.Interaction, game: str | None = None) -> None:
    await interaction.response.defer()
    data = await overlay.get("/api/clips/highlights?source=all&limit=500")
    if not data or not data.get("highlights"):
        await interaction.followup.send("No clips found - has the scanner run?")
        return

    pool = data["highlights"]
    if game:
        pool = [c for c in pool if game.lower() in str(c.get("game", "")).lower()]
        if not pool:
            await interaction.followup.send(f"No clips tagged `{game}`.")
            return

    pick = random.choice(pool)
    embed = discord.Embed(
        title=pick.get("title") or "clip",
        description=f"**{pick.get('game')}** \u00b7 {pick.get('source')} \u00b7 "
                    f"starts at {fmt_time(pick.get('start', 0))}",
        colour=BRAND_PAPER,
    )
    embed.set_footer(text=f"{len(pool)} clips in pool")

    path = clip_path(pick)
    if path is None:
        await interaction.followup.send(
            embed=embed,
            content="(clip file is not reachable from this machine - check "
                    "VIDEO_ROOT / VIDEOQ_ROOT in .env)")
        return

    start = float(pick.get("start", 0))
    size = path.stat().st_size

    # a source small enough to post whole
    if size <= MAX_ATTACH_BYTES:
        await interaction.followup.send(embed=embed,
                                        file=discord.File(str(path), filename=path.name))
        return

    embed.add_field(name="Source", value=f"{size/1e6:.0f} MB recording",
                    inline=False)
    await interaction.followup.send(
        content="Cutting a clip...", ephemeral=True)

    out = HERE / "_clipexcerpt.mp4"
    for stale in (out, HERE / "_clipframe.jpg"):
        stale.unlink(missing_ok=True)

    excerpt = await asyncio.to_thread(clip_excerpt, path, start, out)
    if excerpt and excerpt.stat().st_size <= MAX_ATTACH_BYTES:
        await interaction.edit_original_response(content=None)
        await interaction.followup.send(
            embed=embed,
            file=discord.File(str(excerpt), filename=f"{path.stem}-{int(start)}s.mp4"))
        excerpt.unlink(missing_ok=True)
        return

    # encoding failed or the excerpt was still too big: send a still
    frame = await asyncio.to_thread(clip_frame, path, start + 2.0,
                                    HERE / "_clipframe.jpg")
    await interaction.edit_original_response(content=None)
    if frame:
        await interaction.followup.send(
            embed=embed, file=discord.File(str(frame), filename="frame.jpg"))
        frame.unlink(missing_ok=True)
    else:
        await interaction.followup.send(embed=embed)


@bot.tree.command(description="How many clips are indexed, by game")
async def clips(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    data = await overlay.get("/api/clips/highlights?source=all&limit=500")
    if not data:
        await interaction.followup.send("Overlay server unreachable.")
        return
    counts: dict[str, int] = {}
    for c in data.get("highlights", []):
        counts[str(c.get("game"))] = counts.get(str(c.get("game")), 0) + 1
    lines = [f"`{n:>3}`  {g}" for g, n in sorted(counts.items(), key=lambda x: -x[1])]
    embed = discord.Embed(
        title="Clip pool",
        description="\n".join(lines) or "empty",
        colour=BRAND_PAPER,
    )
    embed.set_footer(text=f"{data.get('count', 0)} clips total")
    await interaction.followup.send(embed=embed)


@bot.tree.command(description="Follower goal progress")
async def goals(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    cfg = await overlay.get("/api/config")
    goals_cfg = (cfg or {}).get("goals") or {}
    if not goals_cfg:
        await interaction.followup.send("No goals configured.")
        return
    embed = discord.Embed(title="Goals", colour=BRAND_PAPER)
    for key, g in goals_cfg.items():
        cur, tgt = g.get("current", 0), g.get("target", 1) or 1
        pct = min(100, cur / tgt * 100)
        filled = int(pct / 100 * 12)
        bar = "\u2588" * filled + "\u2591" * (12 - filled)
        embed.add_field(name=g.get("label", key),
                        value=f"`{bar}`  **{cur}** / {tgt}", inline=False)
    await interaction.followup.send(embed=embed)


@bot.tree.command(description="Pre-flight checklist status")
async def checklist(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    data = await overlay.get("/api/checklists")
    if not data:
        await interaction.followup.send("Overlay server unreachable.")
        return
    s = data.get("summary", {})
    su, sd = s.get("startup", {}), s.get("shutdown", {})
    embed = discord.Embed(
        title="Stream checklists",
        colour=BRAND_PAPER,
        description=(f"**Before going live:** {su.get('done')}/{su.get('total')}\n"
                     f"**After stopping:** {sd.get('done')}/{sd.get('total')}"),
    )
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------- moderation
def _staff(interaction: discord.Interaction) -> bool:
    perms = interaction.user.guild_permissions
    return perms.manage_messages or perms.administrator


@bot.tree.command(description="Delete recent messages (staff only)")
@app_commands.describe(count="How many messages, 1-100")
async def purge(interaction: discord.Interaction, count: int = 10) -> None:
    if not _staff(interaction):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return
    count = max(1, min(100, count))
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=count)
    await _log(interaction.guild, "purge",
               f"{len(deleted)} messages in {interaction.channel.mention}",
               interaction.user)
    await interaction.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)


@bot.tree.command(description="Time a member out (staff only)")
@app_commands.describe(member="Who", minutes="How long, 1-10080", reason="Why")
async def timeout(interaction: discord.Interaction, member: discord.Member,
                  minutes: int = 10, reason: str = "no reason given") -> None:
    if not _staff(interaction):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return
    minutes = max(1, min(10080, minutes))
    try:
        await member.timeout(discord.utils.utcnow() + _minutes(minutes),
                             reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message(
            "I cannot time that member out - check my role position.", ephemeral=True)
        return
    await _log(interaction.guild, "timeout", f"{member} for {minutes}m: {reason}",
               interaction.user)
    await interaction.response.send_message(
        f"{member.mention} timed out for {minutes} minutes.", ephemeral=True)


def _minutes(n: int):
    from datetime import timedelta
    return timedelta(minutes=n)


@bot.tree.command(description="Kick a member (staff only)")
async def kick(interaction: discord.Interaction, member: discord.Member,
               reason: str = "no reason given") -> None:
    if not _staff(interaction):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return
    try:
        await member.kick(reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message(
            "I cannot kick that member - check my role position.", ephemeral=True)
        return
    await _log(interaction.guild, "kick", f"{member}: {reason}", interaction.user)
    await interaction.response.send_message(f"Kicked {member}.", ephemeral=True)


async def _log(guild: discord.Guild | None, action: str, detail: str,
               who: discord.abc.User) -> None:
    if guild is None:
        return
    channel = discord.utils.get(guild.text_channels, name="mod-log")
    if channel is None:
        return
    embed = discord.Embed(title=f"Moderation \u00b7 {action}", description=detail,
                          colour=BRAND_RED, timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"by {who}")
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


# ------------------------------------------------------------ stream watcher
@tasks.loop(seconds=LIVE_POLL_SECONDS)
async def watch_stream() -> None:
    """Announce in #live when the stream comes up.

    Deliberately edge-triggered: it posts once on the transition to live, not on
    every poll, so a long stream does not spam the channel.
    """
    global _last_live
    st = await overlay.get("/api/live/status")
    if not st:
        return
    is_live = bool(st.get("live"))
    if is_live and not _last_live:
        channel = None
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="live")
            if channel:
                break
        if channel:
            embed = discord.Embed(
                title="LIVE NOW",
                description=f"**{st.get('title') or 'stream'}**",
                url=st.get("twitch"),
                colour=BRAND_RED,
            )
            embed.add_field(name="Game", value=st.get("game") or "-", inline=True)
            embed.set_footer(text="see you in chat")
            try:
                await channel.send(content="@everyone" if False else None, embed=embed)
            except discord.Forbidden:
                pass
    _last_live = is_live


@watch_stream.before_loop
async def _before_watch() -> None:
    await bot.wait_until_ready()


# --------------------------------------------------------------- onboarding
@bot.event
async def on_member_join(member: discord.Member) -> None:
    channel = discord.utils.get(member.guild.text_channels, name="introductions")
    if channel is None:
        return
    embed = discord.Embed(
        title=f"Welcome, {member.display_name}",
        description=("Tell us what you play. The clip pool is always open - "
                     "try `/clip`."),
        colour=BRAND_PAPER,
    )
    try:
        await channel.send(content=member.mention, embed=embed)
    except discord.Forbidden:
        pass


# ---------------------------------------------------------------- provisioning
async def provision() -> None:
    """Create the channel and role structure described in blueprint.json.

    Idempotent: anything that already exists by name is left alone, so it is safe
    to re-run after editing the blueprint.
    """
    if not bot.guilds:
        raise SystemExit("bot is in no guilds - invite it first")
    guild = bot.guilds[0]
    print(f"  provisioning {guild.name}")

    bp = json.loads((HERE / "blueprint.json").read_text(encoding="utf-8"))

    existing_roles = {r.name: r for r in guild.roles}
    roles: dict[str, discord.Role] = {}
    for spec in bp.get("roles", []):
        role = existing_roles.get(spec["name"])
        if role is None:
            role = await guild.create_role(
                name=spec["name"],
                colour=discord.Colour(int(spec.get("color", "9BA3AF"), 16)),
                hoist=spec.get("hoist", False),
                mentionable=spec.get("mentionable", False),
                reason="blueprint",
            )
            print(f"    + role {spec['name']}")
        roles[spec["key"]] = role

    for cat_spec in bp.get("categories", []):
        cat = discord.utils.get(guild.categories, name=cat_spec["name"])
        if cat is None:
            cat = await guild.create_category(cat_spec["name"], reason="blueprint")
            print(f"    + category {cat_spec['name']}")

        if cat_spec.get("staff_only"):
            await cat.set_permissions(guild.default_role, view_channel=False)
            if "mod" in roles:
                await cat.set_permissions(roles["mod"], view_channel=True)

        for ch_spec in cat_spec.get("channels", []):
            name = ch_spec["name"]
            kind = ch_spec.get("type", "text")
            if discord.utils.get(cat.channels, name=name) is not None:
                continue
            if kind in ("text", "announcement"):
                ch_type = (discord.ChannelType.news if kind == "announcement"
                           else discord.ChannelType.text)
                ch = await guild.create_text_channel(
                    name, category=cat, topic=ch_spec.get("topic", ""),
                    type=ch_type, reason="blueprint")
                if ch_spec.get("readonly"):
                    await ch.set_permissions(guild.default_role,
                                             send_messages=False)
            else:
                await guild.create_voice_channel(name, category=cat,
                                                 reason="blueprint")
            print(f"    + {kind:12} {cat_spec['name']}/{name}")

    print("  done. AutoMod rules must be set by hand - see README.")


async def check() -> int:
    """Verify configuration without connecting to Discord."""
    ok = True
    print(f"  overlay   : {OVERLAY}")
    st = await overlay.get("/api/live/status")
    if st:
        print(f"  overlay ok: live={st.get('live')} game={st.get('game')!r}")
    else:
        print("  overlay   : UNREACHABLE - start server.py first")
        ok = False
    if TOKEN:
        print(f"  token     : present ({len(TOKEN)} chars)")
    else:
        print("  token     : MISSING - copy .env.example to .env and fill it in")
        ok = False
    print(f"  guild id  : {GUILD_ID or 'unset (commands sync globally)'}")
    await overlay.close()
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provision", action="store_true",
                    help="create channels and roles, then exit")
    ap.add_argument("--check", action="store_true",
                    help="verify config and overlay connectivity, then exit")
    args = ap.parse_args()

    if args.check:
        return asyncio.run(check())

    if not TOKEN:
        print("  DISCORD_TOKEN is not set. Copy .env.example to .env and paste "
              "the bot token from the Discord Developer Portal.")
        return 1

    if args.provision:
        async def runner() -> None:
            @bot.event
            async def on_ready_runner() -> None:
                try:
                    await provision()
                finally:
                    await overlay.close()
                    await bot.close()

            bot.on_ready = on_ready_runner  # type: ignore[assignment]
            await bot.start(TOKEN)
        asyncio.run(runner())
        return 0

    bot.run(TOKEN, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
