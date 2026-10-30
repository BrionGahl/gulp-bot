"""The weekly raid notes forum thread: one per guild per raid week."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

import discord

from ..core.cron import Schedule
from ..core.raid_week import week_end, week_start
from ..repository.raid_notes_post_repository import RaidNotesPostRepository
from ..schema.guild_settings_schema import GuildSettings
from ..schema.raid_notes_schema import WeekThread

log = logging.getLogger(__name__)

# When the post is created for a guild that hasn't registered its own with `/settings raid_notes_schedule`:
# Fridays at 9:00 AM.
DEFAULT_RAID_NOTES_SCHEDULE = Schedule.parse("0 0 9 * * Fri *")

# The first message of a week's thread.
STARTER_MESSAGE = "Raid notes for this week go here."


def week_title(day: date) -> str:
    tuesday = week_start(day)
    monday = week_end(tuesday)
    return f"Week of {tuesday:%b} {tuesday.day} - {monday:%b} {monday.day}"


def raid_notes_schedule(settings: GuildSettings) -> Schedule:
    """When the guild's weekly thread is created: its own schedule, or the default."""
    return settings.raid_notes_cron or DEFAULT_RAID_NOTES_SCHEDULE


class RaidNotesService:
    def __init__(self, repository: RaidNotesPostRepository, discord_client: discord.Client):
        self._repository = repository
        self._discord = discord_client
        # Serializes `week_thread` so that the raid notes and attendance schedules, both wanting the same
        # week's thread at the same moment, can't each create one. Enough on its own because a single bot
        # process owns the database.
        self._lock = asyncio.Lock()

    async def week_thread(self, guild_id: int, forum_channel_id: int, day: date | datetime) -> WeekThread:
        """The raid notes forum thread for the week containing `day`, created (with a placeholder starter
        message) in `forum_channel_id` and recorded if the bot hasn't made one for that week yet."""
        if isinstance(day, datetime):
            day = day.date()
        tuesday = week_start(day)
        title = week_title(day)

        async with self._lock:
            existing = await self._repository.find_thread(guild_id, tuesday)
            if existing is not None:
                return WeekThread(existing, title, created=False)

            channel = self._discord.get_channel(forum_channel_id) or await self._discord.fetch_channel(forum_channel_id)
            if not isinstance(channel, discord.ForumChannel):
                raise RuntimeError(f"channel {forum_channel_id} is not a forum channel")
            thread, _ = await channel.create_thread(name=title, content=STARTER_MESSAGE)
            log.info("Created weekly raid notes post for guild %s: %s", guild_id, title)

            await self._repository.save_thread(guild_id, tuesday, thread.id)
            return WeekThread(thread.id, title, created=True)

    async def find_week_thread(self, guild_id: int, day: date) -> int | None:
        """The thread of the week containing `day`, if the bot has made one; never creates it."""
        return await self._repository.find_thread(guild_id, week_start(day))

    async def find_digest(self, guild_id: int, day: date) -> int | None:
        """The officer digest message of the week containing `day`, if one has been posted."""
        return await self._repository.find_digest(guild_id, week_start(day))

    async def save_digest(self, guild_id: int, day: date, message_id: int) -> None:
        await self._repository.save_digest(guild_id, week_start(day), message_id)

    def thread_messageable(self, guild_id: int, thread_id: int) -> discord.PartialMessageable:
        """Somewhere to post in a week's thread, without fetching it."""
        return self._discord.get_partial_messageable(thread_id, guild_id=guild_id)
