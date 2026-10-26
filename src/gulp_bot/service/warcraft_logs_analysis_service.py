"""What the Warcraft Logs overviews make of a report: which pulls count, who died first, who took the most
damage, when the raid wiped. Pure functions over the report schemas; no API calls in here."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from ..core.text import ascii_lower, pull_time
from ..exception.warcraft_logs_exception import ReportRequestProblem, WarcraftLogsError
from ..schema.guild_settings_schema import GuildSettings
from ..schema.wcl_report_schema import (
    REPLAY_LEAD_IN_MS,
    WIPE_DEATHS,
    WIPE_WINDOW_MS,
    AbilityDamage,
    Actor,
    BossDamage,
    BossRow,
    ConsumablesReport,
    ConsumableUse,
    DamageReport,
    DeathEntry,
    EncounterSummary,
    EnemyDamage,
    Fight,
    FocusDamage,
    FocusRow,
    LastWipe,
    NewestReport,
    PlayerCount,
    PlayerEntry,
    PlayerTotal,
    PlayerTotals,
    PullWindow,
    Report,
    ReportCode,
    ReportOverview,
    ReportSummary,
    ReportTarget,
    WindowDamageReport,
)
from ..schema.wcl_settings_schema import ALL_CONSUMABLE_KINDS, ConsumableAbility, TrackedAbility

# --- Which pulls and reports ---


def select_fights(fights: Sequence[Fight], boss: str | None) -> list[Fight]:
    """The finished fights of `fights`, narrowed to those of the boss whose name contains `boss` (ignoring
    case) when one is given. Raises with which bosses the report does have."""
    finished = [fight for fight in fights if fight.is_finished]
    boss = None if boss is None else boss.strip()
    if not boss:
        return finished

    needle = boss.lower()
    matching = [fight for fight in finished if needle in fight.name.lower()]
    if matching:
        return matching

    names = sorted({fight.name for fight in finished})
    if not names:
        raise WarcraftLogsError("This report has no Mythic boss pulls.")
    raise WarcraftLogsError(f"No Mythic pulls of a boss matching `{boss}`. The bosses in this report: {', '.join(names)}.")


def newest_by_uploader(reports: Sequence[ReportSummary], uploader_id: int) -> ReportSummary | None:
    """The newest of `reports` that `uploader_id` uploaded."""
    theirs = [report for report in reports if report.owner_id == uploader_id]
    return max(theirs, key=lambda report: report.start_time, default=None)


def scope_text(fights: Sequence[Fight], boss: str | None) -> str:
    """Which bosses a set of pulls is of, for an embed: the boss names when the pulls were narrowed to a
    boss, otherwise all of them."""
    if boss is not None and boss.strip():
        # Repeats next to each other are dropped, as the pulls of one boss come together.
        names: list[str] = []
        for fight in fights:
            if not names or names[-1] != fight.name:
                names.append(fight.name)
        return ", ".join(names)
    return "All Mythic bosses"


# --- Players left out ---


def ignored_set(names: Iterable[str]) -> set[str]:
    """Player names in the form they are compared in: lowercase, so capitalisation never matters."""
    return {name.lower() for name in names}


def is_ignored(ignored: set[str], name: str) -> bool:
    return name.lower() in ignored


# --- Totals and rankings ---


def add_totals(players: PlayerTotals, entries: Iterable[PlayerEntry], ignored: set[str]) -> None:
    """Adds `entries`, except those of ignored players, to the running totals per player. Pulls are queried
    in chunks, and a player's total over the night is the sum over the chunks."""
    for entry in entries:
        if is_ignored(ignored, entry.name):
            continue
        class_name, total = players.get(entry.name, (entry.class_name, 0))
        players[entry.name] = (class_name, total + int(max(entry.total, 0.0)))


def totals_of(entries: Iterable[PlayerEntry], ignored: set[str]) -> PlayerTotals:
    players: PlayerTotals = {}
    add_totals(players, entries, ignored)
    return players


def ranked_players(players: PlayerTotals) -> list[PlayerTotal]:
    """Players with their totals, most first (ties in name order)."""
    ranking = [PlayerTotal(name, class_name, total) for name, (class_name, total) in players.items()]
    ranking.sort(key=lambda player: (-player.total, player.name))
    return ranking


def build_ability_damage(ability: TrackedAbility, players: PlayerTotals) -> AbilityDamage:
    ranking = ranked_players(players)
    return AbilityDamage(ability.name, sum(player.total for player in ranking), ranking)


def tally(deaths: Iterable[DeathEntry]) -> list[PlayerCount]:
    """Counts deaths per player, most first (ties in name order)."""
    counts: dict[str, PlayerCount] = {}
    for death in deaths:
        held = counts.get(death.name)
        counts[death.name] = PlayerCount(death.name, held.class_name if held else death.class_name, (held.count if held else 0) + 1)
    return sorted(counts.values(), key=lambda player: (-player.count, player.name))


def first_death_of_each_pull(deaths: Sequence[DeathEntry]) -> list[DeathEntry]:
    """The earliest death in each pull. Deaths at the very same moment keep the order they were listed in."""
    first: dict[int, DeathEntry] = {}
    for death in deaths:
        earlier = first.get(death.fight)
        if earlier is None or death.timestamp < earlier.timestamp:
            first[death.fight] = death
    return list(first.values())


# --- The overviews ---


def build_overview(report: Report, deaths: Sequence[DeathEntry], damage: list[AbilityDamage], ignored: int) -> ReportOverview:
    counts: dict[str, list[int]] = {}
    for fight in report.fights:
        if not fight.is_finished:
            continue
        pulls_and_kills = counts.setdefault(fight.name, [0, 0])
        pulls_and_kills[0] += 1
        if fight.kill:
            pulls_and_kills[1] += 1

    return ReportOverview(
        code=report.code,
        title=report.title,
        started_at=report.started_at,
        encounters=[EncounterSummary(name, pulls, kills) for name, (pulls, kills) in counts.items()],
        deaths=tally(deaths),
        first_deaths=tally(first_death_of_each_pull(deaths)),
        damage=damage,
        ignored=ignored,
    )


def build_damage_report(report: Report, fights: Sequence[Fight], ability: AbilityDamage, boss: str | None, ignored: int) -> DamageReport:
    return DamageReport(report.code, report.title, report.started_at, scope_text(fights, boss), len(fights), ability, ignored)


def build_consumables_report(
    report: Report,
    fights: Sequence[Fight],
    boss: str | None,
    registered: Sequence[ConsumableAbility],
    totals: Sequence[PlayerTotals],
    ignored: int,
) -> ConsumablesReport:
    """`totals` holds each registered ability's players, in the order of `registered`; the abilities of one
    kind are added together (a plain and a demonic healthstone are both healthstones)."""
    uses = []
    for kind in ALL_CONSUMABLE_KINDS:
        players: PlayerTotals = {}
        any_registered = False
        for entry, per_ability in zip(registered, totals, strict=False):
            if entry.kind is not kind:
                continue
            any_registered = True
            for name, (class_name, total) in per_ability.items():
                held_class, held_total = players.get(name, (class_name, 0))
                players[name] = (held_class, held_total + total)
        ranking = ranked_players(players)
        uses.append(ConsumableUse(kind, any_registered, sum(player.total for player in ranking), ranking))

    return ConsumablesReport(report.code, report.title, report.started_at, scope_text(fights, boss), len(fights), uses, ignored)


# --- The last wipe ---


def last_wipe_of(report: Report) -> tuple[Fight, int]:
    """The last finished Mythic boss pull that was a wipe, and which pull of that boss it was (from 1)."""
    finished = [fight for fight in report.fights if fight.is_finished]
    fight = next((fight for fight in reversed(finished) if fight.kill is False), None)
    if fight is None:
        raise WarcraftLogsError("This report has no Mythic boss wipes.")
    pull = sum(1 for other in finished if other.name == fight.name and other.id <= fight.id)
    return fight, pull


def wipe_moment(death_times: Sequence[float]) -> float | None:
    """The time of the first death in the first run of `WIPE_DEATHS` deaths that all fall within
    `WIPE_WINDOW_MS`. `None` if there is no such run."""
    times = sorted(death_times)
    for start in range(len(times) - WIPE_DEATHS + 1):
        if times[start + WIPE_DEATHS - 1] - times[start] <= WIPE_WINDOW_MS:
            return times[start]
    return None


def build_last_wipe(code: str, fight: Fight, pull: int, death_times: Sequence[float]) -> LastWipe:
    """Where the replay of `fight` starts: `REPLAY_LEAD_IN_MS` before the wipe, but not before the pull."""
    moment = wipe_moment(death_times)
    wipe_found = moment is not None
    if moment is None:
        # Nobody went down together: the pull most likely ended some other way, so the end of the pull is
        # the best place to look.
        moment = max([fight.end_time, *death_times])
    position_ms = max(moment - REPLAY_LEAD_IN_MS, fight.start_time) - fight.start_time
    return LastWipe(code, fight.id, fight.name, pull, position_ms, wipe_found)


# --- Damage done in a moment of a pull ---

# The longest time into a pull that can be asked for: no boss pull lasts an hour.
MAX_PULL_SECONDS = 59 * 60 + 59

_SECONDS = re.compile(r"([0-9]{1,4})\s*s?")
_MINUTES_SECONDS = re.compile(r"([0-9]{1,2}):([0-5][0-9])")


def parse_pull_time(text: str) -> int | None:
    """A time into a pull as an officer typed it, in seconds: `40`, `40s` or `1:05`. `None` if it isn't one."""
    text = text.strip().lower()
    if not text.isascii():
        return None
    if match := _MINUTES_SECONDS.fullmatch(text):
        seconds = int(match.group(1)) * 60 + int(match.group(2))
    elif match := _SECONDS.fullmatch(text):
        seconds = int(match.group(1))
    else:
        return None
    return seconds if seconds <= MAX_PULL_SECONDS else None


def select_pull(fights: Sequence[Fight], boss: str, pull: int | None) -> tuple[Fight, int]:
    """One pull of the boss whose name contains `boss`: the `pull`th of the report (from 1), or the last one
    when `pull` is `None`. Returns it with which pull of the boss it is. Raises with what the report does
    have when that pull isn't there, or when `boss` matches more than one boss."""
    matching = select_fights(fights, boss)
    names = sorted({fight.name for fight in matching})
    if len(names) > 1:
        raise WarcraftLogsError(f"`{boss.strip()}` matches more than one boss: {', '.join(names)}. Give more of the name.")
    if not matching:
        # Only when `boss` is blank: `select_fights` raises when a name matches nothing.
        raise WarcraftLogsError("This report has no Mythic boss pulls.")
    if pull is None:
        return matching[-1], len(matching)
    if not 1 <= pull <= len(matching):
        raise WarcraftLogsError(f"This report has {len(matching)} Mythic pulls of {names[0]}. Pick one from 1 to {len(matching)}.")
    return matching[pull - 1], pull


def pull_window(fight: Fight, pull: int, start_seconds: int, end_seconds: int) -> PullWindow:
    """The window of `fight` from `start_seconds` to `end_seconds` after it started, cut at the end of the
    pull. Raises if it is empty, or starts after the pull ended."""
    if end_seconds <= start_seconds:
        raise WarcraftLogsError("The end of the window has to be after its start.")
    length_ms = max(int(fight.end_time - fight.start_time), 0)
    start_ms, end_ms = start_seconds * 1000, end_seconds * 1000
    if start_ms >= length_ms:
        raise WarcraftLogsError(f"Pull {pull} of {fight.name} lasted {pull_time(length_ms)}, so nothing happened from {pull_time(start_ms)} on.")
    cut = end_ms > length_ms
    return PullWindow(fight, pull, start_ms, min(end_ms, length_ms), cut)


def enemy_targets(actors: Sequence[Actor], enemies: Sequence[str]) -> list[list[int]]:
    """For each of `enemies` (names), in order, the IDs of every enemy of the report with that name,
    ignoring case: adds with the same name count as one enemy."""
    by_name: dict[str, list[int]] = {}
    for actor in actors:
        ids = by_name.setdefault(actor.name.strip().lower(), [])
        if actor.id not in ids:
            ids.append(actor.id)
    return [by_name.get(name.strip().lower(), []) for name in enemies]


def build_enemy_damage(name: str, target_ids: Sequence[int], tables: dict[int, list[PlayerEntry]], ignored: set[str]) -> EnemyDamage:
    """The damage done to the enemy `name`: every one of its `target_ids` added together, per player."""
    players: PlayerTotals = {}
    for target_id in target_ids:
        add_totals(players, tables.get(target_id, []), ignored)
    ranking = ranked_players(players)
    return EnemyDamage(name, bool(target_ids), sum(player.total for player in ranking), ranking)


def build_focus_damage(focus: str, enemies: Sequence[EnemyDamage]) -> FocusDamage:
    """The damage to the tracked enemy named `focus` set apart from the damage to all the other tracked
    `enemies`, which is added together, per player."""
    key = focus.strip().lower()
    chosen = next((enemy for enemy in enemies if enemy.name.strip().lower() == key), None)
    rest = [enemy for enemy in enemies if enemy is not chosen and enemy.in_report]

    # Per player: (class, damage to the enemy set apart, damage to the rest).
    players: dict[str, tuple[str, int, int]] = {}
    for enemy, set_apart in [*([(chosen, True)] if chosen is not None else []), *((other, False) for other in rest)]:
        for player in enemy.players:
            class_name, focus_total, rest_total = players.get(player.name, (player.class_name, 0, 0))
            if set_apart:
                focus_total += player.total
            else:
                rest_total += player.total
            players[player.name] = (class_name, focus_total, rest_total)

    rows = [FocusRow(name, class_name, focus_total, rest_total) for name, (class_name, focus_total, rest_total) in players.items()]
    rows.sort(key=lambda row: (-row.focus, -row.rest, row.name))
    return FocusDamage(
        name=chosen.name if chosen is not None else focus.strip(),
        in_report=chosen is not None and chosen.in_report,
        total=chosen.total if chosen is not None else 0,
        rest_names=[enemy.name for enemy in rest],
        rest_total=sum(enemy.total for enemy in rest),
        players=rows,
    )


def boss_ids(actors: Sequence[Actor]) -> list[int]:
    """The IDs of the enemies Warcraft Logs counts as bosses, each once."""
    return list(dict.fromkeys(actor.id for actor in actors if actor.boss))


def build_boss_damage(actors: Sequence[Actor], boss_tables: dict[int, list[PlayerEntry]], all_damage: Sequence[PlayerEntry], ignored: set[str]) -> BossDamage:
    """Each player's damage to the bosses (`boss_tables`, by boss ID) against their damage to everything
    else: their damage to every target (`all_damage`) less what went to the bosses."""
    to_bosses: PlayerTotals = {}
    for entries in boss_tables.values():
        add_totals(to_bosses, entries, ignored)
    to_all = totals_of(all_damage, ignored)

    rows = []
    for name in dict.fromkeys([*to_bosses, *to_all]):
        class_name, boss_total = to_bosses.get(name, (to_all.get(name, ("", 0))[0], 0))
        everything = to_all.get(name, (class_name, 0))[1]
        rows.append(BossRow(name, class_name, boss_total, max(everything - boss_total, 0)))
    rows.sort(key=lambda row: (-row.boss, -row.other, row.name))

    names_by_id = {actor.id: actor.name for actor in actors}
    hit = [names_by_id[boss_id] for boss_id, entries in boss_tables.items() if boss_id in names_by_id and any(entry.total > 0 for entry in entries)]
    return BossDamage(
        boss_names=list(dict.fromkeys(hit)),
        boss_total=sum(row.boss for row in rows),
        other_total=sum(row.other for row in rows),
        players=rows,
    )


def build_window_damage_report(
    report: Report,
    window: PullWindow,
    enemies: list[EnemyDamage],
    ignored: int,
    focus: FocusDamage | None = None,
    boss_damage: BossDamage | None = None,
) -> WindowDamageReport:
    return WindowDamageReport(
        code=report.code,
        title=report.title,
        started_at=report.started_at,
        fight_id=window.fight.id,
        boss=window.fight.name,
        pull=window.pull,
        kill=window.fight.kill,
        start_ms=window.start_ms,
        end_ms=window.end_ms,
        cut_at_pull_end=window.cut_at_pull_end,
        report_start_ms=window.report_start_ms,
        report_end_ms=window.report_end_ms,
        enemies=enemies,
        ignored=ignored,
        focus=focus,
        boss_damage=boss_damage,
    )


# --- What an officer asked for ---

_SPELL_ID = re.compile(r"\+?[0-9]+")


def parse_report_code(text: str) -> str | None:
    """The report code in what an officer typed: a bare code, or a link such as
    `https://www.warcraftlogs.com/reports/YRwPxnJ4MKpWFyrg#fight=3`."""
    text = text.strip()
    _, found, rest = text.partition("/reports/")
    code = rest if found else text
    for separator in "/?#":
        code = code.split(separator, 1)[0]
    if code and len(code) <= 32 and all(char.isascii() and char.isalnum() for char in code):
        return code
    return None


def parse_report_target(report: str | None, settings: GuildSettings) -> ReportTarget:
    """The report an officer asked for. Raises `ReportRequestProblem` saying why it can't be worked out."""
    if report is not None:
        code = parse_report_code(report)
        if code is None:
            raise ReportRequestProblem(f"Couldn't find a report code in `{report.strip()}`. Give a report link or its code.")
        return ReportCode(code)
    if settings.wcl_source is None:
        raise ReportRequestProblem("Give a report link or code, or register the uploader with `/settings wcl source` to use the newest report.")
    return NewestReport(settings.wcl_source)


def resolve_ability(tracked: list[TrackedAbility], text: str) -> TrackedAbility | None:
    """What an officer typed for an ability: a tracked ability's name (ignoring case), or a spell ID, which
    needn't be tracked (it is then shown as "Ability <id>")."""
    text = text.strip()
    for ability in tracked:
        if ascii_lower(ability.name) == ascii_lower(text):
            return ability

    if not _SPELL_ID.fullmatch(text) or not text.isascii():
        return None
    ability_id = int(text)
    if not 0 < ability_id <= 0xFFFFFFFF:
        return None
    return next((ability for ability in tracked if ability.ability_id == ability_id), TrackedAbility(ability_id, f"Ability {ability_id}"))
