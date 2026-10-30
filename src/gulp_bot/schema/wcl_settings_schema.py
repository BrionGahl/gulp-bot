"""What a guild's officers set up for the Warcraft Logs overviews: the abilities and enemies they track, the
players they leave out, and the abilities that count as consumables."""

from __future__ import annotations

import enum
from dataclasses import dataclass

# The largest ability ID: the game's ability IDs are unsigned 32-bit numbers.
MAX_ABILITY_ID = 0xFFFFFFFF

# The largest ID Warcraft Logs gives its own things (guilds, users): signed 32-bit numbers, and zero is never one.
MAX_WCL_ID = 2**31 - 1

# The most abilities, or enemies, one server can track: each is a field of an embed, which has a size limit
# of its own.
MAX_TRACKED = 6

# The longest tracked ability or enemy name: it is an embed field title, which Discord allows 256 characters.
MAX_TRACKED_NAME_CHARS = 100


@dataclass(frozen=True)
class TrackedAbility:
    """An ability whose damage taken is shown in the Warcraft Logs overview."""

    # The game's ability ID, the same number Warcraft Logs and Wowhead use.
    ability_id: int
    name: str


class TrackAbilityOutcome(enum.Enum):
    ADDED = enum.auto()
    # The ability was already tracked, and now has the new name.
    RENAMED = enum.auto()
    # Another ability of this server already has this name.
    NAME_TAKEN = enum.auto()
    # The server already tracks as many abilities as it may.
    FULL = enum.auto()


@dataclass(frozen=True)
class TrackAbilityResult:
    outcome: TrackAbilityOutcome
    # With `NAME_TAKEN`, the ID of the ability that has the name.
    other_ability_id: int | None = None


class NameListOutcome(enum.Enum):
    """What adding a name to one of a guild's lists of names (tracked enemies, ignored players) did."""

    ADDED = enum.auto()
    # The name is on the list already, possibly in another capitalisation.
    ALREADY_LISTED = enum.auto()
    # The list is as long as it may be.
    FULL = enum.auto()


class ConsumableKind(enum.Enum):
    """What kind of consumable an ability is. The value is how it is stored in the database; the order is the
    order they are shown in, healthstones first."""

    HEALTHSTONE = "healthstone"
    HEALTH_POTION = "health_potion"

    @property
    def display_name(self) -> str:
        """As offered in a command: "Healthstone"."""
        return {ConsumableKind.HEALTHSTONE: "Healthstone", ConsumableKind.HEALTH_POTION: "Health potion"}[self]

    @property
    def plural(self) -> str:
        """For headings: "Healthstones"."""
        return {ConsumableKind.HEALTHSTONE: "Healthstones", ConsumableKind.HEALTH_POTION: "Health potions"}[self]


@dataclass(frozen=True)
class ConsumableAbility:
    """An ability registered as a consumable of some kind."""

    kind: ConsumableKind
    ability_id: int


class RegisterConsumableOutcome(enum.Enum):
    ADDED = enum.auto()
    # The ability is already registered as this kind.
    ALREADY_REGISTERED = enum.auto()
    # The ability is registered as the other kind; an ability is one kind only.
    OTHER_KIND = enum.auto()
    # The server already has as many abilities of this kind as it may.
    FULL = enum.auto()


@dataclass(frozen=True)
class RegisterConsumableResult:
    outcome: RegisterConsumableOutcome
    # With `OTHER_KIND`, the kind the ability is registered as.
    other_kind: ConsumableKind | None = None
