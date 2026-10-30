"""Creates a new post in every guild's raid notes forum on that guild's schedule (`/settings raid_notes_schedule`,
or the default). A week that already has a thread (because attendance was recorded earlier in it) doesn't
get a second one. The week's officer digest is posted in it then too, and the absences and notes of earlier
weeks are deleted (`absence_service`)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from ..service.absence_service import AbsenceService
from ..service.guild_settings_service import GuildSettingsService
from ..service.raid_notes_service import RaidNotesService, raid_notes_schedule
from .schedule_loop import ScheduledWork, run_schedule_loop

log = logging.getLogger(__name__)


class RaidNotesJob:
    def __init__(self, guild_settings: GuildSettingsService, raid_notes: RaidNotesService, absences: AbsenceService):
        self._guild_settings = guild_settings
        self._raid_notes = raid_notes
        self._absences = absences

    async def run_forever(self) -> None:
        await run_schedule_loop("raid notes schedules", self._scheduled_work)

    async def _scheduled_work(self) -> list[ScheduledWork]:
        return [
            ScheduledWork(
                raid_notes_schedule(settings),
                self._week_thread_creator(guild_id, settings.raid_notes_channel_id),
                f"Failed to create raid notes post for guild {guild_id}",
            )
            for guild_id, settings in self._guild_settings.all()
            if settings.raid_notes_channel_id is not None
        ]

    def _week_thread_creator(self, guild_id: int, channel_id: int) -> Callable[[datetime], Awaitable[None]]:
        async def create(fire_time: datetime) -> None:
            day = fire_time.date()
            week = await self._raid_notes.week_thread(guild_id, channel_id, day)
            try:
                await self._absences.clean_up(guild_id, day)
                await self._absences.post_digest(guild_id, week.thread_id, day)
            except Exception as e:
                # The thread is there either way; the digest is posted with the next change to the week.
                log.exception("Failed to post the officer digest for guild %s: %s", guild_id, e)

        return create
