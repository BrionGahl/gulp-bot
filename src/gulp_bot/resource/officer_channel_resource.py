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
from ..core.schedule_parsing import now_in_new_york
from ..exception.ai_exception import AiError
from ..mapper.absence_mapper import absence_text, confirmation
from ..schema.absence_schema import NewAbsence, NewChannelNote, OfficerChannelOwner
from ..service.officer_channel_triage_service import RESPONSE_SCHEMA, SYSTEM_PROMPT, TEMPERATURE, read_detection, triage_request, worth_asking
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
    """Has the model read `message`, and records and answers what it found."""
    text = message.content
    if not worth_asking(text):
        return
    settings = services.guild_settings.get(owner.guild_id)
    today = now_in_new_york().date()
    try:
        answer, _ = await services.ai.generate(settings.ai, SYSTEM_PROMPT, triage_request(text, today, settings.raid_nights), TEMPERATURE, RESPONSE_SCHEMA)
    except AiError as e:
        log.info("Didn't read message %s in a personal officer channel: %s", message.id, getattr(e, "detail", str(e)))
        return
    detection = read_detection(answer, today, settings.raid_nights)

    if detection.notes:
        notes = [NewChannelNote(owner.guild_id, owner.user_id, message.channel.id, message.id, category, summary) for category, summary in detection.notes]
        await services.absences.add_notes(owner.guild_id, notes)
        log.info("Noted %d topic(s) from message %s of %s for the officers", len(notes), message.id, owner.user_id)

    found = detection.absence
    try:
        if found is not None:
            stored = await services.absences.add(
                NewAbsence(owner.guild_id, owner.user_id, found.start, found.end, found.kind, found.note, owner.user_id, source_message_id=message.id)
            )
            nights = services.absences.nights_covered(owner.guild_id, found.start, found.end)
            text = confirmation(stored, nights) if nights else f"Noted: {absence_text(stored)}."
            await message.reply(text, view=undo_view(stored.id), allowed_mentions=NO_MENTIONS)
        elif detection.unclear_absence:
            await message.reply(UNCLEAR, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException as e:
        log.warning("Couldn't reply in personal officer channel %s: %s", message.channel.id, e)
