"""Abilities (mechanics) a guild's officers track in the Warcraft Logs damage taken overview."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ai.text import ascii_lower
from .db import rows_affected, to_db

if TYPE_CHECKING:
    import asyncpg

# The most abilities one server can track. Each is a query to the Warcraft Logs API and a field of the
# overview embed, which has a size limit of its own.
MAX_TRACKED = 6

# The longest name an officer can give an ability: it is a field title in the overview, and Discord
# allows 256 characters there.
MAX_NAME_CHARS = 100


@dataclass(frozen=True)
class TrackedAbility:
    """An ability whose damage taken is shown in the Warcraft Logs overview."""

    # The game's ability ID, the same number Warcraft Logs and Wowhead use.
    ability_id: int
    name: str


class AddOutcome(enum.Enum):
    ADDED = enum.auto()
    # The ability was already tracked, and now has the new name.
    RENAMED = enum.auto()
    # Another ability of this server already has this name.
    NAME_TAKEN = enum.auto()
    # The server already tracks `MAX_TRACKED` abilities.
    FULL = enum.auto()


async def add(db: asyncpg.Pool, guild_id: int, ability_id: int, name: str) -> tuple[AddOutcome, int | None]:
    """Tracks `ability_id` under `name` for this guild, or renames it if it is already tracked. With
    `NAME_TAKEN`, also returns the ID of the ability that has the name."""
    tracked = await list_tracked(db, guild_id)
    already_tracked = any(ability.ability_id == ability_id for ability in tracked)

    for other in tracked:
        if other.ability_id != ability_id and ascii_lower(other.name) == ascii_lower(name):
            return AddOutcome.NAME_TAKEN, other.ability_id
    if not already_tracked and len(tracked) >= MAX_TRACKED:
        return AddOutcome.FULL, None

    await db.execute(
        "INSERT INTO wcl_tracked_abilities (guild_id, ability_id, name) VALUES ($1, $2, $3) "
        "ON CONFLICT (guild_id, ability_id) DO UPDATE SET name = EXCLUDED.name",
        to_db(guild_id),
        ability_id,
        name,
    )
    return (AddOutcome.RENAMED if already_tracked else AddOutcome.ADDED), None


async def remove(db: asyncpg.Pool, guild_id: int, name: str) -> bool:
    """Stops tracking the ability with this name (ignoring case). Returns whether there was one."""
    status = await db.execute("DELETE FROM wcl_tracked_abilities WHERE guild_id = $1 AND lower(name) = lower($2)", to_db(guild_id), name)
    return rows_affected(status) == 1


async def list_tracked(db: asyncpg.Pool, guild_id: int) -> list[TrackedAbility]:
    """Every ability this guild tracks, by name."""
    rows = await db.fetch("SELECT ability_id, name FROM wcl_tracked_abilities WHERE guild_id = $1 ORDER BY lower(name), ability_id", to_db(guild_id))
    # Stored from a 32-bit ID, so it always converts back.
    return [TrackedAbility(row["ability_id"] if 0 <= row["ability_id"] <= 0xFFFFFFFF else 0, row["name"]) for row in rows]
