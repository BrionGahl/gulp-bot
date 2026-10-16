"""Answers messages that say "Bot please", and replies to the assistant's answers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from ..checks import member_is_administrator
from ..helper import NO_MENTIONS, role_ids
from . import AskError, Question, access_for
from .text import DISCORD_MAX_CHARS, has_trigger, speaker_label, split_message

if TYPE_CHECKING:
    from ..bot import GulpBot

log = logging.getLogger(__name__)


def replied_message(message: discord.Message) -> discord.Message | None:
    """The message `message` replies to, if Discord sent it along (it isn't there if it was deleted)."""
    reference = message.reference
    if reference is None or not isinstance(reference.resolved, discord.Message):
        return None
    return reference.resolved


async def answer_trigger(bot: GulpBot, message: discord.Message) -> None:
    """Answers `message` in its channel if it says "Bot please", or if it is a reply to one of the
    assistant's answers (which carries the conversation on). If `message` replies to a message, that
    message goes along with the question, so "Bot please, what does he mean?" has something to go on.

    Does nothing if the assistant isn't set up, for a bot's message (this also keeps the bot from
    answering itself), outside a server, or for a member who isn't a raider: they get no answer at all,
    not even a refusal, so that the channel isn't filled with replies to people the assistant doesn't
    talk to. A raider who calls it while the developers have it off, or in maintenance, is told so."""
    ai = bot.ai
    if ai is None or message.guild is None or message.author.bot:
        return
    replied = replied_message(message)
    replies_to_answer = replied is not None and ai.is_answer(replied.id)
    if not replies_to_answer and not has_trigger(message.content):
        return

    settings = bot.guild_settings.get(message.guild.id)
    access = access_for(settings, role_ids(message.author), member_is_administrator(message.author))
    if not access.is_allowed():
        return

    # Mentions as names, and no live @everyone, in what the model sees.
    speaker = speaker_label(message.author.display_name, message.author.name)

    # The message replied to, named as the model should know its author. The member can see it in the
    # channel already, so it tells the model nothing they couldn't read themselves.
    quote = None
    if replied is not None:
        author = "you" if replies_to_answer else speaker_label(replied.author.global_name or replied.author.name, replied.author.name)
        quote = (author, replied.clean_content)
    question = Question(speaker, message.clean_content, quote)

    # Shown until the answer has been sent.
    async with message.channel.typing():
        try:
            reply, answered = await ai.ask(message.channel.id, message.author.id, access, settings.ai, question), True
        except AskError as e:
            reply, answered = str(e), False

        for index, chunk in enumerate(split_message(reply, DISCORD_MAX_CHARS)):
            try:
                # The answer is shown as a reply to the question; nobody is pinged, whatever it says.
                sent = await message.channel.send(chunk, reference=message if index == 0 else None, allowed_mentions=NO_MENTIONS)  # type: ignore[arg-type]
            except discord.HTTPException as e:
                log.error("Failed to send the AI answer in channel %s: %s", message.channel.id, e)
                return
            # A reply to any part of an answer carries the conversation on; a refusal doesn't.
            if answered:
                ai.remember_answer(sent.id)
