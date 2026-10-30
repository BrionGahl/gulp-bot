"""`guild_settings` rows into `GuildSettings`, and `GuildSettings` into the `/settings show` embed."""

from __future__ import annotations

import logging

from ..core.cron import Schedule
from ..core.database import Row, from_bigint, from_bigint_or_none
from ..core.embed import Embed, add_field, command_embed
from ..core.schedule_parsing import describe_schedule, parse_stored_schedule
from ..schema.guild_settings_schema import (
    AiSettings,
    ClearableSetting,
    GuildIdSetting,
    GuildSettings,
    RaidNights,
    RaidVoice,
    ThinkSetting,
    WclCredentials,
    WclSource,
)
from ..schema.wcl_settings_schema import MAX_WCL_ID, ConsumableAbility, ConsumableKind, TrackedAbility
from .raid_nights_mapper import describe_raid_nights

log = logging.getLogger(__name__)


def guild_id_from_row(row: Row) -> int:
    return from_bigint(row["guild_id"])


def settings_from_row(row: Row) -> GuildSettings:
    """One `guild_settings` row. A stored schedule that can't be read (only possible if the row was edited
    by hand) turns its feature off for that guild rather than stopping the bot."""
    guild_id = guild_id_from_row(row)
    return GuildSettings(
        mod_role_id=from_bigint_or_none(row["mod_role_id"]),
        dev_role_id=from_bigint_or_none(row["dev_role_id"]),
        raider_role_id=from_bigint_or_none(row["raider_role_id"]),
        trial_role_id=from_bigint_or_none(row["trial_role_id"]),
        personal_officer_category_id=from_bigint_or_none(row["personal_officer_category_id"]),
        raid_notes_channel_id=from_bigint_or_none(row["raid_notes_channel_id"]),
        bart_token=row["bart_token"],
        wowutils_token=row["wowutils_token"],
        wowutils_group_id=row["wowutils_group_id"],
        raid_notes_cron=_raid_notes_schedule(row, guild_id),
        raid_nights=_raid_nights(row, guild_id),
        raid_voice=_raid_voice(row, guild_id),
        wcl_source=_wcl_source(row),
        wcl_credentials=_wcl_credentials(row),
        ai=AiSettings(disabled=row["ai_disabled"], maintenance=row["ai_maintenance"], model=row["ai_model"], think=_think(row["ai_think"])),
    )


def _think(value: str | None) -> ThinkSetting | None:
    """The stored `/ai think` choice; one the bot doesn't know is taken as none, the configured one."""
    try:
        return None if value is None else ThinkSetting(value)
    except ValueError:
        log.warning("ignoring the unknown /ai think setting %r", value)
        return None


def _raid_notes_schedule(row: Row, guild_id: int) -> Schedule | None:
    expression = row["raid_notes_cron"]
    if expression is None:
        return None
    schedule = parse_stored_schedule(expression)
    if schedule is None:
        # Falls back to the default schedule.
        log.warning("Ignoring unreadable raid notes cron `%s` for guild %s", expression, guild_id)
    return schedule


def _raid_nights(row: Row, guild_id: int) -> RaidNights | None:
    days, start, end = row["raid_days"], row["raid_start"], row["raid_end"]
    if days is None or start is None or end is None:
        return None
    # Only possible if the row was edited by hand: raid nights are checked before they are stored.
    if not days or any(not 0 <= day <= 6 for day in days) or start == end:
        log.warning("Ignoring unreadable raid nights (%s, %s - %s) for guild %s", days, start, end, guild_id)
        return None
    return RaidNights(frozenset(days), start, end)


def _raid_voice(row: Row, guild_id: int) -> RaidVoice | None:
    channel, start, end = row["raid_voice_channel_id"], row["raid_voice_start_cron"], row["raid_voice_end_cron"]
    if channel is None or start is None or end is None:
        return None
    start_schedule, end_schedule = parse_stored_schedule(start), parse_stored_schedule(end)
    if start_schedule is None or end_schedule is None:
        log.warning("Ignoring unreadable raid attendance schedule (`%s` / `%s`) for guild %s", start, end, guild_id)
        return None
    return RaidVoice(from_bigint(channel), start_schedule, end_schedule)


def _wcl_id(value: int | None) -> int | None:
    """A Warcraft Logs ID (`MAX_WCL_ID`); anything else was edited in by hand and is ignored."""
    return value if value is not None and 0 <= value <= MAX_WCL_ID else None


def _wcl_source(row: Row) -> WclSource | None:
    guild, uploader = _wcl_id(row["wcl_guild_id"]), _wcl_id(row["wcl_uploader_id"])
    return WclSource(guild, uploader) if guild is not None and uploader is not None else None


def _wcl_credentials(row: Row) -> WclCredentials | None:
    client_id, client_secret = row["wcl_client_id"], row["wcl_client_secret"]
    return WclCredentials(client_id, client_secret) if client_id is not None and client_secret is not None else None


# --- /settings show ---


def settings_overview_embed(
    bot_name: str,
    settings: GuildSettings,
    default_raid_notes_schedule: Schedule,
    tracked_abilities: list[TrackedAbility],
    ignored_players: int,
    consumables: list[ConsumableAbility],
) -> Embed:
    """Everything registered for a server. Credentials are never echoed back, only whether they're set."""

    def role(role_id: int | None) -> str:
        return "Not set" if role_id is None else f"<@&{role_id}>"

    def channel(channel_id: int | None) -> str:
        return "Not set" if channel_id is None else f"<#{channel_id}>"

    def is_set(value: object) -> str:
        return "Set" if value is not None else "Not set"

    embed = command_embed(bot_name, title="Server Settings")
    add_field(embed, GuildIdSetting.MOD_ROLE.value, role(settings.mod_role_id), inline=True)
    add_field(embed, GuildIdSetting.DEV_ROLE.value, role(settings.dev_role_id), inline=True)
    add_field(embed, GuildIdSetting.RAIDER_ROLE.value, role(settings.raider_role_id), inline=True)
    add_field(embed, GuildIdSetting.TRIAL_ROLE.value, role(settings.trial_role_id), inline=True)
    add_field(embed, GuildIdSetting.PERSONAL_OFFICER_CATEGORY.value, channel(settings.personal_officer_category_id), inline=True)
    add_field(embed, GuildIdSetting.RAID_NOTES_CHANNEL.value, channel(settings.raid_notes_channel_id), inline=True)
    add_field(embed, ClearableSetting.RAID_NOTES_SCHEDULE.label, _raid_notes_schedule_text(settings, default_raid_notes_schedule), inline=True)
    add_field(
        embed, ClearableSetting.RAID_NIGHTS.label, "Not set" if settings.raid_nights is None else describe_raid_nights(settings.raid_nights), inline=False
    )
    add_field(embed, ClearableSetting.RAID_VOICE.label, _raid_voice_text(settings), inline=False)
    add_field(embed, "Clips channels", ", ".join(f"<#{channel_id}>" for channel_id in sorted(settings.clips_channel_ids)) or "None", inline=False)
    add_field(embed, "Replay users", ", ".join(f"<@{user_id}>" for user_id in sorted(settings.replay_user_ids)) or "None", inline=False)
    add_field(embed, ClearableSetting.BART_TOKEN.label, is_set(settings.bart_token), inline=True)
    add_field(embed, ClearableSetting.WOWUTILS.label, is_set(settings.wowutils_token), inline=True)
    add_field(embed, ClearableSetting.WCL_SOURCE.label, _wcl_source_text(settings), inline=True)
    add_field(embed, ClearableSetting.WCL_CLIENT.label, is_set(settings.wcl_credentials), inline=True)
    # Names are limited when they're added, so the list always fits in one field.
    add_field(embed, "Warcraft Logs abilities", ", ".join(ability.name for ability in tracked_abilities) or "None", inline=False)
    # Only how many: the names can add up to more than a field holds, and `/settings wcl ignore_list` has them.
    add_field(embed, "Warcraft Logs ignored players", {0: "None", 1: "1 player"}.get(ignored_players, f"{ignored_players} players"), inline=True)
    add_field(embed, "Warcraft Logs consumables", _consumables_text(consumables), inline=True)
    return embed


def _raid_notes_schedule_text(settings: GuildSettings, default_raid_notes_schedule: Schedule) -> str:
    if settings.raid_notes_cron is not None:
        return describe_schedule(settings.raid_notes_cron)
    return f"Default ({describe_schedule(default_raid_notes_schedule)})"


def _raid_voice_text(settings: GuildSettings) -> str:
    voice = settings.raid_voice
    if voice is None:
        return "Not set"
    if settings.raid_nights is not None:
        return f"<#{voice.channel_id}> — at the start and end of each raid night"
    return f"<#{voice.channel_id}> — start {describe_schedule(voice.start)}, end {describe_schedule(voice.end)} (set the raid nights to follow them instead)"


def _wcl_source_text(settings: GuildSettings) -> str:
    source = settings.wcl_source
    return "Not set" if source is None else f"Guild {source.guild_id}, uploader {source.uploader_id}"


def _consumables_text(consumables: list[ConsumableAbility]) -> str:
    lines = []
    for kind in ConsumableKind:
        ids = [entry.ability_id for entry in consumables if entry.kind is kind]
        if ids:
            lines.append(f"{kind.plural}: {', '.join(map(str, ids))}")
    return "\n".join(lines) or "None"
