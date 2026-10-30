"""Schedules typed by officers: reading them, describing them, and when they fire.

Officers give a schedule as days of the week and a time of day (`Tue, Thu` and `8pm`). It is kept as a
quartz-style cron expression (see `cron.py`), evaluated on the wall clock of `America/New_York`; schedules
stored before days and times were asked for can be any cron expression, and keep working.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..exception.schedule_exception import ScheduleProblem
from .cron import CronError, Schedule

# The zone every schedule is evaluated in. `America/New_York` rather than a fixed UTC offset, so a
# schedule like "9:00 AM Friday" keeps landing at 9:00 AM local time across the EST/EDT switch.
NEW_YORK = ZoneInfo("America/New_York")

# The names a day may be typed as, in any case, by `date.weekday()` (Monday 0 to Sunday 6).
DAY_NAMES = {
    0: ("mon", "monday"),
    1: ("tue", "tues", "tuesday"),
    2: ("wed", "weds", "wednesday"),
    3: ("thu", "thur", "thurs", "thursday"),
    4: ("fri", "friday"),
    5: ("sat", "saturday"),
    6: ("sun", "sunday"),
}
_DAY_BY_NAME = {name: day for day, names in DAY_NAMES.items() for name in names}

# How a day is shown, and named in a cron expression.
SHORT_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Raid weeks run Tuesday to Monday (`raid_notes_service.week_start`), so days are listed in that order.
WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0]

_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m?\.?$|^(\d{1,2}):(\d{2})$|^(\d{1,2})$")

# What every field but the time and the days is in a schedule made of days and a time.
_EVERY = Schedule.parse("0 0 0 * * * *")


def now_in_new_york() -> datetime:
    """The current time in the zone every schedule is evaluated in."""
    return datetime.now(NEW_YORK)


def parse_days(text: str) -> frozenset[int]:
    """The days of the week in `text`: names separated by commas, spaces or "and" (`Tue, Thu`,
    `tuesday and thursday`). Raises `ScheduleProblem` saying what is wrong."""
    words = [word for word in re.split(r"[\s,/&+]+|\band\b", text.strip().lower()) if word]
    if not words:
        raise ScheduleProblem("Give at least one day, e.g. `Tue, Thu`.")
    days = set()
    for word in words:
        if word not in _DAY_BY_NAME:
            raise ScheduleProblem(f"`{word}` isn't a day of the week. Use names like `Tue, Thu` or `tuesday, thursday`.")
        days.add(_DAY_BY_NAME[word])
    return frozenset(days)


def parse_time_of_day(text: str) -> time:
    """A time of day as typed: `20:00`, `8pm`, `8:30 PM`, `20`. Raises `ScheduleProblem` saying what is
    wrong."""
    match = _TIME.match(text.strip().lower())
    if match is None:
        raise ScheduleProblem(f"`{text.strip()}` isn't a time. Use `20:00`, `8pm` or `8:30 PM`.")
    hour_12, minute_12, meridiem, hour_24, minute_24, hour_only = match.groups()
    if meridiem is not None:
        hour, minute = int(hour_12), int(minute_12 or 0)
        if not 1 <= hour <= 12:
            raise ScheduleProblem(f"`{text.strip()}` isn't a time: with am or pm, the hour is 1 to 12.")
        hour = hour % 12 + (12 if meridiem == "p" else 0)
    else:
        hour, minute = (int(hour_24), int(minute_24)) if hour_24 is not None else (int(hour_only), 0)
    if hour > 23 or minute > 59:
        raise ScheduleProblem(f"`{text.strip()}` isn't a time of day.")
    return time(hour, minute)


_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_MONTH_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?$")


_ORDINAL = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?$")

_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}

# Words around a day that don't change which day it is: "on thursday", "this thursday", "thursday night".
_FILLER = {"on", "this", "the", "coming", "night", "nite", "evening", "raid", "of"}


def _words(text: str) -> list[str]:
    """`text` lowercased, in words, without the punctuation around them ("Thursday!" is `thursday`)."""
    return [word for word in re.split(r"[\s,!?.;:()]+", text.strip().lower()) if word]


def _next_day_of_month(day_of_month: int, today: date, month: int | None = None) -> date:
    """The next `day_of_month` (of `month`, if given) from `today` on. Raises `ValueError` if there is none."""
    if month is not None:
        this_year = date(today.year, month, day_of_month)
        return this_year if this_year >= today else date(today.year + 1, month, day_of_month)
    for ahead in range(13):
        year, month_index = divmod(today.month - 1 + ahead, 12)
        try:
            candidate = date(today.year + year, month_index + 1, day_of_month)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    raise ValueError(day_of_month)


def parse_date(text: str, today: date) -> date:
    """A day as a member types or writes it, counted from `today`: `today`, `tonight`, `tomorrow`; a day of
    the week, the next one from today on (`thu`, `this thursday`, `thursday night`), or `next thu`, the next
    one after today; a day of the month (`the 12th`, `nov 12`, `november 12th`), `11/4` (this year, or next
    year once it has passed), `11/4/2026` or `2026-11-04`. Raises `ScheduleProblem` saying what is wrong."""
    shown = text.strip()
    words = _words(shown)
    after_today = bool(words) and words[0] == "next"
    if after_today:
        words = words[1:]
    words = [word for word in words if word not in _FILLER]

    if words in (["today"], ["tonight"]) and not after_today:
        return today
    if words in (["tomorrow"], ["tmrw"], ["tmr"]) and not after_today:
        return today + timedelta(days=1)
    if len(words) == 1:
        day = next((weekday for weekday, names in DAY_NAMES.items() if words[0] in names), None)
        if day is not None:
            ahead = (day - today.weekday()) % 7
            return today + timedelta(days=ahead or (7 if after_today else 0))
    try:
        if len(words) == 1 and (match := _ORDINAL.match(words[0])) is not None and not after_today:
            return _next_day_of_month(int(match[1]), today)
        if len(words) == 2 and not after_today:
            month_first = words[0] in _MONTHS and _ORDINAL.match(words[1])
            day_first = words[1] in _MONTHS and _ORDINAL.match(words[0])
            if month_first or day_first:
                month_word, day_word = (words[0], words[1]) if month_first else (words[1], words[0])
                return _next_day_of_month(int(_ORDINAL.match(day_word)[1]), today, _MONTHS[month_word])  # type: ignore[index]
        if (match := _ISO_DATE.match(shown)) is not None:
            year, month, day_of_month = (int(part) for part in match.groups())
            return date(year, month, day_of_month)
        if (match := _MONTH_DAY.match(shown)) is not None:
            month, day_of_month, year_text = int(match[1]), int(match[2]), match[3]
            if year_text is not None:
                return date(int(year_text) + (2000 if len(year_text) == 2 else 0), month, day_of_month)
            return _next_day_of_month(day_of_month, today, month)
    except ValueError:
        raise ScheduleProblem(f"`{shown}` isn't a day of the calendar.") from None
    raise ScheduleProblem(f"`{shown}` isn't a day. Use `tue`, `tomorrow`, `11/4` or `2026-11-04`.")


def parse_span(text: str, today: date) -> tuple[date, date]:
    """The days a member means, counted from `today`: a week (`this week`, from today to Sunday; `next week`,
    the Monday-to-Sunday week after), the weekend (`this weekend`), or one day as `parse_date` reads it.
    Weeks are calendar weeks, the way people talk, not raid weeks; on a Sunday, both mean the week starting
    tomorrow. Raises `ScheduleProblem` saying what is wrong."""
    words = [word for word in _words(text) if word not in {"the", "of", "for", "all", "rest", "whole", "entire"}]
    sunday = today + timedelta(days=6 - today.weekday())
    coming_week = (today + timedelta(days=1), today + timedelta(days=7))
    if words in (["this", "week"], ["week"]):
        return coming_week if today.weekday() == 6 else (today, sunday)
    if words == ["next", "week"]:
        return coming_week if today.weekday() == 6 else (sunday + timedelta(days=1), sunday + timedelta(days=7))
    if words in (["this", "weekend"], ["weekend"]):
        if today.weekday() == 6:
            return today, today
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        return saturday, saturday + timedelta(days=1)
    if words == ["next", "weekend"]:
        saturday = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
        return saturday, saturday + timedelta(days=1)
    day = parse_date(text, today)
    return day, day


def weekly_schedule(days: frozenset[int], at: time) -> Schedule:
    """Fires at `at` on each of `days` (`date.weekday()`)."""
    names = ",".join(SHORT_DAY_NAMES[day] for day in sorted(days))
    return Schedule.parse(f"0 {at.minute} {at.hour} * * {names} *")


def parse_weekly_schedule(days: str, at: str) -> Schedule:
    """A schedule as an officer typed it: days of the week and a time of day. Raises `ScheduleProblem`
    saying what is wrong."""
    return weekly_schedule(parse_days(days), parse_time_of_day(at))


def time_text(at: time) -> str:
    """`20:00` as `8:00 PM`."""
    return f"{at.hour % 12 or 12}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}"


def days_text(days: frozenset[int]) -> str:
    """`Tue, Thu`, in the order of a raid week; `Every day` for all seven."""
    if len(days) == 7:
        return "Every day"
    return ", ".join(SHORT_DAY_NAMES[day] for day in WEEK_ORDER if day in days)


def describe_schedule(schedule: Schedule) -> str:
    """`Tue, Thu, 8:00 PM Eastern` for a schedule of days and a time, which is every schedule an officer
    can type now; the cron expression itself for an older one that isn't."""
    weekly = (
        schedule.seconds == {0}
        and len(schedule.minutes) == 1
        and len(schedule.hours) == 1
        and (schedule.days_of_month, schedule.months, schedule.years) == (_EVERY.days_of_month, _EVERY.months, _EVERY.years)
    )
    if not weekly or not schedule.days_of_week:
        return f"`{schedule.source}`"
    # The expression counts Sunday as 1; `date.weekday()` counts Monday as 0.
    days = frozenset((day + 5) % 7 for day in schedule.days_of_week)
    (hour,), (minute,) = schedule.hours, schedule.minutes
    return f"{days_text(days)}, {time_text(time(hour, minute))} Eastern"


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
