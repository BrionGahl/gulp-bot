"""Announcements scheduled by a guild's officers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import schedules
from .cron import Schedule
from .db import from_db, rows_affected, to_db
from .guild_settings import GuildSettings
from .mentions import replace_role_mentions

if TYPE_CHECKING:
    import asyncpg

    from .bot import GulpBot

log = logging.getLogger(__name__)

# The longest the scheduler sleeps before re-reading the announcements table, in seconds, which bounds
# how long an announcement added or deleted by an officer takes to be picked up.
MAX_SLEEP = 60.0


@dataclass(frozen=True)
class Announcement:
    """An announcement an officer scheduled: `message` is posted to `channel_id` whenever `schedule` fires."""

    id: int
    guild_id: int
    channel_id: int
    schedule: Schedule
    message: str
    created_by: int


COLUMNS = "id, guild_id, channel_id, cron, message, created_by"


def _from_row(row: Any) -> Announcement | None:
    """Reads one row. Returns `None` for a row whose cron expression can't be parsed: expressions are
    validated before they're stored, so that only happens if the row was edited by hand, and skipping it
    beats taking every other announcement down with it."""
    schedule = schedules.try_parse(row["cron"])
    if schedule is None:
        log.warning("Skipping announcement %s: unreadable cron expression `%s`", row["id"], row["cron"])
        return None
    return Announcement(row["id"], from_db(row["guild_id"]), from_db(row["channel_id"]), schedule, row["message"], from_db(row["created_by"]))


def _from_rows(rows: list[Any]) -> list[Announcement]:
    return [announcement for announcement in map(_from_row, rows) if announcement is not None]


async def create(db: asyncpg.Pool, guild_id: int, channel_id: int, schedule: Schedule, message: str, created_by: int) -> int:
    """Stores a new announcement and returns its ID."""
    return await db.fetchval(
        "INSERT INTO announcements (guild_id, channel_id, cron, message, created_by) VALUES ($1, $2, $3, $4, $5) RETURNING id",
        to_db(guild_id),
        to_db(channel_id),
        schedule.source,
        message,
        to_db(created_by),
    )


async def list_announcements(db: asyncpg.Pool, guild_id: int) -> list[Announcement]:
    """`guild_id`'s announcements, oldest first."""
    return _from_rows(await db.fetch(f"SELECT {COLUMNS} FROM announcements WHERE guild_id = $1 ORDER BY id", to_db(guild_id)))


async def count(db: asyncpg.Pool, guild_id: int) -> int:
    return await db.fetchval("SELECT count(*) AS total FROM announcements WHERE guild_id = $1", to_db(guild_id))


async def get(db: asyncpg.Pool, guild_id: int, announcement_id: int) -> Announcement | None:
    """One announcement, but only if it belongs to `guild_id`: IDs are global, and an officer of one
    server must not be able to read, post or delete another server's announcement by guessing one."""
    row = await db.fetchrow(f"SELECT {COLUMNS} FROM announcements WHERE id = $1 AND guild_id = $2", announcement_id, to_db(guild_id))
    return None if row is None else _from_row(row)


async def delete(db: asyncpg.Pool, guild_id: int, announcement_id: int) -> bool:
    """Deletes an announcement, scoped to `guild_id` like `get`. Returns whether it existed there."""
    status = await db.execute("DELETE FROM announcements WHERE id = $1 AND guild_id = $2", announcement_id, to_db(guild_id))
    return rows_affected(status) == 1


async def all_announcements(db: asyncpg.Pool) -> list[Announcement]:
    return _from_rows(await db.fetch(f"SELECT {COLUMNS} FROM announcements ORDER BY id"))


async def schedule_announcements(bot: GulpBot) -> None:
    """Runs forever, posting each stored announcement to its channel whenever its schedule fires.

    Announcements can be added and deleted at any time, so rather than sleeping until one fixed fire time
    this re-reads the table on every wake-up, wakes at the earliest upcoming fire time across all
    announcements (or after `MAX_SLEEP` if that comes first), and posts every announcement with a fire
    time since the previous wake-up."""
    last_checked = schedules.now()

    while True:
        now = schedules.now()
        next_wake = now + timedelta(seconds=MAX_SLEEP)

        try:
            announcements = await all_announcements(bot.db)
        except Exception as e:
            # `last_checked` is left alone, so anything that came due while the database was unreachable
            # is posted once it's back.
            log.error("Failed to load announcements: %s", e)
        else:
            for announcement in announcements:
                fire_time = announcement.schedule.after(last_checked)
                if fire_time is not None and fire_time.timestamp() <= now.timestamp():
                    try:
                        await post(bot, announcement, bot.guild_settings.get(announcement.guild_id))
                    except Exception as e:
                        log.error("Failed to post announcement %s for guild %s: %s", announcement.id, announcement.guild_id, e)

                upcoming = announcement.schedule.after(now)
                if upcoming is not None:
                    next_wake = min(next_wake, upcoming, key=datetime.timestamp)
            last_checked = now

        # Measured from the current time, not `now`: posting above may have taken a while.
        await asyncio.sleep(max(0.0, next_wake.timestamp() - schedules.now().timestamp()))


async def post(bot: GulpBot, announcement: Announcement, settings: GuildSettings) -> None:
    """Posts `announcement` to its channel now, expanding `@Raider`/`@Trial` into mentions of the roles
    `settings` (its guild's) has registered."""
    channel = bot.get_partial_messageable(announcement.channel_id, guild_id=announcement.guild_id)
    await channel.send(render(announcement.message, settings))
    log.info("Posted announcement %s for guild %s", announcement.id, announcement.guild_id)


def render(message: str, settings: GuildSettings) -> str:
    """Expands the `@Raider`/`@Trial` placeholders in `message` into real role mentions. A role the guild
    hasn't registered is left as its plain-text placeholder."""
    replacements = [
        (placeholder, role_id) for placeholder, role_id in (("@Raider", settings.raider_role_id), ("@Trial", settings.trial_role_id)) if role_id is not None
    ]
    return replace_role_mentions(message, replacements)
