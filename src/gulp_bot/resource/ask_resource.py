"""`/ask`: a question for the guild's AI assistant."""

from __future__ import annotations

import discord
from discord import app_commands

from ..core.discord_context import slash_context
from ..core.discord_helpers import role_ids
from ..core.permission_checks import is_administrator
from ..exception.ai_exception import AskError
from ..schema.ai_schema import Question
from ..service.ai_access_service import access_for_member, ensure_switches_allow
from ..service.ai_prompt_service import speaker_label
from .resource_support import meta, refuse, reply_in_chunks


@app_commands.command(name="ask", description="Ask the guild's AI assistant anything")
@app_commands.guild_only()
@app_commands.describe(question="What do you want to ask?")
async def ask(interaction: discord.Interaction, question: str) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    ai = ctx.services.ai

    # The same roles the other raider-only commands go by, looked up for this server only. Administrators
    # count as Officers, as they do for `/settings` and `/wcl`.
    settings = ctx.services.guild_settings.get(guild_id)
    access = access_for_member(settings, role_ids(ctx.author), is_administrator(ctx))
    if not access.is_allowed():
        if settings.raider_role_id is None and settings.mod_role_id is None:
            await refuse(ctx, "No raider role is registered for this server. An administrator or moderator can set one with `/settings`.")
        else:
            await refuse(ctx, "This command is only available to raiders.")
        return
    # Off, or in maintenance: told privately, before the public "thinking..." of the defer.
    try:
        ensure_switches_allow(settings.ai, access)
    except AskError as e:
        await refuse(ctx, str(e))
        return

    # The model takes a few seconds, longer than Discord waits for a first response.
    await ctx.defer()
    speaker = speaker_label(ctx.author.display_name, ctx.author.name)
    try:
        answer, answered = await ai.ask(ctx.channel.id, ctx.author.id, access, settings.ai, Question(speaker, question)), True
    except AskError as e:
        answer, answered = str(e), False

    # Nobody is pinged, whatever the answer says.
    for sent in await reply_in_chunks(ctx, answer):
        # A reply to any part of the answer carries the conversation on, as with "Bot please".
        if answered:
            ai.remember_answer(sent.id)


meta(
    ask,
    category="AI",
    help_text=(
        'Only for raiders. The assistant remembers the last few questions asked in the same channel, and answers messages that say "Bot please" or "hey bot" '
        "the same way. Reply to one of its answers to carry the conversation on."
    ),
)

COMMANDS = [ask]
