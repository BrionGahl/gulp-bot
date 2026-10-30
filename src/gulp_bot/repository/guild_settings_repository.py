"""The `guild_settings`, `guild_clips_channels` and `guild_replay_users` tables."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.cron import Schedule
from ..core.database import from_bigint, rows_affected, to_bigint, to_bigint_or_none
from ..mapper.guild_settings_mapper import guild_id_from_row, settings_from_row
from ..schema.guild_settings_schema import AiSettings, GuildIdSetting, GuildSettings, RaidNights, RaidVoice, WclCredentials, WclSource, WowUtilsCredentials

if TYPE_CHECKING:
    import asyncpg

SETTINGS_COLUMNS = (
    "guild_id, mod_role_id, dev_role_id, raider_role_id, trial_role_id, personal_officer_category_id, "
    "raid_notes_channel_id, ai_disabled, ai_maintenance, ai_model, ai_think, "
    "bart_token, wowutils_token, wowutils_group_id, raid_notes_cron, "
    "raid_voice_channel_id, raid_voice_start_cron, raid_voice_end_cron, raid_days, raid_start, raid_end, "
    "wcl_guild_id, wcl_uploader_id, wcl_client_id, wcl_client_secret"
)


class GuildSettingsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def load_all(self) -> dict[int, GuildSettings]:
        """Every guild's settings, with its clips channels and replay users."""
        settings = {guild_id_from_row(row): settings_from_row(row) for row in await self._pool.fetch(f"SELECT {SETTINGS_COLUMNS} FROM guild_settings")}
        for row in await self._pool.fetch("SELECT guild_id, channel_id FROM guild_clips_channels"):
            settings.setdefault(guild_id_from_row(row), GuildSettings()).clips_channel_ids.add(from_bigint(row["channel_id"]))
        for row in await self._pool.fetch("SELECT guild_id, user_id FROM guild_replay_users"):
            settings.setdefault(guild_id_from_row(row), GuildSettings()).replay_user_ids.add(from_bigint(row["user_id"]))
        return settings

    async def _upsert(self, guild_id: int, values: dict[str, object]) -> None:
        """Writes `values` (column: value) into the guild's row, creating the row if it has none. The column
        names are this module's own, never a member's input."""
        columns = list(values)
        placeholders = ", ".join(f"${index}" for index in range(2, len(columns) + 2))
        updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns)
        await self._pool.execute(
            f"INSERT INTO guild_settings (guild_id, {', '.join(columns)}) VALUES ($1, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates}, updated_at = now()",
            to_bigint(guild_id),
            *values.values(),
        )

    async def save_id_setting(self, guild_id: int, setting: GuildIdSetting, value: int | None) -> None:
        await self._upsert(guild_id, {setting.column: to_bigint_or_none(value)})

    async def save_ai_settings(self, guild_id: int, ai: AiSettings) -> None:
        await self._upsert(
            guild_id,
            {
                "ai_disabled": ai.disabled,
                "ai_maintenance": ai.maintenance,
                "ai_model": ai.model,
                "ai_think": None if ai.think is None else ai.think.value,
            },
        )

    async def save_wcl_source(self, guild_id: int, source: WclSource | None) -> None:
        await self._upsert(
            guild_id,
            {"wcl_guild_id": None if source is None else source.guild_id, "wcl_uploader_id": None if source is None else source.uploader_id},
        )

    async def save_wcl_credentials(self, guild_id: int, credentials: WclCredentials | None) -> None:
        await self._upsert(
            guild_id,
            {
                "wcl_client_id": None if credentials is None else credentials.client_id,
                "wcl_client_secret": None if credentials is None else credentials.client_secret,
            },
        )

    async def save_bart_token(self, guild_id: int, token: str | None) -> None:
        await self._upsert(guild_id, {"bart_token": token})

    async def save_wowutils(self, guild_id: int, credentials: WowUtilsCredentials | None) -> None:
        await self._upsert(
            guild_id,
            {"wowutils_token": None if credentials is None else credentials.token, "wowutils_group_id": None if credentials is None else credentials.group_id},
        )

    async def save_raid_notes_schedule(self, guild_id: int, schedule: Schedule | None) -> None:
        await self._upsert(guild_id, {"raid_notes_cron": None if schedule is None else schedule.source})

    async def save_raid_nights(self, guild_id: int, nights: RaidNights | None) -> None:
        await self._upsert(
            guild_id,
            {
                "raid_days": None if nights is None else sorted(nights.days),
                "raid_start": None if nights is None else nights.start,
                "raid_end": None if nights is None else nights.end,
            },
        )

    async def save_raid_voice(self, guild_id: int, raid_voice: RaidVoice | None) -> None:
        await self._upsert(
            guild_id,
            {
                "raid_voice_channel_id": None if raid_voice is None else to_bigint(raid_voice.channel_id),
                "raid_voice_start_cron": None if raid_voice is None else raid_voice.start.source,
                "raid_voice_end_cron": None if raid_voice is None else raid_voice.end.source,
            },
        )

    async def add_replay_user(self, guild_id: int, user_id: int) -> None:
        await self._pool.execute(
            "INSERT INTO guild_replay_users (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            to_bigint(guild_id),
            to_bigint(user_id),
        )

    async def remove_replay_user(self, guild_id: int, user_id: int) -> bool:
        status = await self._pool.execute("DELETE FROM guild_replay_users WHERE guild_id = $1 AND user_id = $2", to_bigint(guild_id), to_bigint(user_id))
        return rows_affected(status) > 0

    async def add_clips_channel(self, guild_id: int, channel_id: int) -> None:
        await self._pool.execute(
            "INSERT INTO guild_clips_channels (guild_id, channel_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            to_bigint(guild_id),
            to_bigint(channel_id),
        )

    async def remove_clips_channel(self, guild_id: int, channel_id: int) -> bool:
        status = await self._pool.execute(
            "DELETE FROM guild_clips_channels WHERE guild_id = $1 AND channel_id = $2", to_bigint(guild_id), to_bigint(channel_id)
        )
        return rows_affected(status) > 0
