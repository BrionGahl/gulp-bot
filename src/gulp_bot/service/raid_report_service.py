"""The Warcraft Logs overview posted by itself to the raid notes thread after each raid."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..core.embed import to_discord_embed
from ..mapper.warcraft_logs_embed_mapper import overview_embed
from ..schema.guild_settings_schema import GuildSettings
from ..schema.raid_attendance_schema import MAX_RAID_HOURS
from .guild_settings_service import GuildSettingsService
from .raid_notes_service import RaidNotesService
from .warcraft_logs_service import WarcraftLogsService

if TYPE_CHECKING:
    import discord

log = logging.getLogger(__name__)

# How long after a raid's scheduled end its report is fetched, in seconds. Raids run past their scheduled
# end and a live log trails the game a little, so a report read on the scheduled minute would usually be
# missing the night's last pulls.
UPLOAD_GRACE = 30.0 * 60

# Posted in place of the overview when the uploader has no report from this raid.
NO_REPORT = "No Warcraft Logs report from this raid was found. Run `/wcl report` here once it has been uploaded."


def is_set_up(settings: GuildSettings) -> bool:
    """Whether the guild has registered everything the overview after a raid needs."""
    return settings.wcl_credentials is not None and settings.wcl_source is not None and settings.raid_notes_channel_id is not None


class RaidReportService:
    def __init__(self, guild_settings: GuildSettingsService, warcraft_logs: WarcraftLogsService, raid_notes: RaidNotesService):
        self._guild_settings = guild_settings
        self._warcraft_logs = warcraft_logs
        self._raid_notes = raid_notes

    async def post_after_raid(self, guild_id: int, raid_end: datetime) -> None:
        """Posts the Warcraft Logs overview of the raid that ended at `raid_end` to that week's raid notes
        thread, once the log has had time to upload: the same overview `/wcl report` gives for the newest
        report. Does nothing for a guild that hasn't set Warcraft Logs up."""
        # Checked before the wait too, so that guilds without Warcraft Logs don't each hold a sleeping task
        # after every raid.
        if not is_set_up(self._guild_settings.get(guild_id)):
            return
        await asyncio.sleep(UPLOAD_GRACE)

        try:
            await self._post(guild_id, raid_end)
        except Exception as e:
            log.error("Failed to post the Warcraft Logs overview after the raid for guild %s: %s", guild_id, e)

    async def _post(self, guild_id: int, raid_end: datetime) -> None:
        # Read again after the wait, so that a change made in the meantime counts.
        settings = self._guild_settings.get(guild_id)
        credentials, source, raid_notes_channel_id = settings.wcl_credentials, settings.wcl_source, settings.raid_notes_channel_id
        if credentials is None or source is None or raid_notes_channel_id is None:
            return
        client = self._warcraft_logs.client_for(guild_id, credentials)

        # A report started before this raid can't be this raid's: without the limit, a night whose log
        # hasn't been uploaded yet would get the previous raid's overview.
        code = await self._warcraft_logs.newest_report_code(client, source, started_since=raid_end - timedelta(hours=MAX_RAID_HOURS))
        message: dict[str, str | discord.Embed]
        if code is not None:
            overview = await self._warcraft_logs.overview_for_guild(guild_id, client, code)
            log.info("Posting the Warcraft Logs overview of report %s after the raid for guild %s", code, guild_id)
            message = {"embed": to_discord_embed(overview_embed(overview))}
        else:
            log.info("No Warcraft Logs report from this raid for guild %s", guild_id)
            message = {"content": NO_REPORT}

        week = await self._raid_notes.week_thread(guild_id, raid_notes_channel_id, raid_end.date())
        await self._raid_notes.thread_messageable(guild_id, week.thread_id).send(**message)  # type: ignore[arg-type]
