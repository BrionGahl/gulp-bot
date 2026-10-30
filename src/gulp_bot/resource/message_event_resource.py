"""What the bot does with the messages it sees: clips channels, delete-and-replay, "bot get them", and reading
personal officer channels (`officer_channel_resource`)."""

from __future__ import annotations

import discord

from ..container import Services
from . import officer_channel_resource


async def on_message(services: Services, message: discord.Message) -> None:
    services.clips_channels.enforce(message)
    services.message_replay.remember(message)
    # On a task of its own: deleting a message waits on Discord, and a command in the same message shouldn't.
    services.tasks.spawn(services.bot_get_them.handle(message))
    # The model takes seconds; nothing else waits for it.
    services.tasks.spawn(officer_channel_resource.on_message(services, message))


def on_message_edited(services: Services, payload: discord.RawMessageUpdateEvent) -> None:
    services.tasks.spawn(officer_channel_resource.on_message_edited(services, payload))


async def on_message_deleted(services: Services, payload: discord.RawMessageDeleteEvent) -> None:
    await services.message_replay.replay_if_self_deleted(payload.message_id, payload.guild_id)
    await officer_channel_resource.on_message_deleted(services, payload)


def on_messages_bulk_deleted(services: Services, payload: discord.RawBulkMessageDeleteEvent) -> None:
    # A bulk delete always needs Manage Messages and is always logged, so it is a moderator's: never replayed.
    services.message_replay.forget(list(payload.message_ids))
