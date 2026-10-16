"""Announcements scheduled by a guild's officers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..mapper.announcement_mapper import render_announcement
from ..repository.announcement_repository import AnnouncementRepository
from ..schema.announcement_schema import Announcement, NewAnnouncement
from .guild_settings_service import GuildSettingsService

if TYPE_CHECKING:
    import discord

log = logging.getLogger(__name__)

# Most announcements one server can have scheduled, to keep the list readable and bound how much one server
# can make the bot post.
MAX_ANNOUNCEMENTS_PER_GUILD = 25


class AnnouncementService:
    def __init__(self, repository: AnnouncementRepository, guild_settings: GuildSettingsService, discord_client: discord.Client):
        self._repository = repository
        self._guild_settings = guild_settings
        self._discord = discord_client

    async def has_room_for_another(self, guild_id: int) -> bool:
        """Whether the guild may schedule one more announcement."""
        return await self._repository.count_for_guild(guild_id) < MAX_ANNOUNCEMENTS_PER_GUILD

    async def schedule(self, announcement: NewAnnouncement) -> int:
        """Stores a new announcement and returns its ID."""
        return await self._repository.insert(announcement)

    async def list_for_guild(self, guild_id: int) -> list[Announcement]:
        """`guild_id`'s announcements, oldest first."""
        return await self._repository.list_for_guild(guild_id)

    async def find(self, guild_id: int, announcement_id: int) -> Announcement | None:
        """One announcement, but only if it belongs to `guild_id`."""
        return await self._repository.find(guild_id, announcement_id)

    async def delete(self, guild_id: int, announcement_id: int) -> bool:
        """Deletes an announcement of `guild_id`. Returns whether it existed there."""
        return await self._repository.delete(guild_id, announcement_id)

    async def list_all(self) -> list[Announcement]:
        """Every guild's announcements, for the scheduler."""
        return await self._repository.list_all()

    async def post(self, announcement: Announcement) -> None:
        """Posts `announcement` to its channel now, with `@Raider`/`@Trial` as mentions of the roles its
        guild has registered."""
        settings = self._guild_settings.get(announcement.guild_id)
        channel = self._discord.get_partial_messageable(announcement.channel_id, guild_id=announcement.guild_id)
        await channel.send(render_announcement(announcement.message, settings))
        log.info("Posted announcement %s for guild %s", announcement.id, announcement.guild_id)
