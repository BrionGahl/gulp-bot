"""What the bot does with messages in personal officer channels: reads the member's own messages for an
absence from raid nights and anything else the officers should know (`officer_channel_triage_service`), and
records them for the week's officer digest (`absence_service`).

A recorded absence gets a reply with an Undo button for the member or an Officer. An absence the model saw
but couldn't place gets a reply pointing to `/absence add`. Notes get no reply: the officers see them in the
digest. An edited message is read again, and a deleted one takes what was read from it along.
"""

from __future__ import annotations

import logging

import discord

from ..container import Services
from ..core.discord_helpers import NO_MENTIONS
from ..mapper.absence_mapper import absence_text, confirmation
from ..schema.absence_schema import OfficerChannelOwner
from .absence_resource import undo_view

log = logging.getLogger(__name__)

UNCLEAR = "Sounds like you might miss a raid, but I couldn't tell which day. Report it with `/absence add` so the officers have it."


async def _owner_message(services: Services, message: discord.Message) -> OfficerChannelOwner | None:
    """The owner of the channel `message` is in, if it is a personal officer channel and they wrote it."""
    if message.guild is None or message.author.bot:
        return None
    owner = await services.officer_channels.owner_of(message.channel.id)
    return owner if owner is not None and owner.user_id == message.author.id else None


async def on_message(services: Services, message: discord.Message) -> None:
    owner = await _owner_message(services, message)
    if owner is not None:
        await read(services, message, owner)


async def on_message_edited(services: Services, payload: discord.RawMessageUpdateEvent) -> None:
    """Reads an edited message again, in place of what was read from it before. An edit that didn't change
    the text (Discord sends one when a link preview loads) changes nothing."""
    owner = await _owner_message(services, payload.message)
    if owner is None:
        return
    if payload.cached_message is not None and payload.cached_message.content == payload.message.content:
        return
    await services.absences.forget_message(owner.guild_id, payload.message_id)
    await read(services, payload.message, owner)


async def on_message_deleted(services: Services, payload: discord.RawMessageDeleteEvent) -> None:
    owner = await services.officer_channels.owner_of(payload.channel_id)
    if owner is not None:
        await services.absences.forget_message(owner.guild_id, payload.message_id)


async def read(services: Services, message: discord.Message, owner: OfficerChannelOwner) -> None:
    """Has `message` read (`TriageService`), and answers what was recorded from it."""
    outcome = await services.triage.read(owner, message.channel.id, message.id, message.content)
    if outcome is None:
        return
    try:
        if outcome.absence is not None:
            text = confirmation(outcome.absence, outcome.nights) if outcome.nights else f"Noted: {absence_text(outcome.absence)}."
            await message.reply(text, view=undo_view(outcome.absence.id), allowed_mentions=NO_MENTIONS)
        elif outcome.unclear_absence:
            await message.reply(UNCLEAR, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException as e:
        log.warning("Couldn't reply in personal officer channel %s: %s", message.channel.id, e)
