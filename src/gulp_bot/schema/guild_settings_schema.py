"""What a server's administrators have registered for it through the `/settings` commands.

Everything is optional: a feature whose values aren't registered is simply off for that server.
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field

from ..core.cron import Schedule


class ThinkSetting(enum.Enum):
    """Whether a local model thinks before it answers, as the guild's developers set it with `/ai think`. The
    value is how it is stored in the database."""

    ON = "on"
    OFF = "off"
    # Nothing is sent: the model decides.
    DEFAULT = "default"

    @property
    def think(self) -> bool | None:
        """What is sent to Ollama as `think`: `None` sends nothing."""
        return {ThinkSetting.ON: True, ThinkSetting.OFF: False, ThinkSetting.DEFAULT: None}[self]


@dataclass
class AiSettings:
    """How the AI assistant has been switched, in a guild, by its developers with the `/ai` commands. The
    default is the assistant on, in no special mode, with the model and thinking the bot was configured
    with."""

    # Off: the assistant answers nobody, whatever their role.
    disabled: bool = False
    # Maintenance mode: the assistant answers only Officers and Developers.
    maintenance: bool = False
    # The model to use instead of the configured one.
    model: str | None = None
    # Whether the model thinks, instead of the configured `LOCAL_MODEL_THINK`.
    think: ThinkSetting | None = None


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
class WowUtilsCredentials:
    """A WowUtils API token and the group droptimizer reports are submitted to."""

    token: str = field(repr=False)
    group_id: str


@dataclass(frozen=True)
class RaidVoice:
    """A guild's raid voice channel and when to record who is in it."""

    channel_id: int
    # Fires at the start of each raid.
    start: Schedule
    # Fires at the end of each raid.
    end: Schedule


@dataclass
class GuildSettings:
    mod_role_id: int | None = None
    # The role that makes a member a Developer: above Officer for the AI assistant, and the only one who
    # may run the `/ai` commands. Only administrators can register it.
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
    # When the raid notes post is created; `None` means `raid_notes_service.DEFAULT_RAID_NOTES_CRON`.
    raid_notes_cron: Schedule | None = None
    # Raid attendance tracking; `None` means it's off for this guild.
    raid_voice: RaidVoice | None = None
    # Where this guild's Warcraft Logs reports are found.
    wcl_source: WclSource | None = None
    # This guild's Warcraft Logs API client; `None` means `/wcl` is off for it.
    wcl_credentials: WclCredentials | None = None
    # What the guild's developers have changed about the AI assistant at run time (`/ai`).
    ai: AiSettings = field(default_factory=AiSettings)

    @property
    def wowutils(self) -> WowUtilsCredentials | None:
        if self.wowutils_token is None or self.wowutils_group_id is None:
            return None
        return WowUtilsCredentials(self.wowutils_token, self.wowutils_group_id)

    def copy(self) -> GuildSettings:
        return dataclasses.replace(
            self,
            replay_user_ids=set(self.replay_user_ids),
            clips_channel_ids=set(self.clips_channel_ids),
            ai=dataclasses.replace(self.ai),
        )


class GuildIdSetting(enum.Enum):
    """A setting that is a single role or channel ID: one of the BIGINT columns of the `guild_settings`
    table. The value is the name shown to users."""

    MOD_ROLE = "Moderator role"
    DEV_ROLE = "Developer role"
    RAIDER_ROLE = "Raider role"
    TRIAL_ROLE = "Trial role"
    PERSONAL_OFFICER_CATEGORY = "Personal officer category"
    RAID_NOTES_CHANNEL = "Raid notes channel"

    @property
    def column(self) -> str:
        """The `guild_settings` column, which is also the `GuildSettings` attribute."""
        return {
            GuildIdSetting.MOD_ROLE: "mod_role_id",
            GuildIdSetting.DEV_ROLE: "dev_role_id",
            GuildIdSetting.RAIDER_ROLE: "raider_role_id",
            GuildIdSetting.TRIAL_ROLE: "trial_role_id",
            GuildIdSetting.PERSONAL_OFFICER_CATEGORY: "personal_officer_category_id",
            GuildIdSetting.RAID_NOTES_CHANNEL: "raid_notes_channel_id",
        }[self]


class ClearableSetting(enum.Enum):
    """Everything `/settings clear` can clear. The value is what the command takes; `label` is what it
    shows."""

    MOD_ROLE = "mod_role"
    DEV_ROLE = "dev_role"
    RAIDER_ROLE = "raider_role"
    TRIAL_ROLE = "trial_role"
    PERSONAL_OFFICER_CATEGORY = "personal_officer_category"
    RAID_NOTES_CHANNEL = "raid_notes_channel"
    RAID_NOTES_CRON = "raid_notes_cron"
    RAID_VOICE = "raid_voice"
    BART_TOKEN = "bart_token"
    WOWUTILS = "wowutils"
    WCL_SOURCE = "wcl_source"
    WCL_CLIENT = "wcl_client"

    @property
    def label(self) -> str:
        id_setting = self.id_setting
        if id_setting is not None:
            return id_setting.value
        return {
            ClearableSetting.RAID_NOTES_CRON: "Raid notes schedule",
            ClearableSetting.RAID_VOICE: "Raid attendance",
            ClearableSetting.BART_TOKEN: "Bart token",
            ClearableSetting.WOWUTILS: "WowUtils credentials",
            ClearableSetting.WCL_SOURCE: "Warcraft Logs source",
            ClearableSetting.WCL_CLIENT: "Warcraft Logs API client",
        }[self]

    @property
    def id_setting(self) -> GuildIdSetting | None:
        """The ID setting this is, if it is one."""
        return {
            ClearableSetting.MOD_ROLE: GuildIdSetting.MOD_ROLE,
            ClearableSetting.DEV_ROLE: GuildIdSetting.DEV_ROLE,
            ClearableSetting.RAIDER_ROLE: GuildIdSetting.RAIDER_ROLE,
            ClearableSetting.TRIAL_ROLE: GuildIdSetting.TRIAL_ROLE,
            ClearableSetting.PERSONAL_OFFICER_CATEGORY: GuildIdSetting.PERSONAL_OFFICER_CATEGORY,
            ClearableSetting.RAID_NOTES_CHANNEL: GuildIdSetting.RAID_NOTES_CHANNEL,
        }.get(self)

    @property
    def administrators_only(self) -> bool:
        """Clearing the moderator or developer role is changing it, which only administrators may do."""
        return self in (ClearableSetting.MOD_ROLE, ClearableSetting.DEV_ROLE)
