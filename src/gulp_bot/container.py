"""Builds every service once, with what each depends on, and hands them out as one `Services`.

This is the only place that knows how the layers are wired together: a resource asks for
`ctx.services.announcements` and gets a ready `AnnouncementService`, without caring which repository or
client is behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .client.warcraft_logs_client import WarcraftLogsClientPool
from .client.wowutils_client import WowUtilsClient
from .core.background_tasks import BackgroundTasks
from .core.config import Config
from .core.http_client import HttpClient
from .job.announcement_job import AnnouncementJob
from .job.raid_attendance_job import RaidAttendanceJob
from .job.raid_notes_job import RaidNotesJob
from .repository.announcement_repository import AnnouncementRepository
from .repository.officer_channel_repository import OfficerChannelRepository
from .repository.raid_attendance_repository import RaidAttendanceRepository
from .repository.raid_notes_post_repository import RaidNotesPostRepository
from .repository.replay_message_memory_repository import ReplayMessageMemoryRepository
from .repository.wcl_consumable_ability_repository import ConsumableAbilityRepository
from .repository.wcl_ignored_player_repository import IgnoredPlayerRepository
from .repository.wcl_tracked_ability_repository import TrackedAbilityRepository
from .service.ai_assistant_service import AiAssistantService
from .service.announcement_service import AnnouncementService
from .service.bot_get_them_service import BotGetThemService
from .service.clips_channel_service import ClipsChannelService
from .service.droptimizer_service import DroptimizerService
from .service.guild_settings_service import GuildSettingsService
from .service.guild_validation_service import GuildValidationService
from .service.message_replay_service import MessageReplayService
from .service.officer_channel_service import OfficerChannelService
from .service.raid_attendance_service import RaidAttendanceService
from .service.raid_notes_service import RaidNotesService
from .service.raid_report_service import RaidReportService
from .service.warcraft_logs_service import WarcraftLogsService
from .service.wcl_consumable_service import ConsumableService
from .service.wcl_ignored_player_service import IgnoredPlayerService
from .service.wcl_tracked_ability_service import TrackedAbilityService

if TYPE_CHECKING:
    import asyncpg
    import discord


@dataclass(frozen=True)
class Services:
    config: Config
    tasks: BackgroundTasks
    guild_settings: GuildSettingsService
    guild_validation: GuildValidationService
    announcements: AnnouncementService
    raid_notes: RaidNotesService
    raid_attendance: RaidAttendanceService
    raid_report: RaidReportService
    officer_channels: OfficerChannelService
    clips_channels: ClipsChannelService
    message_replay: MessageReplayService
    bot_get_them: BotGetThemService
    tracked_abilities: TrackedAbilityService
    ignored_players: IgnoredPlayerService
    consumables: ConsumableService
    warcraft_logs: WarcraftLogsService
    droptimizer: DroptimizerService
    # The AI assistant behind `/ask`, `/tldr` and "Bot please"; `None` without `ANTHROPIC_API_KEY`, unless it
    # runs on a local model (`AI_BACKEND=local`), which needs no key.
    ai: AiAssistantService | None


def build_services(
    config: Config,
    pool: asyncpg.Pool,
    http: HttpClient,
    guild_settings: GuildSettingsService,
    discord_client: discord.Client,
    tasks: BackgroundTasks,
) -> Services:
    raid_notes = RaidNotesService(RaidNotesPostRepository(pool), discord_client)
    tracked_abilities = TrackedAbilityService(TrackedAbilityRepository(pool))
    ignored_players = IgnoredPlayerService(IgnoredPlayerRepository(pool))
    warcraft_logs = WarcraftLogsService(WarcraftLogsClientPool(http), tracked_abilities, ignored_players)
    message_replay = MessageReplayService(guild_settings, ReplayMessageMemoryRepository(), discord_client)

    return Services(
        config=config,
        tasks=tasks,
        guild_settings=guild_settings,
        guild_validation=GuildValidationService(discord_client),
        announcements=AnnouncementService(AnnouncementRepository(pool), guild_settings, discord_client),
        raid_notes=raid_notes,
        raid_attendance=RaidAttendanceService(RaidAttendanceRepository(pool), raid_notes, discord_client),
        raid_report=RaidReportService(guild_settings, warcraft_logs, raid_notes),
        officer_channels=OfficerChannelService(OfficerChannelRepository(pool), guild_settings),
        clips_channels=ClipsChannelService(guild_settings, tasks),
        message_replay=message_replay,
        bot_get_them=BotGetThemService(guild_settings, message_replay),
        tracked_abilities=tracked_abilities,
        ignored_players=ignored_players,
        consumables=ConsumableService(ConsumableAbilityRepository(pool)),
        warcraft_logs=warcraft_logs,
        droptimizer=DroptimizerService(WowUtilsClient(http)),
        ai=AiAssistantService.from_config(http, config),
    )


def scheduled_jobs(services: Services) -> list[AnnouncementJob | RaidNotesJob | RaidAttendanceJob]:
    """The jobs that run on a schedule for as long as the bot does."""
    return [
        RaidNotesJob(services.guild_settings, services.raid_notes),
        RaidAttendanceJob(services.guild_settings, services.raid_attendance, services.raid_report, services.tasks),
        AnnouncementJob(services.announcements),
    ]
