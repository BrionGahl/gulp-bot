"""What the bot does with the messages it sees: clips channels, delete-and-replay, "bot get them", the AI
assistant, and the spontaneous reactions (`reactions_service`)."""

from __future__ import annotations

import logging
import time

import discord

from ..container import Services
from ..core.discord_helpers import NO_MENTIONS
from .assistant_chat_resource import answer_if_called, talks_to_assistant

log = logging.getLogger(__name__)


async def on_message(services: Services, message: discord.Message) -> None:
    services.clips_channels.enforce(message)
    services.message_replay.remember(message)
    # On a task of its own: the assistant takes seconds to answer, and a command in the same message
    # shouldn't wait for it.
    services.tasks.spawn(_converse(services, message))
    _react(services, message)


async def _converse(services: Services, message: discord.Message) -> None:
    # "bot get them" is a command, not a question for the assistant.
    if not await services.bot_get_them.handle(message):
        await answer_if_called(services, message)


def _react(services: Services, message: discord.Message) -> None:
    """The bot's spontaneous reaction to the message, if one goes off: at most one. Chat with the AI assistant
    is left alone, and so are clips channels, where only videos may stay: both set nothing off and count
    towards no cooldown."""
    if message.guild is None or message.author.bot or talks_to_assistant(services, message):
        return
    if message.channel.id in services.guild_settings.get(message.guild.id).clips_channel_ids:
        return
    author = message.author
    names = [author.name, author.global_name, getattr(author, "nick", None)]
    response = services.reactions.response_for(message.guild.id, author.id, names, message.content, time.monotonic())
    if response is not None:
        services.tasks.spawn(_send(message, response.text, response.reply))


async def _send(message: discord.Message, text: str, reply: bool) -> None:
    """Says `text` in the channel of `message`, as a reply to it or a plain message. It pings nobody, not even
    the author of the message replied to."""
    try:
        if reply:
            await message.reply(text, allowed_mentions=NO_MENTIONS)
        else:
            await message.channel.send(text, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException as e:
        log.warning("Couldn't say %r in channel %s: %s", text, message.channel.id, e)


async def on_message_deleted(services: Services, payload: discord.RawMessageDeleteEvent) -> None:
    await services.message_replay.replay_if_self_deleted(payload.message_id, payload.guild_id)


def on_messages_bulk_deleted(services: Services, payload: discord.RawBulkMessageDeleteEvent) -> None:
    # A bulk delete always needs Manage Messages and is always logged, so it is a moderator's: never replayed.
    services.message_replay.forget(list(payload.message_ids))
