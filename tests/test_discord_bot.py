"""Discord bot: input conversion and the auto-scan 'only flag when needed' rule (no Discord connection)."""
import asyncio

from backend import config
from backend.channels import common
from backend.pipeline.schemas import CheckResponse, ClaimResult
from bots import discord_bot


class FakeAttachment:
    def __init__(self, filename, content_type, size, data=b"bytes"):
        self.filename, self.content_type, self.size, self._data = filename, content_type, size, data

    async def read(self):
        return self._data


def run(c):
    return asyncio.run(c)


def test_text_and_url_inputs():
    inp = run(discord_bot.build_input("Hot water cures COVID", [], 42, 99))
    assert inp.platform == "discord" and inp.content_type == "text" and inp.external_user_id == "42"
    assert inp.original_message_id == "discord:99"
    assert run(discord_bot.build_input("https://example.com/news", [], 42, None)).content_type == "url"


def test_attachment_inputs_and_limits():
    img = run(discord_bot.build_input("caption", [FakeAttachment("a.png", "image/png", 1000, b"PNG")], 1, 2))
    assert img.content_type == "image" and img.file_bytes == b"PNG" and img.text == "caption"
    big_pdf = run(discord_bot.build_input("", [FakeAttachment("b.pdf", "application/pdf", (config.MAX_PDF_MB + 1) * 1024 * 1024)], 1, 2))
    assert isinstance(big_pdf, str) and f"{config.MAX_PDF_MB} MB" in big_pdf
    unsupported = run(discord_bot.build_input("", [FakeAttachment("x.exe", "application/x-msdownload", 10)], 1, 2))
    assert isinstance(unsupported, str) and "isn't supported" in unsupported
    # unsupported file but text present -> check the text
    assert run(discord_bot.build_input("check this", [FakeAttachment("x.exe", "application/x-msdownload", 10)], 1, 2)).text == "check this"


def test_sentinel_only_flags_false_outdated_or_scams():
    ok = CheckResponse(overall_verdict="verified", claims=[ClaimResult(claim="a", verdict="verified")])
    unsure = CheckResponse(overall_verdict="cannot_be_confirmed", claims=[ClaimResult(claim="a", verdict="cannot_be_confirmed")])
    false = CheckResponse(overall_verdict="partly_supported", claims=[ClaimResult(claim="a", verdict="false"), ClaimResult(claim="b", verdict="verified")])
    scam = CheckResponse(overall_verdict="cannot_be_confirmed", scam_signals=["Asks for OTP"])
    outdated = CheckResponse(overall_verdict="outdated", claims=[ClaimResult(claim="a", verdict="outdated")])
    assert not common.should_flag(ok) and not common.should_flag(unsure) and not common.should_flag(None)
    assert common.should_flag(false) and common.should_flag(scam) and common.should_flag(outdated)


def test_commands_registered():
    names = {c.name for c in discord_bot.bot.tree.get_commands()}
    assert {"check", "link", "help", "sachscan", "Check with SachScan"} <= names
    group = discord_bot.bot.tree.get_command("sachscan")
    assert {c.name for c in group.commands} == {"enable", "disable", "status"}
    assert group.default_permissions.manage_guild  # only Manage Server sees/uses it by default
