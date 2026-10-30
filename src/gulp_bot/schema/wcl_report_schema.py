"""A Warcraft Logs report as the bot reads it, and the overviews built from it."""

from __future__ import annotations

from dataclasses import dataclass

from .guild_settings_schema import WclSource
from .wcl_settings_schema import ConsumableKind

# --- How deaths and wipes are counted ---

# Only this many deaths in a pull are counted. Every death after the second is the raid wiping rather
# than someone's mistake, so the API is asked to ignore everything after it (`wipeCutoff`). Damage taken
# is cut off the same way: nothing after the last counted death.
WIPE_CUTOFF = 2

# A wipe is taken to start when this many players die within `WIPE_WINDOW_MS` of each other.
WIPE_DEATHS = 3
WIPE_WINDOW_MS = 2_000.0
# The replay link starts this long before that moment, to show what led up to it.
REPLAY_LEAD_IN_MS = 5_000.0

# --- What the API sends ---


@dataclass(frozen=True)
class Fight:
    id: int
    name: str
    kill: bool | None
    in_progress: bool | None
    # Milliseconds since the start of the report.
    start_time: float = 0.0
    # Milliseconds since the start of the report.
    end_time: float = 0.0

    @property
    def is_finished(self) -> bool:
        """A pull that is still being uploaded is left out everywhere: its events aren't all there yet."""
        return not bool(self.in_progress)


@dataclass(frozen=True)
class Report:
    code: str
    title: str
    # Milliseconds since the Unix epoch.
    start_time: float
    fights: tuple[Fight, ...]

    @property
    def started_at(self) -> int:
        """The start of the report as Unix seconds."""
        return int(self.start_time / 1000.0)


@dataclass(frozen=True)
class ReportSummary:
    """One of a guild's reports, as the list of its reports gives it."""

    code: str
    # Milliseconds since the Unix epoch.
    start_time: float
    owner_id: int | None


@dataclass(frozen=True)
class DeathEntry:
    """One death in one pull. Only the fields the overview uses are read."""

    name: str
    # The class, written without spaces ("DeathKnight").
    class_name: str
    fight: int
    # Milliseconds since the start of the report.
    timestamp: float


@dataclass(frozen=True)
class PlayerEntry:
    """One player's total for one ability: damage taken from it, or how often they cast it."""

    name: str
    class_name: str
    total: float


@dataclass(frozen=True)
class Actor:
    """An enemy (NPC) of a report, as its master data lists it. Each kind of enemy usually has one, but adds
    can have several under the same name."""

    # The ID the report gives it, which the tables filter by (`targetID`); not the game's NPC ID.
    id: int
    name: str
    # Whether Warcraft Logs counts it as a boss (its `subType` is `Boss`): what its boss damage is done to.
    boss: bool = False


# A player's running total for one ability, by name: `(class, total)`.
PlayerTotals = dict[str, tuple[str, int]]


# --- What the overviews show ---


@dataclass(frozen=True)
class EncounterSummary:
    name: str
    pulls: int
    kills: int


@dataclass(frozen=True)
class PlayerCount:
    name: str
    class_name: str
    count: int


@dataclass(frozen=True)
class PlayerTotal:
    name: str
    class_name: str
    total: int


@dataclass(frozen=True)
class AbilityDamage:
    """The damage taken from one ability: in all, and by each player who took any."""

    name: str
    total: int
    # Most damage first (ties in name order).
    players: list[PlayerTotal]


@dataclass(frozen=True)
class ReportOverview:
    """What `/wcl report` shows for one report."""

    code: str
    title: str
    # Unix timestamp, in seconds, of the start of the report.
    started_at: int
    encounters: list[EncounterSummary]
    # Players ranked by how many of their deaths counted.
    deaths: list[PlayerCount]
    # Players ranked by how many pulls they were the first to die in.
    first_deaths: list[PlayerCount]
    # Damage taken from each tracked ability.
    damage: list[AbilityDamage]
    # How many players were left out of all of it, for the footer.
    ignored: int


@dataclass(frozen=True)
class DamageReport:
    """What `/wcl damage_taken` shows: one ability's damage over some of a report's pulls."""

    code: str
    title: str
    started_at: int
    # Which bosses the pulls are of.
    scope: str
    pulls: int
    ability: AbilityDamage
    # How many players were left out, for the footer.
    ignored: int


@dataclass(frozen=True)
class ConsumableUse:
    """The uses of one kind of consumable: in all, and by each player who used any."""

    kind: ConsumableKind
    # Whether any ability is registered for this kind; if not, there is nothing to count.
    registered: bool
    total: int
    # Most uses first (ties in name order).
    players: list[PlayerTotal]


@dataclass(frozen=True)
class ConsumablesReport:
    """What `/wcl consumables` shows: how many healthstones and health potions were used."""

    code: str
    title: str
    started_at: int
    scope: str
    pulls: int
    # One entry for each kind, healthstones first.
    uses: list[ConsumableUse]
    ignored: int


@dataclass(frozen=True)
class EnemyDamage:
    """The damage the players did to one tracked enemy in a moment of a pull: in all, and by each player."""

    name: str
    # Whether the report has an enemy by that name at all; if not, there is nothing to count.
    in_report: bool
    total: int
    # Most damage first (ties in name order).
    players: list[PlayerTotal]


@dataclass(frozen=True)
class FocusRow:
    """One player's damage to the enemy set apart, and to the other tracked enemies together."""

    name: str
    class_name: str
    focus: int
    rest: int


@dataclass(frozen=True)
class FocusDamage:
    """One tracked enemy set apart from the others, whose damage is added together: in all, and by player."""

    name: str
    # Whether the report has an enemy by that name at all.
    in_report: bool
    total: int
    # The other tracked enemies that are in the report, whose damage makes up the rest.
    rest_names: list[str]
    rest_total: int
    # Most damage to the enemy set apart first, then most to the rest (ties in name order).
    players: list[FocusRow]


@dataclass(frozen=True)
class BossRow:
    """One player's damage to the bosses, and to everything else."""

    name: str
    class_name: str
    boss: int
    other: int


@dataclass(frozen=True)
class BossDamage:
    """The damage done to the bosses of a pull against the damage done to everything else, as Warcraft Logs
    tells boss damage apart: in all, and by player."""

    # The bosses that took damage in the window.
    boss_names: list[str]
    boss_total: int
    other_total: int
    # Most damage to the bosses first, then most to everything else (ties in name order).
    players: list[BossRow]


@dataclass(frozen=True)
class WindowDamageReport:
    """What `/wcl damage_window` shows: the damage done to each tracked enemy between two moments of a pull."""

    code: str
    title: str
    started_at: int
    fight_id: int
    boss: str
    # Which pull of this boss it was, counting from 1.
    pull: int
    kill: bool | None
    # The window, in milliseconds since the start of the pull. `end_ms` is at most the pull's length.
    start_ms: int
    end_ms: int
    # Whether the window asked for went past the end of the pull, and was cut there.
    cut_at_pull_end: bool
    # Milliseconds since the start of the report, for a link to the same moment.
    report_start_ms: int
    report_end_ms: int
    # One entry per tracked enemy, in the order they are tracked.
    enemies: list[EnemyDamage]
    ignored: int
    # With an enemy set apart (`focus`), its damage against the other tracked enemies' together.
    focus: FocusDamage | None = None
    # With `boss_damage`, the damage to the bosses against the damage to everything else; `enemies` is
    # then empty.
    boss_damage: BossDamage | None = None


@dataclass(frozen=True)
class PullWindow:
    """A moment of one pull, asked for in seconds since the pull started."""

    fight: Fight
    pull: int
    start_ms: int
    end_ms: int
    cut_at_pull_end: bool

    @property
    def report_start_ms(self) -> int:
        """The start of the window in milliseconds since the start of the report, as the API takes it."""
        return int(self.fight.start_time) + self.start_ms

    @property
    def report_end_ms(self) -> int:
        return int(self.fight.start_time) + self.end_ms


@dataclass(frozen=True)
class LastWipe:
    """The last wipe of a report, and the moment a replay of it should start at."""

    code: str
    fight_id: int
    boss: str
    # Which pull of this boss it was, counting from 1.
    pull: int
    # Milliseconds since the start of the pull: where the replay starts (the link's `position`).
    position_ms: float
    # Whether `WIPE_DEATHS` players really died within `WIPE_WINDOW_MS`. If not, the replay starts before
    # the end of the pull instead.
    wipe_found: bool


# --- What an officer asked for ---


@dataclass(frozen=True)
class ReportCode:
    """A report the officer named."""

    code: str


@dataclass(frozen=True)
class NewestReport:
    """The newest report from the registered uploader."""

    source: WclSource


ReportTarget = ReportCode | NewestReport
