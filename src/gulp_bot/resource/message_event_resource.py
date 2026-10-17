"""What the bot does with the messages it sees: clips channels, delete-and-replay, "bot get them" and the
AI assistant."""

from __future__ import annotations

import discord

from ..container import Services
from .assistant_chat_resource import answer_if_called


async def on_message(services: Services, message: discord.Message) -> None:
    services.clips_channels.enforce(message)
    services.message_replay.remember(message)
    # On a task of its own: the assistant takes seconds to answer, and a command in the same message
    # shouldn't wait for it.
    services.tasks.spawn(_converse(services, message))


async def _converse(services: Services, message: discord.Message) -> None:
    # "bot get them" is a command, not a question for the assistant.
    if not await services.bot_get_them.handle(message):
        await answer_if_called(services, message)


async def on_message_deleted(services: Services, payload: discord.RawMessageDeleteEvent) -> None:
    await services.message_replay.replay_if_self_deleted(payload.message_id, payload.guild_id)


def on_messages_bulk_deleted(services: Services, payload: discord.RawBulkMessageDeleteEvent) -> None:
    # A bulk delete always needs Manage Messages and is always logged, so it is a moderator's: never replayed.
    services.message_replay.forget(list(payload.message_ids))
