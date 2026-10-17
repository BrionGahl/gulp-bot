""" "bot get them": an Officer replies to a message with these words, and the bot deletes the message they
replied to and barks.

An Officer is who the assistant takes for one: a holder of the moderator role, a server administrator, or a
Developer (`ai_access_service`). It works whether or not the AI assistant is set up or switched on: it is a
command, not a question. The bot needs the Manage Messages permission in the channel to delete someone
else's message.
"""

from __future__ import annotations

import logging

import discord

from ..core.discord_helpers import NO_MENTIONS, member_is_administrator, role_ids
from .ai_access_service import access_for_member
from .chat_trigger_service import mentions_bot_get_them
from .guild_settings_service import GuildSettingsService
from .message_replay_service import MessageReplayService

log = logging.getLogger(__name__)

# What the bot says when it has got them.
BARK = "arf! arf!"

# What it says when it couldn't delete the message.
MISSED = "grr... can't reach that one. i need the Manage Messages permission here."


def replied_message(message: discord.Message) -> discord.Message | None:
    """The message `message` replies to, if Discord sent it along (it isn't there if it was deleted)."""
    reference = message.reference
    if reference is None or not isinstance(reference.resolved, discord.Message):
        return None
    return reference.resolved


class BotGetThemService:
    def __init__(self, guild_settings: GuildSettingsService, message_replay: MessageReplayService):
        self._guild_settings = guild_settings
        self._message_replay = message_replay

    async def handle(self, message: discord.Message) -> bool:
        """Handles `message` if it is "bot get them" in reply to another message: deletes that message and
        barks, if the author is an Officer. Returns whether `message` was the command, whoever sent it, so
        that it isn't also taken as a question for the assistant. Anyone else gets no answer at all, as with
        "Bot please" from someone the assistant doesn't talk to."""
        if message.guild is None or message.author.bot or not mentions_bot_get_them(message.content):
            return False
        # Without a reply there is nobody to get.
        target = replied_message(message)
        if target is None:
            return False

        access = access_for_member(self._guild_settings.get(message.guild.id), role_ids(message.author), member_is_administrator(message.author))
        if not access.is_officer():
            return True

        # Out of the replay buffer first: a message the bot deleted on an Officer's say-so must not be
        # reposted as if its author had deleted it.
        self._message_replay.forget([target.id])

        try:
            await target.delete()
        except discord.HTTPException as e:
            log.error('Failed to delete message %s in channel %s for "bot get them": %s', target.id, message.channel.id, e)
            await _bark(message, MISSED)
            return True

        log.info(
            'audit: %s (%s) had "bot get them" delete message %s by %s (%s) in channel %s of guild %s',
            message.author.id,
            message.author.name,
            target.id,
            target.author.id,
            target.author.name,
            message.channel.id,
            message.guild.id,
        )
        await _bark(message, BARK)
        return True


async def _bark(message: discord.Message, text: str) -> None:
    """Says `text` in reply to the command, pinging nobody."""
    try:
        await message.channel.send(text, reference=message, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException as e:
        log.error("Failed to bark in channel %s: %s", message.channel.id, e)
