"""The `raid_notes_posts` table: which forum thread is each week's, and its officer digest message."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ..core.database import from_bigint, to_bigint

if TYPE_CHECKING:
    import asyncpg


class RaidNotesPostRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def find_thread(self, guild_id: int, week_start: date) -> int | None:
        """The thread recorded for the week starting `week_start`, if any."""
        thread_id = await self._pool.fetchval("SELECT thread_id FROM raid_notes_posts WHERE guild_id = $1 AND week_start = $2", to_bigint(guild_id), week_start)
        return None if thread_id is None else from_bigint(thread_id)

    async def save_thread(self, guild_id: int, week_start: date, thread_id: int) -> None:
        await self._pool.execute(
            "INSERT INTO raid_notes_posts (guild_id, week_start, thread_id) VALUES ($1, $2, $3)",
            to_bigint(guild_id),
            week_start,
            to_bigint(thread_id),
        )

    async def find_digest(self, guild_id: int, week_start: date) -> int | None:
        """The officer digest message posted in the week's thread, if any."""
        message_id = await self._pool.fetchval(
            "SELECT digest_message_id FROM raid_notes_posts WHERE guild_id = $1 AND week_start = $2", to_bigint(guild_id), week_start
        )
        return None if message_id is None else from_bigint(message_id)

    async def save_digest(self, guild_id: int, week_start: date, message_id: int) -> None:
        """Records the officer digest of the week, whose thread must be recorded already."""
        await self._pool.execute(
            "UPDATE raid_notes_posts SET digest_message_id = $3 WHERE guild_id = $1 AND week_start = $2",
            to_bigint(guild_id),
            week_start,
            to_bigint(message_id),
        )
