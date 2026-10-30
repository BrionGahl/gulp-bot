"""The `raid_attendance_starts` table: each guild's latest raid-start snapshot."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..core.database import from_bigint, to_bigint
from ..schema.raid_attendance_schema import StartSnapshot

if TYPE_CHECKING:
    import asyncpg


class RaidAttendanceRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save_start_snapshot(self, guild_id: int, voice_channel_id: int, at: datetime, present: list[int]) -> None:
        """Stores `present` as the guild's current raid-start snapshot, replacing any earlier one."""
        await self._pool.execute(
            "INSERT INTO raid_attendance_starts (guild_id, voice_channel_id, taken_at, user_ids) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id) DO UPDATE SET voice_channel_id = EXCLUDED.voice_channel_id, "
            "taken_at = EXCLUDED.taken_at, user_ids = EXCLUDED.user_ids",
            to_bigint(guild_id),
            to_bigint(voice_channel_id),
            at.astimezone(UTC),
            [to_bigint(user_id) for user_id in present],
        )

    async def take_start_snapshot(self, guild_id: int) -> StartSnapshot | None:
        """Removes and returns the guild's stored raid-start snapshot, if it has one."""
        row = await self._pool.fetchrow(
            "DELETE FROM raid_attendance_starts WHERE guild_id = $1 RETURNING voice_channel_id, taken_at, user_ids",
            to_bigint(guild_id),
        )
        if row is None:
            return None
        return StartSnapshot(from_bigint(row["voice_channel_id"]), row["taken_at"], tuple(from_bigint(user_id) for user_id in row["user_ids"]))
