"""The `wcl_consumable_abilities` table: the abilities a guild counts as healthstones and health potions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.database import to_bigint
from ..schema.wcl_settings_schema import MAX_ABILITY_ID, ConsumableAbility, ConsumableKind

if TYPE_CHECKING:
    import asyncpg


def consumable_kind(value: str) -> ConsumableKind | None:
    """The kind stored as `value`, or `None` for one this version doesn't know (stored by a newer one)."""
    try:
        return ConsumableKind(value)
    except ValueError:
        return None


class ConsumableAbilityRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def list_for_guild(self, guild_id: int) -> list[ConsumableAbility]:
        """Everything the guild has registered, healthstones first, each by ID."""
        rows = await self._pool.fetch(
            "SELECT ability_id, kind FROM wcl_consumable_abilities WHERE guild_id = $1 ORDER BY kind, ability_id", to_bigint(guild_id)
        )
        registered = []
        for row in rows:
            kind = consumable_kind(row["kind"])
            if kind is not None and 0 <= row["ability_id"] <= MAX_ABILITY_ID:
                registered.append(ConsumableAbility(kind, row["ability_id"]))
        return registered

    async def insert(self, guild_id: int, consumable: ConsumableAbility) -> None:
        await self._pool.execute(
            "INSERT INTO wcl_consumable_abilities (guild_id, ability_id, kind) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            to_bigint(guild_id),
            consumable.ability_id,
            consumable.kind.value,
        )

    async def delete(self, guild_id: int, ability_id: int) -> ConsumableKind | None:
        """Unregisters `ability_id`, returning what kind it was, or `None` if it wasn't registered."""
        kind = await self._pool.fetchval(
            "DELETE FROM wcl_consumable_abilities WHERE guild_id = $1 AND ability_id = $2 RETURNING kind",
            to_bigint(guild_id),
            ability_id,
        )
        return None if kind is None else consumable_kind(kind)
