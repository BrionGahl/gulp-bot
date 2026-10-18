"""What a guild's officers set up for the Warcraft Logs overviews: the abilities and enemies they track, the
players they leave out, and the abilities that count as consumables."""

from __future__ import annotations

import enum
from dataclasses import dataclass


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


class TrackEnemyOutcome(enum.Enum):
    ADDED = enum.auto()
    # An enemy with this name is already tracked, possibly in another capitalisation.
    ALREADY_TRACKED = enum.auto()
    # The server already tracks as many enemies as it may.
    FULL = enum.auto()


class IgnorePlayerOutcome(enum.Enum):
    ADDED = enum.auto()
    # The player is already ignored, possibly under a different capitalisation.
    ALREADY_IGNORED = enum.auto()
    # The server already ignores as many players as it may.
    FULL = enum.auto()


class ConsumableKind(enum.Enum):
    """What kind of consumable an ability is. The value is how it is stored in the database."""

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

    @classmethod
    def from_column_value(cls, value: str) -> ConsumableKind | None:
        try:
            return cls(value)
        except ValueError:
            return None


# Every kind, in the order they are shown: healthstones first.
ALL_CONSUMABLE_KINDS = [ConsumableKind.HEALTHSTONE, ConsumableKind.HEALTH_POTION]


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
