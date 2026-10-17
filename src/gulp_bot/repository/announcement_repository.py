"""The `announcements` table. Every lookup by ID is scoped to a guild: IDs are global, and an officer of
one server must not be able to read, post or delete another server's announcement by guessing one."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.database import rows_affected, to_bigint
from ..mapper.announcement_mapper import announcement_from_row, announcements_from_rows
from ..schema.announcement_schema import Announcement, NewAnnouncement

if TYPE_CHECKING:
    import asyncpg

COLUMNS = "id, guild_id, channel_id, cron, message, created_by"


class AnnouncementRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def insert(self, announcement: NewAnnouncement) -> int:
        """Stores a new announcement and returns its ID."""
        return await self._pool.fetchval(
            "INSERT INTO announcements (guild_id, channel_id, cron, message, created_by) VALUES ($1, $2, $3, $4, $5) RETURNING id",
            to_bigint(announcement.guild_id),
            to_bigint(announcement.channel_id),
            announcement.schedule.source,
            announcement.message,
            to_bigint(announcement.created_by),
        )

    async def list_for_guild(self, guild_id: int) -> list[Announcement]:
        """`guild_id`'s announcements, oldest first."""
        return announcements_from_rows(await self._pool.fetch(f"SELECT {COLUMNS} FROM announcements WHERE guild_id = $1 ORDER BY id", to_bigint(guild_id)))

    async def count_for_guild(self, guild_id: int) -> int:
        return await self._pool.fetchval("SELECT count(*) AS total FROM announcements WHERE guild_id = $1", to_bigint(guild_id))

    async def find(self, guild_id: int, announcement_id: int) -> Announcement | None:
        row = await self._pool.fetchrow(f"SELECT {COLUMNS} FROM announcements WHERE id = $1 AND guild_id = $2", announcement_id, to_bigint(guild_id))
        return None if row is None else announcement_from_row(row)

    async def delete(self, guild_id: int, announcement_id: int) -> bool:
        """Returns whether the announcement existed in `guild_id`."""
        status = await self._pool.execute("DELETE FROM announcements WHERE id = $1 AND guild_id = $2", announcement_id, to_bigint(guild_id))
        return rows_affected(status) == 1

    async def list_all(self) -> list[Announcement]:
        """Every guild's announcements, for the scheduler."""
        return announcements_from_rows(await self._pool.fetch(f"SELECT {COLUMNS} FROM announcements ORDER BY id"))
