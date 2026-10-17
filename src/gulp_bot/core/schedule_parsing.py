"""Schedules typed by officers: reading them, and when they fire.

Every schedule is a quartz-style cron expression (see `cron.py`) evaluated on the wall clock of
`America/New_York`.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..exception.schedule_exception import ScheduleProblem
from .cron import CronError, Schedule

# The zone every schedule is evaluated in. `America/New_York` rather than a fixed UTC offset, so a
# schedule like "9:00 AM Friday" keeps landing at 9:00 AM local time across the EST/EDT switch.
NEW_YORK = ZoneInfo("America/New_York")

# Closest together two fire times of one schedule may be. Every fire time is a message or forum post,
# so without a floor a mistyped expression (`* * * * * * *`) would post every second.
MIN_INTERVAL_HOURS = 1

# Example shown to users when their expression can't be read: Fridays at 9:00 AM.
EXAMPLE_CRON = "0 0 9 * * Fri *"


def now_in_new_york() -> datetime:
    """The current time in the zone every schedule is evaluated in."""
    return datetime.now(NEW_YORK)


def parse_user_schedule(expression: str) -> Schedule:
    """Parses a quartz-style cron expression an officer typed, rejecting ones that never fire or fire more
    often than `MIN_INTERVAL_HOURS`. Raises `ScheduleProblem` saying why."""
    try:
        schedule = Schedule.parse(expression)
    except CronError as e:
        raise ScheduleProblem(
            f"Couldn't read that as a cron expression ({e}). Use the quartz-style format "
            f"`sec min hour day-of-month month day-of-week [year]`, e.g. `{EXAMPLE_CRON}`."
        ) from None

    upcoming = schedule.upcoming(now_in_new_york(), 6)
    if not upcoming:
        raise ScheduleProblem("That schedule never fires in the future.")
    if any(later.timestamp() - earlier.timestamp() < MIN_INTERVAL_HOURS * 3600 for earlier, later in zip(upcoming, upcoming[1:], strict=False)):
        raise ScheduleProblem(f"That schedule fires more than once every {MIN_INTERVAL_HOURS} hour(s), which is too often.")
    return schedule


def parse_stored_schedule(expression: str) -> Schedule | None:
    """An expression read back from the database, or `None` if it can't be read (only possible if the row
    was edited by hand: expressions are validated before they are stored)."""
    try:
        return Schedule.parse(expression)
    except CronError:
        return None


def next_fire_time(schedule: Schedule) -> datetime | None:
    """The next time `schedule` fires."""
    return schedule.after(now_in_new_york())


def next_fire_discord_timestamp(schedule: Schedule, style: str = "F", never: str = "unknown") -> str:
    """The next fire time as a Discord timestamp (`<t:...:F>`), or `never` if there is none."""
    fire = next_fire_time(schedule)
    return never if fire is None else f"<t:{int(fire.timestamp())}:{style}>"
