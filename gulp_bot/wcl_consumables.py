"""Abilities that count as a healthstone or a health potion in the Warcraft Logs consumables overview."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .db import to_db

if TYPE_CHECKING:
    import asyncpg

# The most abilities one server can register for a category. A category is a few IDs (a plain and a
# demonic healthstone, a potion and its stronger version), and each is a request to the API.
MAX_PER_KIND = 6


class Kind(enum.Enum):
    """What kind of consumable an ability is. The value is how it is stored in the database."""

    HEALTHSTONE = "healthstone"
    HEALTH_POTION = "health_potion"

    @property
    def display_name(self) -> str:
        """As offered in a command: "Healthstone"."""
        return {Kind.HEALTHSTONE: "Healthstone", Kind.HEALTH_POTION: "Health potion"}[self]

    @property
    def plural(self) -> str:
        """For headings: "Healthstones"."""
        return {Kind.HEALTHSTONE: "Healthstones", Kind.HEALTH_POTION: "Health potions"}[self]

    @classmethod
    def from_column_value(cls, value: str) -> Kind | None:
        try:
            return cls(value)
        except ValueError:
            return None


# Every kind, in the order they are shown: healthstones first.
ALL_KINDS = [Kind.HEALTHSTONE, Kind.HEALTH_POTION]


@dataclass(frozen=True)
class Registered:
    """An ability registered as a consumable of some kind."""

    kind: Kind
    ability_id: int


def ids(registered: list[Registered], kind: Kind) -> list[int]:
    """The IDs registered for one kind."""
    return [entry.ability_id for entry in registered if entry.kind is kind]


class AddOutcome(enum.Enum):
    ADDED = enum.auto()
    # The ability is already registered as this kind.
    ALREADY_REGISTERED = enum.auto()
    # The ability is registered as the other kind; an ability is one kind only.
    OTHER_KIND = enum.auto()
    # The server already has `MAX_PER_KIND` abilities of this kind.
    FULL = enum.auto()


async def add(db: asyncpg.Pool, guild_id: int, kind: Kind, ability_id: int) -> tuple[AddOutcome, Kind | None]:
    """Registers `ability_id` as a `kind` for this guild. With `OTHER_KIND`, also returns that kind."""
    registered = await list_registered(db, guild_id)
    for entry in registered:
        if entry.ability_id == ability_id:
            if entry.kind is kind:
                return AddOutcome.ALREADY_REGISTERED, None
            return AddOutcome.OTHER_KIND, entry.kind
    if len(ids(registered, kind)) >= MAX_PER_KIND:
        return AddOutcome.FULL, None

    await db.execute(
        "INSERT INTO wcl_consumable_abilities (guild_id, ability_id, kind) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        to_db(guild_id),
        ability_id,
        kind.value,
    )
    return AddOutcome.ADDED, None


async def remove(db: asyncpg.Pool, guild_id: int, ability_id: int) -> Kind | None:
    """Unregisters `ability_id`, returning what kind it was, or `None` if it wasn't registered."""
    kind = await db.fetchval(
        "DELETE FROM wcl_consumable_abilities WHERE guild_id = $1 AND ability_id = $2 RETURNING kind",
        to_db(guild_id),
        ability_id,
    )
    return None if kind is None else Kind.from_column_value(kind)


async def list_registered(db: asyncpg.Pool, guild_id: int) -> list[Registered]:
    """Everything this guild has registered, healthstones first, each by ID."""
    rows = await db.fetch("SELECT ability_id, kind FROM wcl_consumable_abilities WHERE guild_id = $1 ORDER BY kind, ability_id", to_db(guild_id))
    registered = []
    for row in rows:
        # Stored from a `Kind` and a 32-bit ID, so both always convert back; a row edited by hand into
        # something else is skipped.
        kind = Kind.from_column_value(row["kind"])
        ability_id = row["ability_id"]
        if kind is not None and 0 <= ability_id <= 0xFFFFFFFF:
            registered.append(Registered(kind, ability_id))
    return registered
