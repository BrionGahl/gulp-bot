"""The nights a guild raids (`/settings raid_nights`): reading what an officer typed, the raid nights of a
week, and the raid attendance schedules worked out from them. No Discord in here; times are Eastern.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..core.cron import Schedule
from ..core.schedule_parsing import NEW_YORK, days_text, parse_days, parse_time_of_day, time_text, weekly_schedule
from ..exception.schedule_exception import ScheduleProblem
from ..schema.guild_settings_schema import GuildSettings, RaidNight, RaidNights


def parse_raid_nights(days: str, start: str, end: str) -> RaidNights:
    """Raid nights as an officer typed them. Raises `ScheduleProblem` saying what is wrong."""
    nights = RaidNights(parse_days(days), parse_time_of_day(start), parse_time_of_day(end))
    if nights.start == nights.end:
        raise ScheduleProblem("A raid can't start and end at the same time.")
    return nights


def crosses_midnight(nights: RaidNights) -> bool:
    return nights.end < nights.start


def nights_in_week(nights: RaidNights, tuesday: date) -> list[RaidNight]:
    """The raid nights of the Tuesday-to-Monday week starting on `tuesday`, in order. A night belongs to
    the day it starts on."""
    result = []
    for offset in range(7):
        day = tuesday + timedelta(days=offset)
        if day.weekday() not in nights.days:
            continue
        start = datetime.combine(day, nights.start, NEW_YORK)
        end_day = day + timedelta(days=1) if crosses_midnight(nights) else day
        result.append(RaidNight(start, datetime.combine(end_day, nights.end, NEW_YORK)))
    return result


def nights_between(nights: RaidNights, first: date, last: date) -> list[RaidNight]:
    """The raid nights that start on a day from `first` to `last`, both included, in order."""
    result = []
    tuesday = week_start(first)
    while tuesday <= last:
        result += [night for night in nights_in_week(nights, tuesday) if first <= night.start.date() <= last]
        tuesday += timedelta(days=7)
    return result


def week_start(day: date) -> date:
    """The Tuesday on or before `day`: raid weeks run Tuesday (the game's weekly reset) to Monday."""
    return day - timedelta(days=(day.weekday() + 6) % 7)


def start_schedule(nights: RaidNights) -> Schedule:
    """Fires at the start of every raid night."""
    return weekly_schedule(nights.days, nights.start)


def end_schedule(nights: RaidNights) -> Schedule:
    """Fires at the end of every raid night: the next day for a raid that runs past midnight."""
    days = frozenset((day + 1) % 7 for day in nights.days) if crosses_midnight(nights) else nights.days
    return weekly_schedule(days, nights.end)


def attendance_schedules(settings: GuildSettings) -> tuple[Schedule, Schedule] | None:
    """When raid attendance is recorded: at the start and end of every raid night once those are
    registered, else on the schedules registered with raid voice before raid nights existed. `None` if
    attendance is off."""
    if settings.raid_voice is None:
        return None
    if settings.raid_nights is not None:
        return start_schedule(settings.raid_nights), end_schedule(settings.raid_nights)
    return settings.raid_voice.start, settings.raid_voice.end


def describe_raid_nights(nights: RaidNights) -> str:
    """`Tue, Thu, 8:00 PM – 11:00 PM Eastern`."""
    days = days_text(nights.days)
    past_midnight = " (the next day)" if crosses_midnight(nights) else ""
    return f"{days}, {time_text(nights.start)} – {time_text(nights.end)}{past_midnight} Eastern"
