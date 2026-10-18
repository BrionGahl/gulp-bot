"""The Warcraft Logs overviews into the embeds `/wcl` posts."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..core.embed import MAX_DESCRIPTION_CHARS, MAX_EMBED_CHARS, MAX_FIELD_CHARS, MAX_TITLE_CHARS, Embed, add_field, new_embed
from ..core.text import plural, pull_time, truncate_with_ellipsis, utf8_length
from ..schema.wcl_report_schema import (
    WIPE_CUTOFF,
    WIPE_DEATHS,
    WIPE_WINDOW_MS,
    AbilityDamage,
    BossDamage,
    ConsumablesReport,
    ConsumableUse,
    DamageReport,
    EnemyDamage,
    FocusDamage,
    LastWipe,
    PlayerCount,
    PlayerTotal,
    ReportOverview,
    WindowDamageReport,
)

# Room kept free in the overview for the footer note that says abilities were left out.
FOOTER_NOTE_RESERVE = 100
# The longest an ability name is shown as, in a title or a field name (Discord allows 256).
MAX_ABILITY_NAME_CHARS = 100

# How many players each ranking shows.
MOST_DEATHS_SHOWN = 10
FIRST_DEATHS_SHOWN = 5
DAMAGE_SHOWN_IN_OVERVIEW = 5
DAMAGE_SHOWN = 15
CONSUMABLE_PLAYERS_SHOWN = 10
WINDOW_DAMAGE_SHOWN = 20


def report_url(code: str) -> str:
    return f"https://www.warcraftlogs.com/reports/{code}"


def replay_url(wipe: LastWipe) -> str:
    """The link to the replay of a pull, opened `position_ms` into the pull."""
    # Rounded half away from zero, as the Rust bot did.
    position = math.floor(wipe.position_ms + 0.5)
    return f"{report_url(wipe.code)}?fight={wipe.fight_id}&view=replay&position={position}"


def window_url(report: WindowDamageReport) -> str:
    """The link to the Damage Done tab of the pull, narrowed to the same window."""
    return f"{report_url(report.code)}?fight={report.fight_id}&type=damage-done&start={report.report_start_ms}&end={report.report_end_ms}"


# --- /wcl report ---


def overview_embed(overview: ReportOverview) -> Embed:
    title = truncate_with_ellipsis(overview.title, MAX_TITLE_CHARS)
    description = overview_description(overview)

    fields: list[tuple[str, str]] = []
    if overview.deaths:
        fields.append(("Most deaths", ranked_list(death_rows(overview.deaths), MOST_DEATHS_SHOWN, MAX_FIELD_CHARS)))
        fields.append(("First to die in a pull", ranked_list(death_rows(overview.first_deaths), FIRST_DEATHS_SHOWN, MAX_FIELD_CHARS)))

    # An embed holds at most 6000 characters in all, so tracked abilities are added while they fit.
    used = embed_chars(title, description, footer_text(overview.ignored), fields) + FOOTER_NOTE_RESERVE
    left_out = 0
    pulls = sum(encounter.pulls for encounter in overview.encounters)
    for ability in overview.damage:
        name, value = damage_field(ability, DAMAGE_SHOWN_IN_OVERVIEW, pulls)
        size = len(name) + len(value)
        if used + size > MAX_EMBED_CHARS:
            left_out += 1
            continue
        used += size
        fields.append((name, value))

    footer = footer_text(overview.ignored)
    if left_out > 0:
        footer = f"{footer} {left_out} more tracked abilities didn't fit; see /wcl damage_taken."

    embed = new_embed(title=title, url=report_url(overview.code), description=description, footer=footer)
    for name, value in fields:
        add_field(embed, name, value)
    return embed


def overview_description(overview: ReportOverview) -> str:
    pulls = sum(encounter.pulls for encounter in overview.encounters)
    if pulls == 0:
        return f"<t:{overview.started_at}:F>\nNo Mythic boss pulls in this report."

    kills = sum(encounter.kills for encounter in overview.encounters)
    description = f"<t:{overview.started_at}:F>\n**{pulls}** Mythic pulls: {kills} kills, {pulls - kills} wipes\n"
    for listed, encounter in enumerate(overview.encounters):
        line = f"\n• {encounter.name} — {encounter.pulls} pulls, {encounter.kills} kills"
        overflow_note = f"\n…and {len(overview.encounters) - listed} more."
        if utf8_length(description) + utf8_length(line) + utf8_length(overflow_note) > MAX_DESCRIPTION_CHARS:
            description += overflow_note
            break
        description += line
    return description


def damage_field(ability: AbilityDamage, shown: int, pulls: int) -> tuple[str, str]:
    """The field for one tracked ability: its title, the total damage taken from it over all `pulls` of
    the night, and the players who took the most of it."""
    name = f"{truncate_with_ellipsis(ability.name, MAX_ABILITY_NAME_CHARS)} — damage taken"
    if not ability.players:
        return name, "Nobody took damage from this. If that's unexpected, check its ability ID."
    total = f"Total: **{format_damage(ability.total)}** over {plural(pulls, 'pull')}\n"
    return name, total + ranked_list(damage_rows(ability), shown, MAX_FIELD_CHARS - utf8_length(total))


# --- /wcl damage_taken ---


def damage_embed(report: DamageReport) -> Embed:
    ability = report.ability
    report_title = truncate_with_ellipsis(report.title, MAX_ABILITY_NAME_CHARS)
    embed = new_embed(
        title=f"{truncate_with_ellipsis(ability.name, MAX_ABILITY_NAME_CHARS)} — damage taken",
        url=report_url(report.code),
        footer=footer_text(report.ignored),
    )
    if report.pulls == 0:
        embed["description"] = f"<t:{report.started_at}:F> · {report_title}\nNo Mythic boss pulls in this report."
        return embed

    description = (
        f"<t:{report.started_at}:F> · {report_title}\n**{report.scope}**, {report.pulls} pulls\nTotal damage taken: **{format_damage(ability.total)}**"
    )
    # Cut so that however many bosses the scope names, the description stays within Discord's limit.
    embed["description"] = truncate_with_ellipsis(description, MAX_DESCRIPTION_CHARS)

    if not ability.players:
        add_field(
            embed,
            "Who took it",
            "Nobody took damage from this in those pulls. If that's unexpected, check the ability ID: Warcraft Logs "
            "can log an ability's damage under a different ID than its spell page shows.",
        )
        return embed
    add_field(embed, "Who took it", ranked_list(damage_rows(ability), DAMAGE_SHOWN, MAX_FIELD_CHARS))
    return embed


# --- /wcl consumables ---


def consumables_embed(report: ConsumablesReport) -> Embed:
    embed = new_embed(title="Consumables used", url=report_url(report.code), footer=consumables_footer(report.ignored))
    report_title = truncate_with_ellipsis(report.title, MAX_ABILITY_NAME_CHARS)
    if report.pulls == 0:
        embed["description"] = f"<t:{report.started_at}:F> · {report_title}\nNo Mythic boss pulls in this report."
        return embed

    # Cut so that however many bosses the scope names, the description stays within Discord's limit.
    description = f"<t:{report.started_at}:F> · {report_title}\n**{report.scope}**, {plural(report.pulls, 'pull')}"
    embed["description"] = truncate_with_ellipsis(description, MAX_DESCRIPTION_CHARS)
    for used in report.uses:
        add_field(embed, *consumable_field(used))
    return embed


def consumable_field(used: ConsumableUse) -> tuple[str, str]:
    """The field for one kind of consumable: how many were used in all, and by whom. Always has a value,
    since Discord turns down an empty one."""
    if not used.registered:
        return used.kind.plural, "None registered. Add one with `/settings wcl consumable_add`."
    name = f"{used.kind.plural} — {used.total} used"
    if not used.players:
        return name, "None used."
    return name, ranked_list(count_rows(used.players), CONSUMABLE_PLAYERS_SHOWN, MAX_FIELD_CHARS)


# --- /wcl damage_window ---


def window_damage_embed(report: WindowDamageReport) -> Embed:
    window = f"{pull_time(report.start_ms)}–{pull_time(report.end_ms)}"
    title = truncate_with_ellipsis(f"{report.boss} — pull {report.pull}, {window}", MAX_TITLE_CHARS)
    outcome = "" if report.kill is None else (" (kill)" if report.kill else " (wipe)")
    target = "the bosses and everything else" if report.boss_damage is not None else "the tracked enemies"
    description = (
        f"<t:{report.started_at}:F> · {truncate_with_ellipsis(report.title, MAX_ABILITY_NAME_CHARS)}\n"
        f"Damage done to {target} from **{pull_time(report.start_ms)}** to **{pull_time(report.end_ms)}** of pull {report.pull}{outcome}"
    )
    if report.cut_at_pull_end:
        description += f"\nThe pull ended at {pull_time(report.end_ms)}, so the window stops there."
    if report.boss_damage is not None:
        return boss_damage_embed(report, report.boss_damage, title, description)
    footer = f"Enemies with the same name count as one.{ignored_note(report.ignored)}"
    if report.focus is not None:
        return focus_embed(report, report.focus, title, description, footer)

    names = [enemy_field_name(enemy) for enemy in report.enemies]
    # The fields share what is left of Discord's limit on the whole embed.
    room = MAX_EMBED_CHARS - embed_chars(title, description, footer, [(name, "") for name in names])
    per_field = min(MAX_FIELD_CHARS, room // max(len(names), 1))

    embed = new_embed(title=title, url=window_url(report), description=description, footer=footer)
    for name, enemy in zip(names, report.enemies, strict=True):
        add_field(embed, name, enemy_field_value(enemy, per_field))
    return embed


def focus_embed(report: WindowDamageReport, focus: FocusDamage, title: str, description: str, footer: str) -> Embed:
    """The embed with one enemy set apart: a single ranking, each player's damage to it and to the rest."""
    description += f"\n**{truncate_with_ellipsis(focus.name, MAX_ABILITY_NAME_CHARS)}** set apart; the rest is {rest_text(focus.rest_names)}."
    missing = [enemy.name for enemy in report.enemies if not enemy.in_report]
    if missing:
        description += f"\nNot in this report: {', '.join(missing)}."
    description = truncate_with_ellipsis(description, MAX_DESCRIPTION_CHARS)

    embed = new_embed(title=title, url=window_url(report), description=description, footer=footer)
    name = truncate_with_ellipsis(f"{focus.name} — {format_damage(focus.total)} · the rest — {format_damage(focus.rest_total)}", MAX_TITLE_CHARS)
    add_field(embed, name, focus_field_value(focus))
    return embed


def boss_damage_embed(report: WindowDamageReport, split: BossDamage, title: str, description: str) -> Embed:
    """The embed of boss against non-boss damage: a single ranking, each player's damage to the bosses and to
    everything else."""
    if split.boss_names:
        bosses = ", ".join(truncate_with_ellipsis(name, MAX_ABILITY_NAME_CHARS) for name in split.boss_names)
        description += f"\nBosses: {bosses}."
    description = truncate_with_ellipsis(description, MAX_DESCRIPTION_CHARS)
    footer = f"Boss damage as Warcraft Logs counts it; non-boss is everything else the players damaged.{ignored_note(report.ignored)}"

    embed = new_embed(title=title, url=window_url(report), description=description, footer=footer)
    name = f"Boss — {format_damage(split.boss_total)} · non-boss — {format_damage(split.other_total)}"
    if not split.players:
        value = "Nobody did any damage in this window."
    else:
        rows = [RankingRow(row.name, row.class_name, f"{format_damage(row.boss)} · non-boss {format_damage(row.other)}") for row in split.players]
        value = ranked_list(rows, WINDOW_DAMAGE_SHOWN, MAX_FIELD_CHARS)
    add_field(embed, name, value)
    return embed


def rest_text(names: Sequence[str]) -> str:
    """The other tracked enemies, as a sentence says them: `A`, `A and B`, `A, B and C`."""
    if not names:
        return "nothing: no other tracked enemy is in this report"
    shown = [truncate_with_ellipsis(name, MAX_ABILITY_NAME_CHARS) for name in names]
    return shown[0] if len(shown) == 1 else f"{', '.join(shown[:-1])} and {shown[-1]}"


def focus_field_value(focus: FocusDamage) -> str:
    """Who damaged the enemy set apart and the rest, most to the enemy set apart first. Always has a value."""
    if not focus.in_report:
        return "Not in this report. Check the name against the report's Damage Done tab, by target."
    if not focus.players:
        return "Nobody damaged the tracked enemies in this window."
    rows = [RankingRow(row.name, row.class_name, f"{format_damage(row.focus)} · rest {format_damage(row.rest)}") for row in focus.players]
    return ranked_list(rows, WINDOW_DAMAGE_SHOWN, MAX_FIELD_CHARS)


def enemy_field_name(enemy: EnemyDamage) -> str:
    name = truncate_with_ellipsis(enemy.name, MAX_ABILITY_NAME_CHARS)
    if not enemy.in_report or not enemy.players:
        return name
    return f"{name} — {format_damage(enemy.total)} damage"


def enemy_field_value(enemy: EnemyDamage, max_chars: int) -> str:
    """Who damaged the enemy, most first. Always has a value, since Discord turns down an empty one."""
    if not enemy.in_report:
        return "Not in this report. Check the name against the report's Damage Done tab, by target."
    if not enemy.players:
        return "Nobody damaged it in this window."
    return ranked_list(amount_rows(enemy.players), WINDOW_DAMAGE_SHOWN, max_chars)


# --- /wcl replay ---


def replay_embed(wipe: LastWipe) -> Embed:
    minutes = math.floor(wipe.position_ms / 60_000.0)
    seconds = math.floor((wipe.position_ms / 1000.0) % 60.0)
    if wipe.wipe_found:
        how = f"Starts 5 seconds before {WIPE_DEATHS} players died within {WIPE_WINDOW_MS / 1000.0:g} seconds."
    else:
        how = "Nobody died together, so it starts near the end of the pull."
    return new_embed(
        title=truncate_with_ellipsis(f"{wipe.boss} — pull {wipe.pull}, wipe", MAX_TITLE_CHARS),
        url=replay_url(wipe),
        description=f"[Open the replay]({replay_url(wipe)})\n{how} That is {minutes}:{seconds:02} into the pull.",
    )


# --- Footers ---


def footer_text(ignored: int) -> str:
    """The footer of the overview and damage embeds: what is counted, and how many players are left out."""
    return f"Only the first {WIPE_CUTOFF} deaths in each pull count, and damage taken up to the last of them.{ignored_note(ignored)}"


def consumables_footer(ignored: int) -> str:
    return f"Every use in every Mythic pull counts, including once the raid had started wiping.{ignored_note(ignored)}"


def ignored_note(ignored: int) -> str:
    """A sentence for a footer saying how many players are left out, or nothing if none are."""
    if ignored == 0:
        return ""
    if ignored == 1:
        return " 1 ignored player is left out."
    return f" {ignored} ignored players are left out."


def embed_chars(title: str, description: str, footer: str, fields: Sequence[tuple[str, str]]) -> int:
    """The characters of an embed that count towards Discord's limit on its total size."""
    return len(title) + len(description) + len(footer) + sum(len(name) + len(value) for name, value in fields)


# --- Rankings ---


@dataclass(frozen=True)
class RankingRow:
    """One line of a ranking: a player, their class, and what is being counted."""

    name: str
    class_name: str
    value: str


def death_rows(players: Sequence[PlayerCount]) -> list[RankingRow]:
    return [RankingRow(player.name, player.class_name, str(player.count)) for player in players]


def count_rows(players: Sequence[PlayerTotal]) -> list[RankingRow]:
    """Each player's count, for a ranking of how many they used."""
    return [RankingRow(player.name, player.class_name, str(player.total)) for player in players]


def amount_rows(players: Sequence[PlayerTotal]) -> list[RankingRow]:
    """Each player's damage, for a ranking of how much they did."""
    return [RankingRow(player.name, player.class_name, format_damage(player.total)) for player in players]


def damage_rows(ability: AbilityDamage) -> list[RankingRow]:
    """Each player's damage and their share of everything taken from the ability."""
    rows = []
    for player in ability.players:
        share = 0.0 if ability.total == 0 else player.total * 100.0 / ability.total
        rows.append(RankingRow(player.name, player.class_name, f"{format_damage(player.total)} ({share:.1f}%)"))
    return rows


def ranked_list(rows: Sequence[RankingRow], max_lines: int, max_chars: int) -> str:
    """The top `max_lines` of a ranking, one numbered line each, in at most `max_chars`. If players are
    left off, the list ends with a line saying how many."""
    text = ""
    for index, row in enumerate(rows):
        class_name = f" ({display_class(row.class_name)})" if row.class_name else ""
        line = f"{index + 1}. **{row.name}**{class_name} — {row.value}"
        separator = "" if index == 0 else "\n"
        overflow_note = f"\n…and {len(rows) - index} more."
        if index >= max_lines or utf8_length(text) + utf8_length(separator) + utf8_length(line) + utf8_length(overflow_note) > max_chars:
            text += overflow_note
            break
        text += separator + line
    return text


def format_damage(amount: int) -> str:
    """Damage as a short number: 3.5B, 200.3M, 12.3K."""
    if amount >= 1_000_000_000:
        return f"{amount / 1e9:.1f}B"
    if amount >= 1_000_000:
        return f"{amount / 1e6:.1f}M"
    if amount >= 1_000:
        return f"{amount / 1e3:.1f}K"
    return str(amount)


def display_class(class_name: str) -> str:
    """ "DeathKnight" as "Death Knight"."""
    return "".join(f" {char}" if index > 0 and char.isupper() else char for index, char in enumerate(class_name))
