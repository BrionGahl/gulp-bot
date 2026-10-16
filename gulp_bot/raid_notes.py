"""The weekly raid notes forum post."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from . import schedules
from .cron import Schedule
from .db import from_db, to_db

if TYPE_CHECKING:
    import asyncpg

    from .bot import GulpBot

log = logging.getLogger(__name__)

# Cron expression used for a guild that hasn't registered its own with `/settings raid_notes_cron`.
# Quartz-style 7-field format (`sec min hour day-of-month month day-of-week year`): Fridays at 9:00 AM.
DEFAULT_RAID_NOTES_CRON = "0 0 9 * * Fri *"

# The longest the scheduler sleeps before re-reading every guild's settings, in seconds, which bounds
# how long a schedule or channel changed through `/settings` takes to be picked up.
MAX_SLEEP = 60.0

# Serializes `week_post` so that the scheduler here and the attendance scheduler, both wanting the same
# week's thread at the same moment, can't each create one. Enough on its own because a single bot
# process owns the database.
_WEEK_POST_LOCK = asyncio.Lock()


async def schedule_weekly_posts(bot: GulpBot) -> None:
    """Runs forever, creating a new post in every guild's registered raid notes forum channel on that
    guild's schedule (`/settings raid_notes_cron`, or `DEFAULT_RAID_NOTES_CRON` when it has none).

    Schedules differ per guild and can change at any time, so rather than sleeping until one fixed fire
    time this wakes at the earliest upcoming fire time across all guilds, or after `MAX_SLEEP` if that
    comes first, and posts for every guild with a fire time since the previous wake-up. A week that
    already has a thread (because attendance was recorded earlier in it, see `raid_attendance`) doesn't
    get a second one. Fire times are computed in `America/New_York`, see `schedules.now`."""
    default_schedule = Schedule.parse(DEFAULT_RAID_NOTES_CRON)
    last_checked = schedules.now()

    while True:
        now = schedules.now()
        next_wake = now + timedelta(seconds=MAX_SLEEP)

        for guild_id, settings in bot.guild_settings.all():
            channel_id = settings.raid_notes_channel_id
            if channel_id is None:
                continue
            schedule = settings.raid_notes_cron or default_schedule

            fire_time = schedule.after(last_checked)
            if fire_time is not None and fire_time.timestamp() <= now.timestamp():
                try:
                    await week_post(bot, guild_id, channel_id, fire_time.date())
                except Exception as e:
                    log.error("Failed to create raid notes post for guild %s: %s", guild_id, e)

            upcoming = schedule.after(now)
            if upcoming is not None:
                next_wake = min(next_wake, upcoming, key=datetime.timestamp)

        last_checked = now
        # Measured from the current time, not `now`: posting above may have taken a while.
        await asyncio.sleep(max(0.0, next_wake.timestamp() - schedules.now().timestamp()))


@dataclass(frozen=True)
class WeekPost:
    """The raid notes forum thread for one week."""

    thread_id: int
    title: str
    # Whether this call created the thread, as opposed to finding the one already recorded.
    created: bool


def week_start(day: date) -> date:
    """The Tuesday on or before `day`, which is what identifies a week's thread. Raid weeks run Tuesday
    (the game's weekly reset) to Monday, so a Tuesday always starts a new week and a Monday belongs to the
    week that began six days earlier."""
    days_since_tuesday = (day.weekday() + 6) % 7
    return day - timedelta(days=days_since_tuesday)


def week_title(day: date) -> str:
    tuesday = week_start(day)
    monday = tuesday + timedelta(days=6)
    return f"Week of {tuesday:%b} {tuesday.day} - {monday:%b} {monday.day}"


async def week_post(bot: GulpBot, guild_id: int, channel_id: int, day: date | datetime) -> WeekPost:
    """Returns the raid notes forum thread for the week containing `day`, creating it (with a placeholder
    starter message) in `channel_id` and recording it if the bot hasn't made one for that week yet."""
    import discord

    if isinstance(day, datetime):
        day = day.date()
    tuesday = week_start(day)
    title = week_title(day)
    db: asyncpg.Pool = bot.db

    async with _WEEK_POST_LOCK:
        existing = await db.fetchval("SELECT thread_id FROM raid_notes_posts WHERE guild_id = $1 AND week_start = $2", to_db(guild_id), tuesday)
        if existing is not None:
            return WeekPost(from_db(existing), title, created=False)

        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.ForumChannel):
            raise RuntimeError(f"channel {channel_id} is not a forum channel")
        thread, _ = await channel.create_thread(name=title, content="Raid notes for this week go here.")
        log.info("Created weekly raid notes post for guild %s: %s", guild_id, title)

        await db.execute(
            "INSERT INTO raid_notes_posts (guild_id, week_start, thread_id) VALUES ($1, $2, $3)",
            to_db(guild_id),
            tuesday,
            to_db(thread.id),
        )
        return WeekPost(thread.id, title, created=True)
