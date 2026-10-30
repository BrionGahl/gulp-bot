"""A guild's list of names in a table of its own (`guild_id`, `name`): the enemies it tracks, the players it
ignores."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.database import to_bigint

if TYPE_CHECKING:
    import asyncpg


class NameListRepository:
    """The rows of one such table. `table` is this module's own constant, never a member's input."""

    def __init__(self, pool: asyncpg.Pool, table: str):
        self._pool = pool
        self._table = table

    async def list_for_guild(self, guild_id: int) -> list[str]:
        """Every name on the guild's list, in name order (ignoring case)."""
        rows = await self._pool.fetch(f"SELECT name FROM {self._table} WHERE guild_id = $1", to_bigint(guild_id))
        return sorted((row["name"] for row in rows), key=str.casefold)

    async def insert(self, guild_id: int, name: str) -> None:
        await self._pool.execute(f"INSERT INTO {self._table} (guild_id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING", to_bigint(guild_id), name)

    async def delete(self, guild_id: int, name: str) -> None:
        """Deletes the row with exactly this name."""
        await self._pool.execute(f"DELETE FROM {self._table} WHERE guild_id = $1 AND name = $2", to_bigint(guild_id), name)
