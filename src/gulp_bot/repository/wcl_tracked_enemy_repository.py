"""The `wcl_tracked_enemies` table: the enemies a guild tracks in `/wcl damage_window`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.database import to_bigint

if TYPE_CHECKING:
    import asyncpg


class TrackedEnemyRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def list_for_guild(self, guild_id: int) -> list[str]:
        """Every enemy the guild tracks, in name order (ignoring case)."""
        rows = await self._pool.fetch("SELECT name FROM wcl_tracked_enemies WHERE guild_id = $1", to_bigint(guild_id))
        return sorted((row["name"] for row in rows), key=str.lower)

    async def insert(self, guild_id: int, name: str) -> None:
        await self._pool.execute("INSERT INTO wcl_tracked_enemies (guild_id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING", to_bigint(guild_id), name)

    async def delete(self, guild_id: int, name: str) -> None:
        """Deletes the row with exactly this name."""
        await self._pool.execute("DELETE FROM wcl_tracked_enemies WHERE guild_id = $1 AND name = $2", to_bigint(guild_id), name)
