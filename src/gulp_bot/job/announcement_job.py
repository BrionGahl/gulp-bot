"""Posts each stored announcement to its channel whenever its schedule fires."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from ..schema.announcement_schema import Announcement
from ..service.announcement_service import AnnouncementService
from .schedule_loop import ScheduledWork, run_schedule_loop


class AnnouncementJob:
    def __init__(self, announcements: AnnouncementService):
        self._announcements = announcements

    async def run_forever(self) -> None:
        await run_schedule_loop("announcements", self._scheduled_work)

    async def _scheduled_work(self) -> list[ScheduledWork]:
        # Read again on every wake-up: officers add and delete announcements at any time.
        return [
            ScheduledWork(
                announcement.schedule,
                self._poster(announcement),
                f"Failed to post announcement {announcement.id} for guild {announcement.guild_id}",
            )
            for announcement in await self._announcements.list_all()
        ]

    def _poster(self, announcement: Announcement) -> Callable[[datetime], Awaitable[None]]:
        async def post(fire_time: datetime) -> None:
            await self._announcements.post(announcement)

        return post
