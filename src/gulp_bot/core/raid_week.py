"""Raid weeks: they run Tuesday, the game's weekly reset, to Monday, and each is named by its Tuesday. Times
are Eastern."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta

from .schedule_parsing import NEW_YORK


def week_start(day: date) -> date:
    """The Tuesday on or before `day`: raid weeks run Tuesday (the game's weekly reset) to Monday."""
    return day - timedelta(days=(day.weekday() + 6) % 7)


def week_end(tuesday: date) -> date:
    """The Monday that ends the raid week starting `tuesday`."""
    return tuesday + timedelta(days=6)


def weeks_between(first: date, last: date) -> Iterator[date]:
    """The Tuesday of every raid week with a day from `first` to `last`, both included, in order."""
    tuesday = week_start(first)
    while tuesday <= last:
        yield tuesday
        tuesday += timedelta(days=7)


def week_begins(tuesday: date) -> datetime:
    """Midnight Eastern at the start of the raid week beginning `tuesday`."""
    return datetime.combine(tuesday, datetime.min.time(), NEW_YORK)
