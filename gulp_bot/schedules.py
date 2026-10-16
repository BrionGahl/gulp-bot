"""User-supplied schedules: reading them, and when they fire."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .cron import CronError, Schedule

# The zone every schedule is evaluated in. `America/New_York` rather than a fixed UTC offset, so a
# schedule like "9:00 AM Friday" keeps landing at 9:00 AM local time across the EST/EDT switch.
NEW_YORK = ZoneInfo("America/New_York")

# Closest together two fire times of one schedule may be. Every fire time is a message or forum
# post, so without a floor a mistyped expression (`* * * * * * *`) would post every second.
MIN_INTERVAL_HOURS = 1

# Example shown to users when their expression can't be read: Fridays at 9:00 AM.
EXAMPLE_CRON = "0 0 9 * * Fri *"


def now() -> datetime:
    """The current time in the zone every schedule is evaluated in."""
    return datetime.now(NEW_YORK)


class ScheduleProblem(ValueError):
    """Why a schedule was turned down, written to be shown to the person who typed it."""


def parse_schedule(expression: str) -> Schedule:
    """Parses a user-supplied quartz-style cron expression, rejecting ones that never fire or fire
    more often than `MIN_INTERVAL_HOURS`."""
    try:
        schedule = Schedule.parse(expression)
    except CronError as e:
        raise ScheduleProblem(
            f"Couldn't read that as a cron expression ({e}). Use the quartz-style format "
            f"`sec min hour day-of-month month day-of-week [year]`, e.g. `{EXAMPLE_CRON}`."
        ) from None

    upcoming = schedule.upcoming(now(), 6)
    if not upcoming:
        raise ScheduleProblem("That schedule never fires in the future.")
    if any(later.timestamp() - earlier.timestamp() < MIN_INTERVAL_HOURS * 3600 for earlier, later in zip(upcoming, upcoming[1:], strict=False)):
        raise ScheduleProblem(f"That schedule fires more than once every {MIN_INTERVAL_HOURS} hour(s), which is too often.")
    return schedule


def try_parse(expression: str) -> Schedule | None:
    """A stored expression, or `None` if it can't be read (only possible if it was edited by hand)."""
    try:
        return Schedule.parse(expression)
    except CronError:
        return None


def next_fire_time(schedule: Schedule) -> datetime | None:
    """The next time `schedule` fires."""
    return schedule.after(now())


def discord_time(schedule: Schedule, style: str = "F", never: str = "unknown") -> str:
    """The next fire time as a Discord timestamp (`<t:...:F>`), or `never` if there is none."""
    fire = next_fire_time(schedule)
    return never if fire is None else f"<t:{int(fire.timestamp())}:{style}>"
