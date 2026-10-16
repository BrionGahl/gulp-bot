"""`/ask`: a question for the guild's AI assistant."""

import discord
from discord import app_commands

from ..ai import AskError, Question, access_for, gate
from ..ai.text import DISCORD_MAX_CHARS, speaker_label, split_message
from ..checks import is_administrator
from ..context import slash_context
from ..helper import NO_MENTIONS, role_ids
from .base import meta, refuse


@app_commands.command(name="ask", description="Ask the guild's AI assistant anything")
@app_commands.guild_only()
@app_commands.describe(question="What do you want to ask?")
async def ask(interaction: discord.Interaction, question: str) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    ai = ctx.bot.ai
    if ai is None:
        await refuse(ctx, "The AI assistant isn't set up for this bot yet.")
        return

    # The same roles the other raider-only commands go by, looked up for this server only.
    # Administrators count as Officers, as they do for `/settings` and `/wcl`.
    settings = ctx.bot.guild_settings.get(guild_id)
    access = access_for(settings, role_ids(ctx.author), is_administrator(ctx))
    if not access.is_allowed():
        if settings.raider_role_id is None and settings.mod_role_id is None:
            reason = "No raider role is registered for this server. An administrator or moderator can set one with `/settings`."
        else:
            reason = "This command is only available to raiders."
        await refuse(ctx, reason)
        return
    # Off, or in maintenance: told privately, before the public "thinking..." of the defer.
    try:
        gate(settings.ai, access)
    except AskError as e:
        await refuse(ctx, str(e))
        return

    # The model takes a few seconds, longer than Discord waits for a first response.
    await ctx.defer()

    speaker = speaker_label(ctx.author.display_name, ctx.author.name)
    try:
        reply, answered = await ai.ask(ctx.channel.id, ctx.author.id, access, settings.ai, Question(speaker, question)), True
    except AskError as e:
        reply, answered = str(e), False

    # Nobody is pinged, whatever the answer says.
    for chunk in split_message(reply, DISCORD_MAX_CHARS):
        sent = await ctx.send(chunk, allowed_mentions=NO_MENTIONS)
        # A reply to any part of the answer carries the conversation on, as with "Bot please".
        if answered:
            ai.remember_answer(sent.id)


meta(
    ask,
    category="AI",
    help_text=(
        'Only for raiders. The assistant remembers the last few questions asked in the same channel, and answers messages that say "Bot please" '
        "the same way. Reply to one of its answers to carry the conversation on."
    ),
)
