"""Players a guild's officers leave out of the Warcraft Logs overview."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from .db import to_db

if TYPE_CHECKING:
    import asyncpg

# The most players one server can ignore.
MAX_IGNORED = 50

# The longest name accepted: a character name is at most 12 letters, so this leaves room for a mistyped
# one without letting a whole sentence in.
MAX_NAME_CHARS = 24


class AddOutcome(enum.Enum):
    ADDED = enum.auto()
    # The player is already ignored, possibly under a different capitalisation.
    ALREADY_IGNORED = enum.auto()
    # The server already ignores `MAX_IGNORED` players.
    FULL = enum.auto()


def same_name(a: str, b: str) -> bool:
    """Whether two character names are the same one: names compare ignoring case, including the
    accented letters character names can have."""
    return a.lower() == b.lower()


async def add(db: asyncpg.Pool, guild_id: int, name: str) -> AddOutcome:
    """Leaves `name` out of this guild's Warcraft Logs overview."""
    ignored = await list_ignored(db, guild_id)
    if any(same_name(existing, name) for existing in ignored):
        return AddOutcome.ALREADY_IGNORED
    if len(ignored) >= MAX_IGNORED:
        return AddOutcome.FULL

    await db.execute("INSERT INTO wcl_ignored_players (guild_id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING", to_db(guild_id), name)
    return AddOutcome.ADDED


async def remove(db: asyncpg.Pool, guild_id: int, name: str) -> bool:
    """Stops ignoring `name` (in any capitalisation). Returns whether the player was ignored."""
    ignored = await list_ignored(db, guild_id)
    stored = next((existing for existing in ignored if same_name(existing, name)), None)
    if stored is None:
        return False

    await db.execute("DELETE FROM wcl_ignored_players WHERE guild_id = $1 AND name = $2", to_db(guild_id), stored)
    return True


async def list_ignored(db: asyncpg.Pool, guild_id: int) -> list[str]:
    """Every player this guild ignores, in name order (ignoring case)."""
    rows = await db.fetch("SELECT name FROM wcl_ignored_players WHERE guild_id = $1", to_db(guild_id))
    return sorted((row["name"] for row in rows), key=str.lower)
