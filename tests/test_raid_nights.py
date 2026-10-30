"""Raid nights: reading what an officer typed, the nights of a week, and the attendance schedules."""

from datetime import date, datetime, time

import pytest

from gulp_bot.core.cron import Schedule
from gulp_bot.core.schedule_parsing import NEW_YORK, parse_days, parse_time_of_day
from gulp_bot.exception.schedule_exception import ScheduleProblem
from gulp_bot.schema.guild_settings_schema import GuildSettings, RaidNight, RaidNights, RaidVoice
from gulp_bot.service.raid_nights_service import (
    attendance_schedules,
    describe_raid_nights,
    end_schedule,
    nights_in_week,
    parse_raid_nights,
    start_schedule,
)

TUE_THU = RaidNights(frozenset({1, 3}), time(20, 0), time(23, 0))
LATE = RaidNights(frozenset({4, 5}), time(22, 0), time(1, 0))


def eastern(*args):
    return datetime(*args, tzinfo=NEW_YORK)


def test_days_are_read_by_name_in_any_case_and_separator():
    assert parse_days("Tue, Thu") == {1, 3}
    assert parse_days("tuesday and THURSDAY") == {1, 3}
    assert parse_days("mon/weds thurs sun") == {0, 2, 3, 6}
    for bad in ["", "  ", "tue, someday", "1, 3"]:
        with pytest.raises(ScheduleProblem):
            parse_days(bad)


def test_times_are_read_as_typed():
    assert parse_time_of_day("20:00") == time(20, 0)
    assert parse_time_of_day("8pm") == time(20, 0)
    assert parse_time_of_day("8:30 PM") == time(20, 30)
    assert parse_time_of_day("12am") == time(0, 0)
    assert parse_time_of_day("12 p.m.") == time(12, 0)
    assert parse_time_of_day("20") == time(20, 0)
    for bad in ["", "25:00", "13pm", "0am", "20:60", "eight"]:
        with pytest.raises(ScheduleProblem):
            parse_time_of_day(bad)


def test_a_raid_cannot_start_and_end_at_once():
    assert parse_raid_nights("Tue, Thu", "8pm", "11pm") == TUE_THU
    with pytest.raises(ScheduleProblem):
        parse_raid_nights("Tue", "8pm", "20:00")


def test_the_nights_of_a_week_run_tuesday_to_monday():
    assert nights_in_week(TUE_THU, date(2026, 11, 3)) == [
        RaidNight(eastern(2026, 11, 3, 20), eastern(2026, 11, 3, 23)),
        RaidNight(eastern(2026, 11, 5, 20), eastern(2026, 11, 5, 23)),
    ]
    # A Monday raid is the last night of the week, not the first.
    monday = RaidNights(frozenset({0, 1}), time(20, 0), time(23, 0))
    assert [night.start.date() for night in nights_in_week(monday, date(2026, 11, 3))] == [date(2026, 11, 3), date(2026, 11, 9)]


def test_a_raid_past_midnight_ends_the_next_day():
    nights = nights_in_week(LATE, date(2026, 11, 3))
    assert nights[0] == RaidNight(eastern(2026, 11, 6, 22), eastern(2026, 11, 7, 1))
    assert nights[1].end == eastern(2026, 11, 8, 1)


def test_the_nights_keep_their_wall_clock_across_daylight_saving():
    # Daylight saving time ends on Sunday, November 1, 2026.
    before, after = nights_in_week(TUE_THU, date(2026, 10, 27))[0], nights_in_week(TUE_THU, date(2026, 11, 3))[0]
    assert before.start.hour == after.start.hour == 20
    assert before.start.utcoffset() != after.start.utcoffset()


def test_attendance_is_recorded_at_the_start_and_end_of_each_night():
    assert start_schedule(TUE_THU).source == "0 0 20 * * Tue,Thu *"
    assert end_schedule(TUE_THU).source == "0 0 23 * * Tue,Thu *"
    assert start_schedule(LATE).source == "0 0 22 * * Fri,Sat *"
    assert end_schedule(LATE).source == "0 0 1 * * Sat,Sun *"
    # A Sunday raid past midnight ends on Monday.
    assert end_schedule(RaidNights(frozenset({6}), time(22, 0), time(1, 0))).source == "0 0 1 * * Mon *"
    assert start_schedule(TUE_THU).after(eastern(2026, 11, 3, 21)) == eastern(2026, 11, 5, 20)


def test_attendance_follows_the_raid_nights_once_they_are_set():
    old = RaidVoice(77, Schedule.parse("0 0 19 * * Wed *"), Schedule.parse("0 0 22 * * Wed *"))
    assert attendance_schedules(GuildSettings()) is None
    assert attendance_schedules(GuildSettings(raid_nights=TUE_THU)) is None
    assert attendance_schedules(GuildSettings(raid_voice=old)) == (old.start, old.end)
    start, end = attendance_schedules(GuildSettings(raid_voice=old, raid_nights=TUE_THU))  # type: ignore[misc]
    assert (start.source, end.source) == ("0 0 20 * * Tue,Thu *", "0 0 23 * * Tue,Thu *")


def test_raid_nights_are_described_in_week_order():
    assert describe_raid_nights(TUE_THU) == "Tue, Thu, 8:00 PM – 11:00 PM Eastern"
    assert describe_raid_nights(RaidNights(frozenset({0, 1}), time(20, 30), time(0, 30))) == "Tue, Mon, 8:30 PM – 12:30 AM (the next day) Eastern"
