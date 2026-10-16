"""The Warcraft Logs overview posted by itself after each raid."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from . import raid_notes, warcraftlogs, wcl_ignored_players, wcl_mechanics
from .guild_settings import GuildSettings

if TYPE_CHECKING:
    from .bot import GulpBot

log = logging.getLogger(__name__)

# How long after a raid's scheduled end its report is fetched, in seconds. Raids run past their
# scheduled end and a live log trails the game a little, so a report read on the scheduled minute would
# usually be missing the night's last pulls.
UPLOAD_GRACE = 30.0 * 60

# Posted in place of the overview when the uploader has no report from this raid.
NO_REPORT = "No Warcraft Logs report from this raid was found. Run `/wcl report` here once it has been uploaded."

# How long before a raid's end its report can have been started (see `raid_attendance.MAX_RAID_HOURS`).
MAX_RAID_HOURS = 12


async def post_after_raid(bot: GulpBot, guild_id: int, raid_end: datetime) -> None:
    """Posts the Warcraft Logs overview of the raid that ended at `raid_end` to that week's raid notes
    thread: the same overview `/wcl report` gives for the newest report. Called when a guild's raid end
    schedule fires (see `raid_attendance.schedule_snapshots`), and does nothing for a guild that hasn't
    set Warcraft Logs up."""
    # Checked before the wait too, so that guilds without Warcraft Logs don't each hold a sleeping task
    # after every raid.
    if not is_set_up(bot.guild_settings.get(guild_id)):
        return
    await asyncio.sleep(UPLOAD_GRACE)

    try:
        await _post(bot, guild_id, raid_end)
    except Exception as e:
        log.error("Failed to post the Warcraft Logs overview after the raid for guild %s: %s", guild_id, e)


def is_set_up(settings: GuildSettings) -> bool:
    """Whether the guild has registered everything the overview after a raid needs."""
    return settings.wcl_credentials is not None and settings.wcl_source is not None and settings.raid_notes_channel_id is not None


async def _post(bot: GulpBot, guild_id: int, raid_end: datetime) -> None:
    from .helper import embed_from_dict

    # Read again after the wait, so that a change made in the meantime counts.
    settings = bot.guild_settings.get(guild_id)
    credentials, source, raid_notes_channel_id = settings.wcl_credentials, settings.wcl_source, settings.raid_notes_channel_id
    if credentials is None or source is None or raid_notes_channel_id is None:
        return
    client = bot.warcraft_logs.for_guild(guild_id, credentials)

    # A report started before this raid can't be this raid's: without the limit, a night whose log
    # hasn't been uploaded yet would get the previous raid's overview.
    earliest_start = raid_end - timedelta(hours=MAX_RAID_HOURS)
    code = await client.latest_report_code_since(source, earliest_start)
    if code is not None:
        tracked = await wcl_mechanics.list_tracked(bot.db, guild_id)
        ignored = await wcl_ignored_players.list_ignored(bot.db, guild_id)
        overview = await client.overview(code, tracked, ignored)
        log.info("Posting the Warcraft Logs overview of report %s after the raid for guild %s", code, guild_id)
        message = {"embed": embed_from_dict(warcraftlogs.overview_embed(overview))}
    else:
        log.info("No Warcraft Logs report from this raid for guild %s", guild_id)
        message = {"content": NO_REPORT}

    post = await raid_notes.week_post(bot, guild_id, raid_notes_channel_id, raid_end.date())
    await bot.get_partial_messageable(post.thread_id, guild_id=guild_id).send(**message)  # type: ignore[arg-type]
