"""The Warcraft Logs overviews into the embeds `/wcl` posts."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..core.embed import MAX_DESCRIPTION_CHARS, MAX_EMBED_CHARS, MAX_FIELD_CHARS, MAX_TITLE_CHARS, Embed, add_field, new_embed
from ..core.text import plural, truncate_with_ellipsis, utf8_length
from ..schema.wcl_report_schema import (
    WIPE_CUTOFF,
    WIPE_DEATHS,
    WIPE_WINDOW_MS,
    AbilityDamage,
    ConsumablesReport,
    ConsumableUse,
    DamageReport,
    LastWipe,
    PlayerCount,
    PlayerTotal,
    ReportOverview,
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


def report_url(code: str) -> str:
    return f"https://www.warcraftlogs.com/reports/{code}"


def replay_url(wipe: LastWipe) -> str:
    """The link to the replay of a pull, opened `position_ms` into the pull."""
    # Rounded half away from zero, as the Rust bot did.
    position = math.floor(wipe.position_ms + 0.5)
    return f"{report_url(wipe.code)}?fight={wipe.fight_id}&view=replay&position={position}"


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
