"""`/tldr`: a funny summary of the channel's recent chat."""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands

from ..core.discord_context import GulpContext, slash_context
from ..core.discord_helpers import NO_MENTIONS, role_ids
from ..core.permission_checks import is_administrator
from ..exception.ai_exception import AskError
from ..schema.ai_schema import Access, TldrBusy, TldrWait, TranscriptLine
from ..schema.guild_settings_schema import GuildSettings
from ..service.ai_access_service import access_for_member, ensure_switches_allow
from ..service.ai_assistant_service import AiAssistantService
from ..service.ai_prompt_service import speaker_label
from ..service.tldr_service import WINDOW_SECS, central_time, conversation_to_summarize, describe_span, transcript
from .ask_resource import NOT_SET_UP
from .resource_support import meta, refuse, reply_in_chunks

log = logging.getLogger(__name__)

# Messages are read back 100 at a time (Discord's most), at most this many times: 500 messages.
MAX_PAGES = 5
PAGE_SIZE = 100


@app_commands.command(name="tldr", description="Sum up this channel's recent chat, with jokes")
@app_commands.guild_only()
async def tldr(interaction: discord.Interaction) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    ai = ctx.services.ai
    if ai is None:
        await refuse(ctx, NOT_SET_UP)
        return

    # The same access as `/ask`: raiders, Officers and Developers.
    settings = ctx.services.guild_settings.get(guild_id)
    access = access_for_member(settings, role_ids(ctx.author), is_administrator(ctx))
    if not access.is_allowed():
        await refuse(ctx, "This command is only available to raiders.")
        return
    try:
        ensure_switches_allow(settings.ai, access)
    except AskError as e:
        await refuse(ctx, str(e))
        return

    channel = ctx.channel.id
    cooldown = ai.start_tldr(channel)
    if isinstance(cooldown, TldrBusy):
        await refuse(ctx, "Already writing one for this channel, hang on.")
        return
    if isinstance(cooldown, TldrWait):
        next_one = int(time.time()) + int(cooldown.remaining)
        await refuse(ctx, f"Already did one <t:{cooldown.posted_unix}:R>: {cooldown.link}\nNext one <t:{next_one}:R>.")
        return

    # The channel is held from here: unless a summary gets posted, let it go again.
    posted = False
    try:
        posted = await post_summary(ctx, ai, settings, access)
    finally:
        if not posted:
            ai.cancel_tldr(channel)


meta(
    tldr,
    category="AI",
    help_text="Reads back up to 4 hours, stopping at the first pause of an hour or more. One per channel per hour: until then, it points to the last one. Only for raiders.",
)


async def post_summary(ctx: GulpContext, ai: AiAssistantService, settings: GuildSettings, access: Access) -> bool:
    """Reads the channel back, has the conversation summed up and posts it. Returns whether a summary was
    posted."""
    # Reading the channel and the model both take longer than Discord waits for a first response.
    await ctx.defer()

    now = int(time.time())
    try:
        newest_first = await read_back(ctx, now)
    except discord.HTTPException as e:
        log.error("Failed to read the history of channel %s for /tldr: %s", ctx.channel.id, e)
        await ctx.send("Can't read this channel's history. I need the Read Message History permission here.", allowed_mentions=NO_MENTIONS)
        return False
    lines = conversation_to_summarize(newest_first, now)
    if not lines:
        await ctx.send("Nothing to sum up: nobody has said anything here in the last 4 hours.", allowed_mentions=NO_MENTIONS)
        return False

    text, omitted = transcript(lines)
    try:
        summary = await ai.summarize(access, settings.ai, text, omitted)
    except AskError as e:
        await ctx.send(str(e), allowed_mentions=NO_MENTIONS)
        return False

    channel_link = f"https://discord.com/channels/{ctx.guild_id or 0}/{ctx.channel.id}"
    sent = await reply_in_chunks(ctx, f"**tl;dr** of the last {describe_span(now - lines[0].at)} ({len(lines)} messages)\n{summary}")
    for message in sent:
        # A reply to the summary carries on with the assistant, as for its other answers.
        ai.remember_answer(message.id)
    link = f"{channel_link}/{sent[0].id}" if sent else channel_link

    ai.finish_tldr(ctx.channel.id, link, now)
    log.info("%s (%s) had %d messages of channel %s summed up", ctx.author.id, ctx.author.name, len(lines), ctx.channel.id)
    return True


async def read_back(ctx: GulpContext, now: int) -> list[TranscriptLine]:
    """The channel's messages of about the last `WINDOW_SECS`, the newest first, as transcript lines. Bots'
    messages are left out, and so are messages with neither text nor a file."""
    lines: list[TranscriptLine] = []
    before: discord.abc.Snowflake | None = None

    for _ in range(MAX_PAGES):
        page = [message async for message in ctx.channel.history(limit=PAGE_SIZE, before=before)]
        if not page:
            break
        oldest = page[-1]
        before = oldest
        # A short page is the start of the channel; one reaching past the window is enough.
        done = len(page) < PAGE_SIZE or now - int(oldest.created_at.timestamp()) > WINDOW_SECS

        for message in page:
            if message.author.bot:
                continue
            # Mentions as names, and no live @everyone, in what the model sees.
            text = message.clean_content
            if not text.strip():
                if not message.attachments:
                    continue
                text = "[attachment]"
            at = int(message.created_at.timestamp())
            lines.append(TranscriptLine(at, central_time(at), speaker_label(message.author.global_name or message.author.name, message.author.name), text))

        if done:
            break
    return lines


COMMANDS = [tldr]
