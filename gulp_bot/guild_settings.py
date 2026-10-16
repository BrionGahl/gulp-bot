"""What a server's administrators have registered for it through the `/settings` commands.

Everything is optional: a feature whose values aren't registered is simply off for that server.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import schedules
from .cron import Schedule
from .db import from_db, from_db_opt, to_db, to_db_opt

if TYPE_CHECKING:
    import asyncpg

log = logging.getLogger(__name__)


@dataclass
class AiSettings:
    """How the AI assistant has been switched, in a guild, by its developers with the `/ai` commands.
    The default is the assistant on, in no special mode, with the model and effort the bot was
    configured with."""

    # Off: the assistant answers nobody, whatever their role.
    disabled: bool = False
    # Maintenance mode: the assistant answers only Officers and Developers.
    maintenance: bool = False
    # The model to use instead of the configured one.
    model: str | None = None
    # The effort to use instead of the configured one; `off` means none is sent.
    effort: str | None = None


@dataclass(frozen=True)
class WclCredentials:
    """A Warcraft Logs API client's ID and secret, as issued at warcraftlogs.com (Manage Your Clients)."""

    client_id: str
    # Kept out of `repr`, so that the secret can never end up in a log line.
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class WclSource:
    """Where a guild's Warcraft Logs reports come from: the Warcraft Logs guild they are uploaded to, and
    the user ID of the one account whose uploads count."""

    guild_id: int
    uploader_id: int


@dataclass(frozen=True)
class RaidVoice:
    """A guild's raid voice channel and when to record who is in it. See `raid_attendance`."""

    channel_id: int
    # Fires at the start of each raid.
    start: Schedule
    # Fires at the end of each raid.
    end: Schedule


@dataclass
class GuildSettings:
    mod_role_id: int | None = None
    # The role that makes a member a Developer: above Officer for the AI assistant, and the only one
    # who may run the `/ai` commands. Only administrators can register it.
    dev_role_id: int | None = None
    raider_role_id: int | None = None
    trial_role_id: int | None = None
    personal_officer_category_id: int | None = None
    raid_notes_channel_id: int | None = None
    # Members whose self-deleted messages the bot reposts.
    replay_user_ids: set[int] = field(default_factory=set)
    clips_channel_ids: set[int] = field(default_factory=set)
    # Bart Timeline Reminders access token handed to this server's raiders.
    bart_token: str | None = field(default=None, repr=False)
    # WowUtils API token and the group droptimizer reports are submitted to. Registered and cleared
    # together, so in practice both are set or neither is.
    wowutils_token: str | None = field(default=None, repr=False)
    wowutils_group_id: str | None = None
    # When the raid notes post is created; `None` means `raid_notes.DEFAULT_RAID_NOTES_CRON`.
    raid_notes_cron: Schedule | None = None
    # Raid attendance tracking; `None` means it's off for this guild.
    raid_voice: RaidVoice | None = None
    # Where this guild's Warcraft Logs reports are found.
    wcl_source: WclSource | None = None
    # This guild's Warcraft Logs API client; `None` means `/wcl` is off for it.
    wcl_credentials: WclCredentials | None = None
    # What the guild's developers have changed about the AI assistant at run time (`/ai`).
    ai: AiSettings = field(default_factory=AiSettings)

    def copy(self) -> GuildSettings:
        return dataclasses.replace(
            self,
            replay_user_ids=set(self.replay_user_ids),
            clips_channel_ids=set(self.clips_channel_ids),
            ai=dataclasses.replace(self.ai),
        )


class Setting(enum.Enum):
    """A single-valued ID setting, i.e. one of the BIGINT columns of the `guild_settings` table. The
    value is the name shown to users."""

    MOD_ROLE = "Moderator role"
    DEV_ROLE = "Developer role"
    RAIDER_ROLE = "Raider role"
    TRIAL_ROLE = "Trial role"
    PERSONAL_OFFICER_CATEGORY = "Personal officer category"
    RAID_NOTES_CHANNEL = "Raid notes channel"

    @property
    def column(self) -> str:
        return {
            Setting.MOD_ROLE: "mod_role_id",
            Setting.DEV_ROLE: "dev_role_id",
            Setting.RAIDER_ROLE: "raider_role_id",
            Setting.TRIAL_ROLE: "trial_role_id",
            Setting.PERSONAL_OFFICER_CATEGORY: "personal_officer_category_id",
            Setting.RAID_NOTES_CHANNEL: "raid_notes_channel_id",
        }[self]


class GuildSettingsStore:
    """Postgres-backed store of every guild's settings, fronted by an in-memory copy.

    Settings are read on every message the bot sees (clips channels, delete-and-replay), so reads are
    served from memory. The copy is loaded once at startup and updated on every write; that is only
    correct because a single bot process owns the database.
    """

    def __init__(self, pool: asyncpg.Pool, cache: dict[int, GuildSettings]):
        self._pool = pool
        self._cache = cache

    @classmethod
    async def load(cls, pool: asyncpg.Pool) -> GuildSettingsStore:
        cache: dict[int, GuildSettings] = {}

        rows = await pool.fetch(
            "SELECT guild_id, mod_role_id, dev_role_id, raider_role_id, trial_role_id, personal_officer_category_id, "
            "raid_notes_channel_id, ai_disabled, ai_maintenance, ai_model, ai_effort, "
            "bart_token, wowutils_token, wowutils_group_id, raid_notes_cron, "
            "raid_voice_channel_id, raid_voice_start_cron, raid_voice_end_cron, "
            "wcl_guild_id, wcl_uploader_id, wcl_client_id, wcl_client_secret FROM guild_settings"
        )
        for row in rows:
            guild_id = from_db(row["guild_id"])

            # Expressions are validated before they're stored, so this only fails if the row was edited
            # by hand; fall back to the default schedule rather than refusing to start.
            raid_notes_cron = None
            if row["raid_notes_cron"] is not None:
                raid_notes_cron = schedules.try_parse(row["raid_notes_cron"])
                if raid_notes_cron is None:
                    log.warning("Ignoring unreadable raid notes cron `%s` for guild %s", row["raid_notes_cron"], guild_id)

            # Same reasoning: an unreadable expression turns the feature off for that guild rather than
            # stopping the bot.
            raid_voice = None
            channel, start, end = row["raid_voice_channel_id"], row["raid_voice_start_cron"], row["raid_voice_end_cron"]
            if channel is not None and start is not None and end is not None:
                start_schedule, end_schedule = schedules.try_parse(start), schedules.try_parse(end)
                if start_schedule is not None and end_schedule is not None:
                    raid_voice = RaidVoice(from_db(channel), start_schedule, end_schedule)
                else:
                    log.warning("Ignoring unreadable raid attendance schedule (`%s` / `%s`) for guild %s", start, end, guild_id)

            def wcl_id(value: int | None) -> int | None:
                return value if value is not None and 0 <= value <= 0xFFFFFFFF else None

            wcl_guild, wcl_uploader = wcl_id(row["wcl_guild_id"]), wcl_id(row["wcl_uploader_id"])
            wcl_source = WclSource(wcl_guild, wcl_uploader) if wcl_guild is not None and wcl_uploader is not None else None
            client_id, client_secret = row["wcl_client_id"], row["wcl_client_secret"]
            wcl_credentials = WclCredentials(client_id, client_secret) if client_id is not None and client_secret is not None else None

            cache[guild_id] = GuildSettings(
                mod_role_id=from_db_opt(row["mod_role_id"]),
                dev_role_id=from_db_opt(row["dev_role_id"]),
                raider_role_id=from_db_opt(row["raider_role_id"]),
                trial_role_id=from_db_opt(row["trial_role_id"]),
                personal_officer_category_id=from_db_opt(row["personal_officer_category_id"]),
                raid_notes_channel_id=from_db_opt(row["raid_notes_channel_id"]),
                bart_token=row["bart_token"],
                wowutils_token=row["wowutils_token"],
                wowutils_group_id=row["wowutils_group_id"],
                raid_notes_cron=raid_notes_cron,
                raid_voice=raid_voice,
                wcl_source=wcl_source,
                wcl_credentials=wcl_credentials,
                ai=AiSettings(
                    disabled=row["ai_disabled"],
                    maintenance=row["ai_maintenance"],
                    model=row["ai_model"],
                    effort=row["ai_effort"],
                ),
            )

        for row in await pool.fetch("SELECT guild_id, channel_id FROM guild_clips_channels"):
            cache.setdefault(from_db(row["guild_id"]), GuildSettings()).clips_channel_ids.add(from_db(row["channel_id"]))

        for row in await pool.fetch("SELECT guild_id, user_id FROM guild_replay_users"):
            cache.setdefault(from_db(row["guild_id"]), GuildSettings()).replay_user_ids.add(from_db(row["user_id"]))

        return cls(pool, cache)

    def get(self, guild_id: int) -> GuildSettings:
        """The settings registered for `guild_id`, or all-unset defaults if it has none. Lookups are
        always keyed by the guild an event came from, so an ID registered in one server is never
        applied in another. A copy: changing it changes nothing."""
        settings = self._cache.get(guild_id)
        return GuildSettings() if settings is None else settings.copy()

    def all(self) -> list[tuple[int, GuildSettings]]:
        """Every guild that has settings, for the scheduled jobs that aren't driven by a guild event."""
        return [(guild_id, settings.copy()) for guild_id, settings in self._cache.items()]

    def _entry(self, guild_id: int) -> GuildSettings:
        return self._cache.setdefault(guild_id, GuildSettings())

    async def set(self, guild_id: int, setting: Setting, value: int | None) -> None:
        """Registers `value` for `setting` in `guild_id`, or clears the setting when `value` is `None`."""
        column = setting.column
        await self._pool.execute(
            f"INSERT INTO guild_settings (guild_id, {column}) VALUES ($1, $2) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {column} = EXCLUDED.{column}, updated_at = now()",
            to_db(guild_id),
            to_db_opt(value),
        )
        setattr(self._entry(guild_id), column, value)

    async def set_ai(self, guild_id: int, ai: AiSettings) -> None:
        """Saves how the AI assistant has been switched in this guild (`/ai`). Takes the whole of it, so
        a caller reads the current value, changes what it means to and writes it back."""
        await self._pool.execute(
            "INSERT INTO guild_settings (guild_id, ai_disabled, ai_maintenance, ai_model, ai_effort) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (guild_id) DO UPDATE SET ai_disabled = EXCLUDED.ai_disabled, "
            "ai_maintenance = EXCLUDED.ai_maintenance, ai_model = EXCLUDED.ai_model, "
            "ai_effort = EXCLUDED.ai_effort, updated_at = now()",
            to_db(guild_id),
            ai.disabled,
            ai.maintenance,
            ai.model,
            ai.effort,
        )
        self._entry(guild_id).ai = dataclasses.replace(ai)

    async def set_wcl_source(self, guild_id: int, source: WclSource | None) -> None:
        """Registers where this guild's Warcraft Logs reports are found, or clears it when `None`."""
        await self._pool.execute(
            "INSERT INTO guild_settings (guild_id, wcl_guild_id, wcl_uploader_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id) DO UPDATE SET wcl_guild_id = EXCLUDED.wcl_guild_id, "
            "wcl_uploader_id = EXCLUDED.wcl_uploader_id, updated_at = now()",
            to_db(guild_id),
            None if source is None else source.guild_id,
            None if source is None else source.uploader_id,
        )
        self._entry(guild_id).wcl_source = source

    async def set_wcl_credentials(self, guild_id: int, credentials: WclCredentials | None) -> None:
        """Registers this guild's Warcraft Logs API client, or clears it when `None`."""
        await self._pool.execute(
            "INSERT INTO guild_settings (guild_id, wcl_client_id, wcl_client_secret) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id) DO UPDATE SET wcl_client_id = EXCLUDED.wcl_client_id, "
            "wcl_client_secret = EXCLUDED.wcl_client_secret, updated_at = now()",
            to_db(guild_id),
            None if credentials is None else credentials.client_id,
            None if credentials is None else credentials.client_secret,
        )
        self._entry(guild_id).wcl_credentials = credentials

    async def set_bart_token(self, guild_id: int, token: str | None) -> None:
        """Registers this guild's Bart Timeline Reminders token, or clears it when `token` is `None`."""
        await self._pool.execute(
            "INSERT INTO guild_settings (guild_id, bart_token) VALUES ($1, $2) "
            "ON CONFLICT (guild_id) DO UPDATE SET bart_token = EXCLUDED.bart_token, updated_at = now()",
            to_db(guild_id),
            token,
        )
        self._entry(guild_id).bart_token = token

    async def set_wowutils(self, guild_id: int, credentials: tuple[str, str] | None) -> None:
        """Registers this guild's WowUtils `(token, group_id)`, or clears both when `None`."""
        token, group_id = credentials if credentials is not None else (None, None)
        await self._pool.execute(
            "INSERT INTO guild_settings (guild_id, wowutils_token, wowutils_group_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id) DO UPDATE SET wowutils_token = EXCLUDED.wowutils_token, "
            "wowutils_group_id = EXCLUDED.wowutils_group_id, updated_at = now()",
            to_db(guild_id),
            token,
            group_id,
        )
        settings = self._entry(guild_id)
        settings.wowutils_token, settings.wowutils_group_id = token, group_id

    async def set_raid_notes_cron(self, guild_id: int, schedule: Schedule | None) -> None:
        """Registers this guild's raid notes schedule, or reverts it to the default when `None`."""
        await self._pool.execute(
            "INSERT INTO guild_settings (guild_id, raid_notes_cron) VALUES ($1, $2) "
            "ON CONFLICT (guild_id) DO UPDATE SET raid_notes_cron = EXCLUDED.raid_notes_cron, updated_at = now()",
            to_db(guild_id),
            None if schedule is None else schedule.source,
        )
        self._entry(guild_id).raid_notes_cron = schedule

    async def set_raid_voice(self, guild_id: int, raid_voice: RaidVoice | None) -> None:
        """Registers this guild's raid voice channel and schedules, or turns attendance tracking off
        when `None`."""
        await self._pool.execute(
            "INSERT INTO guild_settings (guild_id, raid_voice_channel_id, raid_voice_start_cron, raid_voice_end_cron) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id) DO UPDATE SET raid_voice_channel_id = EXCLUDED.raid_voice_channel_id, "
            "raid_voice_start_cron = EXCLUDED.raid_voice_start_cron, raid_voice_end_cron = EXCLUDED.raid_voice_end_cron, "
            "updated_at = now()",
            to_db(guild_id),
            None if raid_voice is None else to_db(raid_voice.channel_id),
            None if raid_voice is None else raid_voice.start.source,
            None if raid_voice is None else raid_voice.end.source,
        )
        self._entry(guild_id).raid_voice = raid_voice

    async def add_replay_user(self, guild_id: int, user_id: int) -> None:
        await self._pool.execute(
            "INSERT INTO guild_replay_users (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            to_db(guild_id),
            to_db(user_id),
        )
        self._entry(guild_id).replay_user_ids.add(user_id)

    async def remove_replay_user(self, guild_id: int, user_id: int) -> bool:
        """Returns whether `user_id` was registered as a replay user before this call."""
        await self._pool.execute("DELETE FROM guild_replay_users WHERE guild_id = $1 AND user_id = $2", to_db(guild_id), to_db(user_id))
        users = self._entry(guild_id).replay_user_ids
        was_there = user_id in users
        users.discard(user_id)
        return was_there

    async def add_clips_channel(self, guild_id: int, channel_id: int) -> None:
        await self._pool.execute(
            "INSERT INTO guild_clips_channels (guild_id, channel_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            to_db(guild_id),
            to_db(channel_id),
        )
        self._entry(guild_id).clips_channel_ids.add(channel_id)

    async def remove_clips_channel(self, guild_id: int, channel_id: int) -> bool:
        """Returns whether `channel_id` was registered as a clips channel before this call."""
        await self._pool.execute("DELETE FROM guild_clips_channels WHERE guild_id = $1 AND channel_id = $2", to_db(guild_id), to_db(channel_id))
        channels = self._entry(guild_id).clips_channel_ids
        was_there = channel_id in channels
        channels.discard(channel_id)
        return was_there
