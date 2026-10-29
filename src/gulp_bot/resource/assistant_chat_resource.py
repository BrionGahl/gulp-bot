"""The AI assistant in the channels: answers messages that say "Bot please" or "hey bot", and replies to its
answers."""

from __future__ import annotations

import logging

import discord

from ..container import Services
from ..core.discord_helpers import NO_MENTIONS, member_is_administrator, role_ids
from ..core.text import split_message
from ..exception.ai_exception import AskError
from ..schema.ai_schema import Question
from ..service.ai_access_service import access_for_member
from ..service.ai_prompt_service import speaker_label
from ..service.bot_get_them_service import replied_message
from ..service.chat_trigger_service import calls_assistant

log = logging.getLogger(__name__)


async def answer_if_called(services: Services, message: discord.Message) -> None:
    """Answers `message` in its channel if it says "Bot please" or "hey bot", or if it is a reply to one of the
    assistant's answers (which carries the conversation on). If `message` replies to a message, that message
    goes along with the question, so "Bot please, what does he mean?" has something to go on.

    Does nothing for a bot's message (this also keeps the bot from answering
    itself), outside a server, or for a member who isn't a raider: they get no answer at all, not even a
    refusal, so that the channel isn't filled with replies to people the assistant doesn't talk to. A raider
    who calls it while the developers have it off, or in maintenance, is told so."""
    ai = services.ai
    if message.guild is None or message.author.bot:
        return
    replied = replied_message(message)
    replies_to_answer = replied is not None and ai.is_answer(replied.id)
    if not replies_to_answer and not calls_assistant(message.content):
        return

    settings = services.guild_settings.get(message.guild.id)
    access = access_for_member(settings, role_ids(message.author), member_is_administrator(message.author))
    if not access.is_allowed():
        return

    question = Question(speaker_label(message.author.display_name, message.author.name), message.clean_content, _quote(replied, replies_to_answer))

    # Shown until the answer has been sent.
    async with message.channel.typing():
        try:
            answer, answered = await ai.ask(message.channel.id, message.author.id, access, settings.ai, question), True
        except AskError as e:
            answer, answered = str(e), False

        for index, chunk in enumerate(split_message(answer)):
            try:
                # The answer is shown as a reply to the question; nobody is pinged, whatever it says.
                sent = await message.channel.send(chunk, reference=message if index == 0 else None, allowed_mentions=NO_MENTIONS)  # type: ignore[arg-type]
            except discord.HTTPException as e:
                log.error("Failed to send the AI answer in channel %s: %s", message.channel.id, e)
                return
            # A reply to any part of an answer carries the conversation on; a refusal doesn't.
            if answered:
                ai.remember_answer(sent.id)


def _quote(replied: discord.Message | None, replies_to_answer: bool) -> tuple[str, str] | None:
    """The message replied to, named as the model should know its author. The member can see it in the
    channel already, so it tells the model nothing they couldn't read themselves."""
    if replied is None:
        return None
    author = "you" if replies_to_answer else speaker_label(replied.author.global_name or replied.author.name, replied.author.name)
    return author, replied.clean_content
