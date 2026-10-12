"""SachScan Discord bot (discord.py). Separate process:

    backend\\.venv\\Scripts\\python -m bots.discord_bot        (from the repo root)

Features
  /check text:<…> attachment:<file> public:<bool>   fact-check; ephemeral reply unless public:true
  Right-click a message → Apps → "Check with SachScan"
  /sachscan enable #channel | disable [#channel] | status   (Manage Server only)
      AutoMod Sentinel: checks new messages with links/attachments in opted-in channels and replies
      in a thread ONLY when something is false, outdated or a scam. It never deletes anything.
  /link <code>, /help, and direct messages to the bot work like the WhatsApp bot.

Needs the Message Content privileged intent (Developer Portal → Bot → Privileged Gateway Intents).
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Union

import discord
from discord import app_commands

from backend import config
from backend.channels import common
from backend.pipeline.core import NormalizedInput, content_type_for
from backend.pipeline.extract import ExtractionError

log = logging.getLogger("sachscan.discord")

MAX_MSG = 1990
MB = 1024 * 1024
URL_RE = re.compile(r"https?://\S+", re.I)
PLATFORM = "discord"


# ---------------------------------------------------------------------------
# Input conversion (the only Discord-specific part of a check)
# ---------------------------------------------------------------------------

async def build_input(text: str, attachments: list[discord.Attachment], user_id: int, message_id: Optional[int]) -> Union[NormalizedInput, str]:
    """Discord message parts -> NormalizedInput, or a user-facing error string."""
    text = (text or "").strip()
    inp = NormalizedInput(platform=PLATFORM, text=text, external_user_id=str(user_id),
                          original_message_id=f"discord:{message_id}" if message_id else None)
    for att in attachments:
        try:
            ctype = content_type_for(att.content_type or "", att.filename)
        except ExtractionError:
            continue  # unsupported file type: skip it (text may still be checkable)
        if ctype == "text":
            continue
        limit = config.MAX_PDF_MB if ctype == "pdf" else config.MAX_MEDIA_MB
        if att.size > limit * MB:
            return f"⚠️ `{att.filename}` is too large. I can check files up to {limit} MB."
        inp.file_bytes = await att.read()
        inp.mime_type, inp.file_name, inp.content_type = att.content_type or "", att.filename, ctype
        return inp
    if attachments and not text:
        return "⚠️ I can check images, voice notes, videos, PDFs, links and text. That file type isn't supported."
    if not text:
        return "Give me some text, a link or a file to check."
    inp.content_type = "url" if URL_RE.fullmatch(text) else "text"
    return inp


def help_message() -> str:
    return (
        "🔎 **SachScan** checks claims against trusted sources (PIB Fact Check, *.gov.in, WHO, fact-checkers) "
        "and replies with an evidence-backed verdict.\n\n"
        "• `/check` with text, a link or a file (add `public:True` to post the result for everyone)\n"
        "• Right-click any message → **Apps** → **Check with SachScan**\n"
        "• Admins: `/sachscan enable #channel` flags false claims and scams in that channel (flag only, never deletes)\n"
        "• `/link <code>` connects your Discord to your SachScan account (code from the website: History → Link a chat app)\n\n"
        + common.PRIVACY_NOTICE
    )


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class SachScanBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # privileged: needed to read messages for auto-scan / context menu
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.auto: dict[str, set[int]] = {}  # guild id -> opted-in channel ids

    async def setup_hook(self) -> None:
        stored = await common.list_space_settings(PLATFORM)
        self.auto = {gid: {int(c) for c in s.get("channels", [])} for gid, s in stored.items()}
        log.info("loaded auto-scan settings for %d servers", len(self.auto))

    async def sync_guild(self, guild: discord.abc.Snowflake) -> None:
        # Guild-scoped commands appear instantly (global ones can take up to an hour)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        for guild in self.guilds:
            try:
                await self.sync_guild(guild)
            except discord.HTTPException as e:
                log.warning("command sync failed for a server: %s", e)
        log.info("SachScan Discord bot online in %d servers", len(self.guilds))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.sync_guild(guild)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.guild is None:
            await self.handle_dm(message)
            return
        channels = self.auto.get(str(message.guild.id))
        if not channels or message.channel.id not in channels:
            return
        if not (URL_RE.search(message.content or "") or message.attachments):
            return  # Sentinel only looks at links and attachments
        inp = await build_input(message.content, list(message.attachments), message.author.id, message.id)
        if isinstance(inp, str):
            return
        result, reply = await common.handle_check(inp, bold="**", limit=MAX_MSG - 120)
        if not common.should_flag(result):
            return  # stay quiet unless something is false, outdated or a scam
        notice = "⚠️ **SachScan flagged this message.** It was not removed. Please read before sharing.\n\n"
        try:
            if isinstance(message.channel, discord.Thread):
                await message.reply(notice + reply, mention_author=False, suppress_embeds=True)
            else:
                thread = await message.create_thread(name="SachScan check", auto_archive_duration=1440)
                await thread.send(notice + reply, suppress_embeds=True)
        except discord.HTTPException as e:
            log.warning("could not post auto-scan result: %s", e)

    async def handle_dm(self, message: discord.Message) -> None:
        text = (message.content or "").strip()
        if text.lower() in {"help", "/help", "hi", "hello", "start"}:
            await message.channel.send(help_message())
            return
        code = common.parse_link_command(text)
        if code:
            await message.channel.send(await common.redeem_link_code(PLATFORM, str(message.author.id), code))
            return
        inp = await build_input(text, list(message.attachments), message.author.id, message.id)
        if isinstance(inp, str):
            await message.channel.send(inp)
            return
        async with message.channel.typing():
            _, reply = await common.handle_check(inp, bold="**", limit=MAX_MSG)
        await message.channel.send(reply, suppress_embeds=True)


bot = SachScanBot()


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@bot.tree.command(name="check", description="Fact-check a message, link or file against trusted sources")
@app_commands.describe(text="The message or link to check", attachment="A screenshot, voice note, video or PDF",
                       public="Post the result in the channel for everyone (default: only you see it)")
async def check_cmd(interaction: discord.Interaction, text: Optional[str] = None,
                    attachment: Optional[discord.Attachment] = None, public: bool = False) -> None:
    if not text and not attachment:
        await interaction.response.send_message("Give me some text, a link or a file to check.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=not public, thinking=True)
    inp = await build_input(text or "", [attachment] if attachment else [], interaction.user.id, None)
    if isinstance(inp, str):
        await interaction.followup.send(inp, ephemeral=True)
        return
    _, reply = await common.handle_check(inp, bold="**", limit=MAX_MSG)
    await interaction.followup.send(reply, ephemeral=not public, suppress_embeds=True)


@bot.tree.context_menu(name="Check with SachScan")
async def check_message(interaction: discord.Interaction, message: discord.Message) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    inp = await build_input(message.content, list(message.attachments), interaction.user.id, None)
    if isinstance(inp, str):
        await interaction.followup.send(inp, ephemeral=True)
        return
    _, reply = await common.handle_check(inp, bold="**", limit=MAX_MSG)
    await interaction.followup.send(reply, ephemeral=True, suppress_embeds=True)


@bot.tree.command(name="link", description="Connect your Discord to your SachScan account")
@app_commands.describe(code="6-character code from the SachScan website (History → Link a chat app)")
async def link_cmd(interaction: discord.Interaction, code: str) -> None:
    await interaction.response.defer(ephemeral=True)
    if not re.fullmatch(r"[A-Za-z0-9]{6}", code.strip()):
        await interaction.followup.send("That doesn't look like a 6-character code.", ephemeral=True)
        return
    await interaction.followup.send(await common.redeem_link_code(PLATFORM, str(interaction.user.id), code.strip()), ephemeral=True)


@bot.tree.command(name="help", description="How to use SachScan")
async def help_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(help_message(), ephemeral=True)


# ---- AutoMod Sentinel (opt-in, Manage Server only) --------------------------------

sentinel = app_commands.Group(
    name="sachscan", description="AutoMod Sentinel: flag false claims and scams in chosen channels",
    guild_only=True, default_permissions=discord.Permissions(manage_guild=True),
)


def _is_manager(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


async def _save(interaction: discord.Interaction, gid: str) -> str:
    try:
        await common.save_space_settings(PLATFORM, gid, {"channels": sorted(bot.auto.get(gid, set()))}, str(interaction.user.id))
        return ""
    except Exception:
        log.exception("saving sentinel settings failed")
        return "\n(Couldn't save this setting, so it will reset when the bot restarts.)"


@sentinel.command(name="enable", description="Flag false claims & scams in a channel (flag only, never deletes)")
@app_commands.describe(channel="Channel to watch for links and attachments")
async def sentinel_enable(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not _is_manager(interaction):
        await interaction.response.send_message("Only members with **Manage Server** can change this.", ephemeral=True)
        return
    gid = str(interaction.guild_id)
    bot.auto.setdefault(gid, set()).add(channel.id)
    note = await _save(interaction, gid)
    await interaction.response.send_message(
        f"✅ AutoMod Sentinel is on in {channel.mention}. I'll check new messages with links or attachments and reply in a "
        f"thread only when something is false, outdated or looks like a scam. I never delete messages.{note}", ephemeral=True)


@sentinel.command(name="disable", description="Stop auto-checking a channel (or all channels)")
@app_commands.describe(channel="Channel to stop watching (leave empty for all channels)")
async def sentinel_disable(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    if not _is_manager(interaction):
        await interaction.response.send_message("Only members with **Manage Server** can change this.", ephemeral=True)
        return
    gid = str(interaction.guild_id)
    if channel:
        bot.auto.get(gid, set()).discard(channel.id)
    else:
        bot.auto[gid] = set()
    note = await _save(interaction, gid)
    await interaction.response.send_message(f"AutoMod Sentinel turned off for {channel.mention if channel else 'all channels'}.{note}", ephemeral=True)


@sentinel.command(name="status", description="Show which channels are auto-checked")
async def sentinel_status(interaction: discord.Interaction) -> None:
    chans = bot.auto.get(str(interaction.guild_id), set())
    text = ", ".join(f"<#{c}>" for c in sorted(chans)) if chans else "none"
    await interaction.response.send_message(f"AutoMod Sentinel channels: {text}", ephemeral=True)


bot.tree.add_command(sentinel)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not config.DISCORD_BOT_TOKEN:
        raise SystemExit("Set DISCORD_BOT_TOKEN in backend/.env first (see SETUP.md section 5).")
    bot.run(config.DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
