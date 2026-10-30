"""Every guild's settings: kept in the database, read from memory.

Settings are read on every message the bot sees (clips channels, delete-and-replay), so reads are served
from an in-memory copy. The copy is loaded once at startup and updated on every write; that is only
correct because a single bot process owns the database.
"""

from __future__ import annotations

import dataclasses

from ..core.cron import Schedule
from ..repository.guild_settings_repository import GuildSettingsRepository
from ..schema.guild_settings_schema import (
    AiSettings,
    ClearableSetting,
    GuildIdSetting,
    GuildSettings,
    RaidNights,
    RaidVoice,
    WclCredentials,
    WclSource,
    WowUtilsCredentials,
)
from .raid_nights_service import end_schedule, start_schedule


class GuildSettingsService:
    def __init__(self, repository: GuildSettingsRepository, cache: dict[int, GuildSettings]):
        self._repository = repository
        self._cache = cache

    @classmethod
    async def load(cls, repository: GuildSettingsRepository) -> GuildSettingsService:
        return cls(repository, await repository.load_all())

    # --- Reading ---

    def get(self, guild_id: int) -> GuildSettings:
        """The settings registered for `guild_id`, or all-unset defaults if it has none. Lookups are always
        keyed by the guild an event came from, so an ID registered in one server is never applied in
        another. A copy: changing it changes nothing."""
        settings = self._cache.get(guild_id)
        return GuildSettings() if settings is None else settings.copy()

    def all(self) -> list[tuple[int, GuildSettings]]:
        """Every guild that has settings, for the scheduled jobs that aren't driven by a guild event."""
        return [(guild_id, settings.copy()) for guild_id, settings in self._cache.items()]

    def _cached(self, guild_id: int) -> GuildSettings:
        return self._cache.setdefault(guild_id, GuildSettings())

    # --- Writing: each saves to the database, then to the in-memory copy ---

    async def set_id_setting(self, guild_id: int, setting: GuildIdSetting, value: int | None) -> None:
        """Registers `value` for `setting` in `guild_id`, or clears the setting when `value` is `None`."""
        await self._repository.save_id_setting(guild_id, setting, value)
        setattr(self._cached(guild_id), setting.column, value)

    async def set_ai(self, guild_id: int, ai: AiSettings) -> None:
        """Saves how the AI features have been switched in this guild (`/ai`). Takes the whole of it, so a
        caller reads the current value, changes what it means to and writes it back."""
        await self._repository.save_ai_settings(guild_id, ai)
        self._cached(guild_id).ai = dataclasses.replace(ai)

    async def set_wcl_source(self, guild_id: int, source: WclSource | None) -> None:
        await self._repository.save_wcl_source(guild_id, source)
        self._cached(guild_id).wcl_source = source

    async def set_wcl_credentials(self, guild_id: int, credentials: WclCredentials | None) -> None:
        await self._repository.save_wcl_credentials(guild_id, credentials)
        self._cached(guild_id).wcl_credentials = credentials

    async def set_bart_token(self, guild_id: int, token: str | None) -> None:
        await self._repository.save_bart_token(guild_id, token)
        self._cached(guild_id).bart_token = token

    async def set_wowutils(self, guild_id: int, credentials: WowUtilsCredentials | None) -> None:
        """Registers this guild's WowUtils token and group together, or clears both when `None`."""
        await self._repository.save_wowutils(guild_id, credentials)
        settings = self._cached(guild_id)
        settings.wowutils_token = None if credentials is None else credentials.token
        settings.wowutils_group_id = None if credentials is None else credentials.group_id

    async def set_raid_notes_schedule(self, guild_id: int, schedule: Schedule | None) -> None:
        """Registers this guild's raid notes schedule, or reverts it to the default when `None`."""
        await self._repository.save_raid_notes_schedule(guild_id, schedule)
        self._cached(guild_id).raid_notes_cron = schedule

    async def set_raid_nights(self, guild_id: int, nights: RaidNights | None) -> None:
        """Registers the nights this guild raids, or forgets them when `None`. Raid attendance follows
        them; its stored schedules are kept in step too, so that they still say when it is recorded."""
        await self._repository.save_raid_nights(guild_id, nights)
        settings = self._cached(guild_id)
        settings.raid_nights = nights
        if nights is not None and settings.raid_voice is not None:
            await self.set_raid_voice(guild_id, RaidVoice(settings.raid_voice.channel_id, start_schedule(nights), end_schedule(nights)))

    async def set_raid_voice(self, guild_id: int, raid_voice: RaidVoice | None) -> None:
        """Registers this guild's raid voice channel and schedules, or turns attendance off when `None`."""
        await self._repository.save_raid_voice(guild_id, raid_voice)
        self._cached(guild_id).raid_voice = raid_voice

    async def add_replay_user(self, guild_id: int, user_id: int) -> None:
        await self._repository.add_replay_user(guild_id, user_id)
        self._cached(guild_id).replay_user_ids.add(user_id)

    async def remove_replay_user(self, guild_id: int, user_id: int) -> bool:
        """Returns whether `user_id` was registered as a replay user before this call."""
        await self._repository.remove_replay_user(guild_id, user_id)
        users = self._cached(guild_id).replay_user_ids
        was_there = user_id in users
        users.discard(user_id)
        return was_there

    async def add_clips_channel(self, guild_id: int, channel_id: int) -> None:
        await self._repository.add_clips_channel(guild_id, channel_id)
        self._cached(guild_id).clips_channel_ids.add(channel_id)

    async def remove_clips_channel(self, guild_id: int, channel_id: int) -> bool:
        """Returns whether `channel_id` was registered as a clips channel before this call."""
        await self._repository.remove_clips_channel(guild_id, channel_id)
        channels = self._cached(guild_id).clips_channel_ids
        was_there = channel_id in channels
        channels.discard(channel_id)
        return was_there

    async def clear(self, guild_id: int, setting: ClearableSetting) -> None:
        """Clears one of the settings `/settings clear` offers."""
        id_setting = setting.id_setting
        if id_setting is not None:
            await self.set_id_setting(guild_id, id_setting, None)
            return
        match setting:
            case ClearableSetting.RAID_NOTES_SCHEDULE:
                await self.set_raid_notes_schedule(guild_id, None)
            case ClearableSetting.RAID_NIGHTS:
                await self.set_raid_nights(guild_id, None)
            case ClearableSetting.RAID_VOICE:
                await self.set_raid_voice(guild_id, None)
            case ClearableSetting.BART_TOKEN:
                await self.set_bart_token(guild_id, None)
            case ClearableSetting.WOWUTILS:
                await self.set_wowutils(guild_id, None)
            case ClearableSetting.WCL_SOURCE:
                await self.set_wcl_source(guild_id, None)
            case ClearableSetting.WCL_CLIENT:
                await self.set_wcl_credentials(guild_id, None)
