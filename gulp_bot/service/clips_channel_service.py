"""Clips channels: only messages with a video stay."""

from __future__ import annotations

import asyncio
import logging

import discord

from ..core.background_tasks import BackgroundTasks
from .guild_settings_service import GuildSettingsService

log = logging.getLogger(__name__)

# Discord unfurls link embeds (YouTube, Streamable, Twitch clips, etc.) asynchronously after the message is
# sent, so a message with no video yet isn't necessarily invalid. In seconds.
EMBED_GRACE_PERIOD = 6.0


def has_video(message: discord.Message) -> bool:
    # An embed's `video` is empty (falsy) unless the embed has one.
    return any((attachment.content_type or "").startswith("video/") for attachment in message.attachments) or any(bool(embed.video) for embed in message.embeds)


class ClipsChannelService:
    def __init__(self, guild_settings: GuildSettingsService, tasks: BackgroundTasks):
        self._guild_settings = guild_settings
        self._tasks = tasks

    def enforce(self, message: discord.Message) -> None:
        """Deletes a message posted in a clips channel that doesn't contain a video, giving Discord a moment
        to unfurl link embeds before making a final decision."""
        if message.author.bot or message.guild is None:
            return
        if message.channel.id not in self._guild_settings.get(message.guild.id).clips_channel_ids:
            return
        if has_video(message):
            return
        self._tasks.spawn(self._delete_unless_video(message))

    async def _delete_unless_video(self, message: discord.Message) -> None:
        await asyncio.sleep(EMBED_GRACE_PERIOD)
        try:
            message = await message.channel.fetch_message(message.id)
        except discord.HTTPException:
            return  # Already deleted, or we lost access - nothing to do.
        if has_video(message):
            return

        try:
            await message.delete()
        except discord.HTTPException as e:
            log.error("Failed to delete non-video message %s in clips channel %s: %s", message.id, message.channel.id, e)
            return
        log.info("Deleted non-video message from %s in clips channel %s", message.author.name, message.channel.id)
        await self._tell_author_why(message)

    async def _tell_author_why(self, message: discord.Message) -> None:
        """DMs the author to let them know why their message was removed."""
        channel_id = message.channel.id
        content = (
            f"Your message in <#{channel_id}> was removed because it didn't include a video or clip. "
            "That channel only allows messages with a video attachment or a video link (e.g. YouTube, Streamable, Twitch clip)."
        )
        try:
            await message.author.send(content)
        except discord.HTTPException as e:
            log.warning("Failed to DM %s about their deleted message in %s: %s", message.author.name, channel_id, e)
