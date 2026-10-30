"""The `wcl_tracked_abilities` table: the abilities a guild tracks in the Warcraft Logs overview."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.database import rows_affected, to_bigint
from ..schema.wcl_settings_schema import MAX_ABILITY_ID, TrackedAbility

if TYPE_CHECKING:
    import asyncpg


class TrackedAbilityRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def list_for_guild(self, guild_id: int) -> list[TrackedAbility]:
        """Every ability the guild tracks, by name."""
        rows = await self._pool.fetch(
            "SELECT ability_id, name FROM wcl_tracked_abilities WHERE guild_id = $1 ORDER BY lower(name), ability_id", to_bigint(guild_id)
        )
        return [TrackedAbility(row["ability_id"] if 0 <= row["ability_id"] <= MAX_ABILITY_ID else 0, row["name"]) for row in rows]

    async def save(self, guild_id: int, ability: TrackedAbility) -> None:
        """Tracks the ability, or renames it if it is already tracked."""
        await self._pool.execute(
            "INSERT INTO wcl_tracked_abilities (guild_id, ability_id, name) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, ability_id) DO UPDATE SET name = EXCLUDED.name",
            to_bigint(guild_id),
            ability.ability_id,
            ability.name,
        )

    async def delete_by_name(self, guild_id: int, name: str) -> bool:
        """Stops tracking the ability with this name (ignoring case). Returns whether there was one."""
        status = await self._pool.execute("DELETE FROM wcl_tracked_abilities WHERE guild_id = $1 AND lower(name) = lower($2)", to_bigint(guild_id), name)
        return rows_affected(status) == 1
