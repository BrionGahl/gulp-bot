"""Quartz-style cron expressions: the schedules stored in the database (raid notes, announcements, raid
attendance) are written in this syntax, so it must not change.

The format is `sec min hour day-of-month month day-of-week [year]`: six or seven fields, or one of the
shorthands `@yearly`, `@annually`, `@monthly`, `@weekly`, `@daily` and `@hourly`. A field is `*` or `?`
(every value), a number, a name (`Jan`..`Dec` for months, `Sun`..`Sat` for days of the week, in any
case, short or full), a range `a-b`, a step `x/n` (from `x`, which may be `*` or a range, every `n`),
or a comma-separated list of those. Days of the week are numbered 1 (Sunday) to 7 (Saturday). A time
matches when every field matches, the day of the month and the day of the week included.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_UTC = ZoneInfo("UTC")

MIN_YEAR = 1970
MAX_YEAR = 2100

# (name, lowest, highest) of each field, in order.
_FIELDS = [
    ("seconds", 0, 59),
    ("minutes", 0, 59),
    ("hours", 0, 23),
    ("days of the month", 1, 31),
    ("months", 1, 12),
    ("days of the week", 1, 7),
    ("years", MIN_YEAR, MAX_YEAR),
]

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTHS_FULL = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
_DAYS_FULL = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]

_SHORTHANDS = {
    "@yearly": "0 0 0 1 1 * *",
    "@annually": "0 0 0 1 1 * *",
    "@monthly": "0 0 0 1 * * *",
    "@weekly": "0 0 0 * * 1 *",
    "@daily": "0 0 0 * * * *",
    "@hourly": "0 0 * * * * *",
}


class CronError(ValueError):
    """The expression can't be read. The message says why, for the person who typed it."""


def _ordinal(token: str, field_index: int) -> int:
    name, lowest, highest = _FIELDS[field_index]
    if token.isascii() and token.isdigit():
        value = int(token)
    elif field_index == 4 and token.lower() in _MONTHS:
        value = _MONTHS.index(token.lower()) + 1
    elif field_index == 4 and token.lower() in _MONTHS_FULL:
        value = _MONTHS_FULL.index(token.lower()) + 1
    elif field_index == 5 and token.lower() in _DAYS:
        value = _DAYS.index(token.lower()) + 1
    elif field_index == 5 and token.lower() in _DAYS_FULL:
        value = _DAYS_FULL.index(token.lower()) + 1
    else:
        raise CronError(f"`{token}` isn't a valid value for {name}")
    if not lowest <= value <= highest:
        raise CronError(f"{value} is out of range for {name} ({lowest}-{highest})")
    return value


def _parse_field(text: str, field_index: int) -> frozenset[int]:
    name, lowest, highest = _FIELDS[field_index]
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"an empty item in the {name} field")
        base, slash, step_text = part.partition("/")
        if base in ("*", "?"):
            start, end = lowest, highest
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = _ordinal(first, field_index), _ordinal(last, field_index)
            if start > end:
                raise CronError(f"the range `{base}` in {name} ends before it starts")
        else:
            start = _ordinal(base, field_index)
            # A single value with a step runs from it to the end of the field.
            end = highest if slash else start
        step = 1
        if slash:
            if not (step_text.isascii() and step_text.isdigit()) or int(step_text) == 0:
                raise CronError(f"`{step_text}` isn't a valid step for {name}")
            step = int(step_text)
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class Schedule:
    """A parsed cron expression. `source` is the expression as it was given."""

    source: str
    seconds: frozenset[int]
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    years: frozenset[int]

    @classmethod
    def parse(cls, expression: str) -> Schedule:
        text = _SHORTHANDS.get(expression.strip().lower(), expression)
        fields = text.split()
        if len(fields) == 6:
            fields.append("*")
        if len(fields) != 7:
            raise CronError(f"expected 6 or 7 fields, found {len(fields)}")
        parsed = [_parse_field(field, index) for index, field in enumerate(fields)]
        return cls(expression, *parsed)

    def _day_matches(self, moment: datetime) -> bool:
        # Python counts Monday as 0; the expression counts Sunday as 1.
        weekday = (moment.weekday() + 1) % 7 + 1
        return moment.day in self.days_of_month and weekday in self.days_of_week

    def after(self, moment: datetime) -> datetime | None:
        """The first time strictly after `moment` that the schedule fires, in `moment`'s time zone
        (which must be a `ZoneInfo`), or `None` if it never does again.

        Times are matched on the wall clock of that zone, so "9:00 every Friday" stays at 9:00 across
        a daylight saving change. A wall-clock time the zone skips (the hour lost in spring) never
        fires; one it repeats (the hour gained in autumn) fires the first time round.
        """
        zone = moment.tzinfo
        if not isinstance(zone, ZoneInfo):
            raise ValueError("after() needs a datetime in a ZoneInfo time zone")

        candidate = moment.replace(tzinfo=None, microsecond=0) + timedelta(seconds=1)
        while candidate.year <= MAX_YEAR:
            if candidate.year not in self.years:
                later = [year for year in self.years if year > candidate.year]
                if not later:
                    return None
                candidate = datetime(min(later), 1, 1)
                continue
            if candidate.month not in self.months:
                candidate = _next_month(candidate)
                continue
            if not self._day_matches(candidate):
                candidate = datetime(candidate.year, candidate.month, candidate.day) + timedelta(days=1)
                continue
            if candidate.hour not in self.hours:
                candidate = candidate.replace(minute=0, second=0) + timedelta(hours=1)
                continue
            if candidate.minute not in self.minutes:
                candidate = candidate.replace(second=0) + timedelta(minutes=1)
                continue
            if candidate.second not in self.seconds:
                candidate += timedelta(seconds=1)
                continue

            aware = candidate.replace(tzinfo=zone, fold=0)
            in_utc = aware.astimezone(_UTC)
            # A wall-clock time the zone skips doesn't survive the round trip through UTC. Compared in
            # UTC, since Python compares two times of one zone by their wall clocks alone.
            if in_utc.astimezone(zone).replace(tzinfo=None) == candidate and in_utc > moment.astimezone(_UTC):
                return aware
            candidate += timedelta(seconds=1)
        return None

    def upcoming(self, moment: datetime, count: int) -> list[datetime]:
        """The next `count` fire times after `moment` (fewer if it stops firing)."""
        times: list[datetime] = []
        current = moment
        while len(times) < count:
            fire = self.after(current)
            if fire is None:
                break
            times.append(fire)
            current = fire
        return times


def _next_month(moment: datetime) -> datetime:
    if moment.month == 12:
        return datetime(moment.year + 1, 1, 1)
    return datetime(moment.year, moment.month + 1, 1)
