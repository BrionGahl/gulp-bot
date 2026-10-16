"""Raid attendance: who is in the raid voice channel at the start and the end of each raid."""

from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import raid_notes, schedules, wcl_raid_report
from .db import from_db, to_db

if TYPE_CHECKING:
    import asyncpg

    from .bot import GulpBot

log = logging.getLogger(__name__)

# The longest the scheduler sleeps before re-reading every guild's settings, in seconds, which bounds
# how long a change made through `/settings raid_voice` takes to be picked up.
MAX_SLEEP = 60.0

# Discord's limits on an embed's description and on one field's value; a longer attendance list is cut
# short to fit.
MAX_DESCRIPTION_CHARS = 4096
MAX_FIELD_CHARS = 1024

# The purple of the bot's embeds.
EMBED_COLOUR = 0xAF69EE

# How long before a raid-end snapshot a raid-start snapshot can have been taken and still be treated as
# the start of that same raid. Anything older is a start whose end never fired (the bot was down, or
# the schedules don't pair up), and comparing against it would be misleading.
MAX_RAID_HOURS = 12


class Moment(enum.Enum):
    """Which point of the raid a snapshot was taken at. The value is how it is named in messages."""

    START = "start"
    END = "end"
    # Taken on demand with `/test_raid_attendance`.
    TEST = "manual check"


async def schedule_snapshots(bot: GulpBot) -> None:
    """Runs forever, recording who is in each guild's raid voice channel whenever that guild's raid start
    or raid end schedule fires (`/settings raid_voice`), and posting the list to that week's raid notes
    thread. Uses the same wake-up scheme as `raid_notes.schedule_weekly_posts`.

    The raid end schedule is also what the Warcraft Logs overview of the night goes by: it is posted to
    the same thread a little later, see `wcl_raid_report`."""
    last_checked = schedules.now()

    while True:
        now = schedules.now()
        next_wake = now + timedelta(seconds=MAX_SLEEP)

        for guild_id, settings in bot.guild_settings.all():
            raid_voice = settings.raid_voice
            if raid_voice is None:
                continue

            for moment, schedule in ((Moment.START, raid_voice.start), (Moment.END, raid_voice.end)):
                fire_time = schedule.after(last_checked)
                if fire_time is not None and fire_time.timestamp() <= now.timestamp():
                    # Checked only once a snapshot is due, so that registering the raid notes channel
                    # later needs no change here.
                    if settings.raid_notes_channel_id is not None:
                        try:
                            await record(bot, guild_id, raid_voice.channel_id, settings.raid_notes_channel_id, moment, fire_time)
                        except Exception as e:
                            log.error("Failed to record raid %s attendance for guild %s: %s", moment.value, guild_id, e)
                    else:
                        log.error("Skipping raid %s attendance for guild %s: no raid notes channel is registered", moment.value, guild_id)

                    # On a task of its own: it waits for the log to finish uploading, and the other
                    # guilds' snapshots can't wait with it.
                    if moment is Moment.END:
                        bot.spawn(wcl_raid_report.post_after_raid(bot, guild_id, fire_time))

                upcoming = schedule.after(now)
                if upcoming is not None:
                    next_wake = min(next_wake, upcoming, key=datetime.timestamp)

        last_checked = now
        # Measured from the current time, not `now`: posting above may have taken a while.
        await asyncio.sleep(max(0.0, next_wake.timestamp() - schedules.now().timestamp()))


async def record(bot: GulpBot, guild_id: int, voice_channel_id: int, raid_notes_channel_id: int, moment: Moment, at: datetime) -> int:
    """Takes a snapshot of who is in `voice_channel_id` right now and posts it to the raid notes thread for
    the week containing `at`, creating that thread if the week doesn't have one yet (raids usually happen
    before the weekly thread's own schedule fires). Returns the thread's ID."""
    from .helper import embed_from_dict

    # Taken before any awaiting below, so the list reflects the scheduled moment as closely as possible
    # rather than whenever Discord got round to creating the thread.
    present = members_in_voice(bot, guild_id, voice_channel_id)

    post = await raid_notes.week_post(bot, guild_id, raid_notes_channel_id, at.date())
    thread = bot.get_partial_messageable(post.thread_id, guild_id=guild_id)

    # Mentions inside an embed never notify anyone, so the list is a record, not a summons.
    await thread.send(embed=embed_from_dict(snapshot_embed(moment, at, voice_channel_id, present)))
    log.info("Recorded raid %s attendance for guild %s: %d present", moment.value, guild_id, len(present))

    if moment is Moment.START:
        await save_start(bot.db, guild_id, voice_channel_id, at, present)
    elif moment is Moment.END:
        # Taken (not just read) so one start is only ever compared against one end.
        start = await take_start(bot.db, guild_id)
        if start is not None and not start.pairs_with(voice_channel_id, at):
            start = None
        await thread.send(embed=embed_from_dict(summary_embed(start, at, present)))
    # A manual check is neither end of a raid: it mustn't disturb a real start snapshot.

    return post.thread_id


@dataclass(frozen=True)
class StartSnapshot:
    """A stored raid-start snapshot."""

    voice_channel_id: int
    taken_at: datetime
    present: list[int]

    def pairs_with(self, voice_channel_id: int, end: datetime) -> bool:
        """Whether this is plausibly the start of the raid ending at `end` in `voice_channel_id`."""
        elapsed = end.timestamp() - self.taken_at.timestamp()
        return self.voice_channel_id == voice_channel_id and 0 <= elapsed <= MAX_RAID_HOURS * 3600


async def save_start(db: asyncpg.Pool, guild_id: int, voice_channel_id: int, at: datetime, present: list[int]) -> None:
    """Stores `present` as the guild's current raid-start snapshot, replacing any earlier one."""
    await db.execute(
        "INSERT INTO raid_attendance_starts (guild_id, voice_channel_id, taken_at, user_ids) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (guild_id) DO UPDATE SET voice_channel_id = EXCLUDED.voice_channel_id, "
        "taken_at = EXCLUDED.taken_at, user_ids = EXCLUDED.user_ids",
        to_db(guild_id),
        to_db(voice_channel_id),
        at.astimezone(UTC),
        [to_db(user_id) for user_id in present],
    )


async def take_start(db: asyncpg.Pool, guild_id: int) -> StartSnapshot | None:
    """Removes and returns the guild's stored raid-start snapshot, if it has one."""
    row = await db.fetchrow(
        "DELETE FROM raid_attendance_starts WHERE guild_id = $1 RETURNING voice_channel_id, taken_at, user_ids",
        to_db(guild_id),
    )
    if row is None:
        return None
    return StartSnapshot(from_db(row["voice_channel_id"]), row["taken_at"], [from_db(user_id) for user_id in row["user_ids"]])


def members_in_voice(bot: GulpBot, guild_id: int, voice_channel_id: int) -> list[int]:
    """Everyone currently connected to `voice_channel_id`, from the gateway cache, sorted by ID so the
    start and end lists are easy to compare. Bots are left out where the cache knows the account is one."""
    guild = bot.get_guild(guild_id)
    if guild is None:
        raise RuntimeError("the server isn't in the bot's cache, so its voice channels can't be read")

    present = []
    for user_id, voice_state in guild._voice_states.items():
        if voice_state.channel is None or voice_state.channel.id != voice_channel_id:
            continue
        member = guild.get_member(user_id)
        if member is not None and member.bot:
            continue
        present.append(user_id)
    return sorted(present)


def _embed(title: str, at: datetime) -> dict[str, Any]:
    return {"type": "rich", "title": title, "color": EMBED_COLOUR, "timestamp": at.astimezone(UTC).isoformat()}


def snapshot_embed(moment: Moment, at: datetime, voice_channel_id: int, present: list[int]) -> dict[str, Any]:
    """The embed for one snapshot: who was in the voice channel at that moment."""
    embed = _embed(f"Raid attendance — {moment.value}", at)
    embed["description"] = snapshot_description(at, voice_channel_id, present)
    return embed


def snapshot_description(at: datetime, voice_channel_id: int, present: list[int]) -> str:
    description = f"<#{voice_channel_id}> · <t:{int(at.timestamp())}:f>\n\n"
    if not present:
        return description + "Nobody was in the channel."
    description += f"**{len(present)} present**\n"
    return description + mention_list(present, MAX_DESCRIPTION_CHARS - len(description.encode("utf-8")))


def summary_embed(start: StartSnapshot | None, end_at: datetime, present_at_end: list[int]) -> dict[str, Any]:
    """The embed posted after a raid-end snapshot, comparing it with the raid's start snapshot, or saying
    there was none to compare with."""
    embed = _embed("Raid attendance — summary", end_at)
    if start is None:
        embed["description"] = "No start snapshot was recorded for this raid, so there's nothing to compare the end against."
        return embed

    comparison = Comparison.between(start.present, present_at_end)

    def listed(users: list[int]) -> str:
        return mention_list(users, MAX_FIELD_CHARS) if users else "Nobody"

    embed["description"] = f"<t:{int(start.taken_at.timestamp())}:t> to <t:{int(end_at.timestamp())}:t>"
    embed["fields"] = [
        {"name": "There at both the start and the end", "value": str(comparison.stayed), "inline": False},
        {"name": f"Left before the end ({len(comparison.left_early)})", "value": listed(comparison.left_early), "inline": False},
        {"name": f"Joined after the start ({len(comparison.joined_late)})", "value": listed(comparison.joined_late), "inline": False},
    ]
    return embed


@dataclass(frozen=True)
class Comparison:
    """How the people present at the end of a raid differ from those present at its start."""

    # How many were there at both moments.
    stayed: int
    left_early: list[int]
    joined_late: list[int]

    @classmethod
    def between(cls, present_at_start: list[int], present_at_end: list[int]) -> Comparison:
        left_early = [user for user in present_at_start if user not in present_at_end]
        joined_late = [user for user in present_at_end if user not in present_at_start]
        return cls(len(present_at_start) - len(left_early), left_early, joined_late)


def mention_list(users: list[int], max_chars: int) -> str:
    """`users` as comma-separated mentions, in at most `max_chars`. If they don't all fit, the list is cut
    short and ends with a line saying how many were left off."""
    text = ""
    for listed, user_id in enumerate(users):
        mention = f"<@{user_id}>"
        separator = "" if listed == 0 else ", "
        overflow_note = f"\n…and {len(users) - listed} more."
        # Always keep room for the note, so the count of who was left off is never itself cut.
        if len(text.encode("utf-8")) + len(separator) + len(mention) + len(overflow_note.encode("utf-8")) > max_chars:
            text += overflow_note
            break
        text += separator + mention
    return text
