"""Records who is in each guild's raid voice channel (`/settings raid_voice`) at the start and end of each raid
night (`/settings raid_nights`), and posts the list to that week's raid notes thread.

The raid end is also when the Warcraft Logs overview of the night is set off: it is posted to the same
thread a little later, once the log has uploaded (see `raid_report_service.py`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from ..core.background_tasks import BackgroundTasks
from ..schema.raid_attendance_schema import AttendanceMoment
from ..service.guild_settings_service import GuildSettingsService
from ..service.raid_attendance_service import RaidAttendanceService
from ..service.raid_nights_service import attendance_schedules
from ..service.raid_report_service import RaidReportService
from .schedule_loop import ScheduledWork, run_schedule_loop

log = logging.getLogger(__name__)


class RaidAttendanceJob:
    def __init__(self, guild_settings: GuildSettingsService, attendance: RaidAttendanceService, raid_report: RaidReportService, tasks: BackgroundTasks):
        self._guild_settings = guild_settings
        self._attendance = attendance
        self._raid_report = raid_report
        self._tasks = tasks

    async def run_forever(self) -> None:
        await run_schedule_loop("raid attendance schedules", self._scheduled_work)

    async def _scheduled_work(self) -> list[ScheduledWork]:
        work = []
        for guild_id, settings in self._guild_settings.all():
            schedules = attendance_schedules(settings)
            if schedules is None:
                continue
            for moment, schedule in zip((AttendanceMoment.START, AttendanceMoment.END), schedules, strict=True):
                work.append(
                    ScheduledWork(schedule, self._snapshot_taker(guild_id, moment), f"Failed to record raid {moment.value} attendance for guild {guild_id}")
                )
        return work

    def _snapshot_taker(self, guild_id: int, moment: AttendanceMoment) -> Callable[[datetime], Awaitable[None]]:
        async def take_snapshot(fire_time: datetime) -> None:
            try:
                await self._record(guild_id, moment, fire_time)
            finally:
                # On a task of its own, whether or not the snapshot worked: it waits for the log to finish
                # uploading, and the other guilds' snapshots can't wait with it.
                if moment is AttendanceMoment.END:
                    self._tasks.spawn(self._raid_report.post_after_raid(guild_id, fire_time))

        return take_snapshot

    async def _record(self, guild_id: int, moment: AttendanceMoment, fire_time: datetime) -> None:
        # Read when the snapshot is due, so that registering the raid notes channel later needs no change.
        settings = self._guild_settings.get(guild_id)
        if settings.raid_voice is None:
            return
        if settings.raid_notes_channel_id is None:
            log.error("Skipping raid %s attendance for guild %s: no raid notes channel is registered", moment.value, guild_id)
            return
        await self._attendance.record_snapshot(guild_id, settings.raid_voice.channel_id, settings.raid_notes_channel_id, moment, fire_time)
