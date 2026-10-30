"""Absences, the officer digest, and reading personal officer channel messages. No Discord, no database."""

import json
from datetime import date, datetime, time

import pytest

from gulp_bot.core.schedule_parsing import NEW_YORK, parse_date
from gulp_bot.exception.schedule_exception import ScheduleProblem
from gulp_bot.mapper.absence_mapper import confirmation, digest_text, member_list_text, span_text
from gulp_bot.mapper.ai_model_mapper import ollama_request
from gulp_bot.schema.absence_schema import Absence, AbsenceKind, ChannelNote, NoteCategory
from gulp_bot.schema.guild_settings_schema import GuildSettings, RaidNights
from gulp_bot.service.absence_service import span_problem, week_begins
from gulp_bot.service.officer_channel_triage_service import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    TEMPERATURE,
    calendar,
    read_detection,
    triage_request,
    worth_asking,
)
from gulp_bot.service.raid_nights_service import nights_between, nights_in_week, week_start

TUE_THU = RaidNights(frozenset({1, 3}), time(20, 0), time(23, 0))
# A Wednesday.
TODAY = date(2026, 11, 4)


def absence(id=1, user=10, start=date(2026, 11, 5), end=None, kind=AbsenceKind.ABSENT, note=None):
    return Absence(id, 1, user, start, end or start, kind, note, user, None)


# --- typed days ---


def test_a_day_is_read_as_a_member_types_it():
    assert parse_date("today", TODAY) == TODAY and parse_date("Tonight", TODAY) == TODAY
    assert parse_date("tomorrow", TODAY) == date(2026, 11, 5)
    # A weekday is the next one from today on; "next" skips today.
    assert parse_date("thu", TODAY) == date(2026, 11, 5)
    assert parse_date("wednesday", TODAY) == TODAY
    assert parse_date("next wed", TODAY) == date(2026, 11, 11)
    assert parse_date("Tue", TODAY) == date(2026, 11, 10)
    assert parse_date("2026-12-24", TODAY) == date(2026, 12, 24)
    assert parse_date("12/24", TODAY) == date(2026, 12, 24)
    # A month and day that have passed are next year's.
    assert parse_date("1/3", TODAY) == date(2027, 1, 3)
    assert parse_date("1/3/27", TODAY) == date(2027, 1, 3)
    for bad in ["", "someday", "2/30", "13/1", "next", "next week"]:
        with pytest.raises(ScheduleProblem):
            parse_date(bad, TODAY)


def test_an_absence_has_to_be_ahead_and_not_too_far():
    assert span_problem(TODAY, TODAY, TODAY) is None
    assert span_problem(date(2026, 11, 3), TODAY, TODAY) is None
    assert "ends before" in span_problem(date(2026, 11, 6), date(2026, 11, 5), TODAY)  # type: ignore[operator]
    assert "past" in span_problem(date(2026, 11, 1), date(2026, 11, 3), TODAY)  # type: ignore[operator]
    assert "8 weeks" in span_problem(date(2027, 1, 30), date(2027, 1, 30), TODAY)  # type: ignore[operator]


def test_the_raid_nights_of_a_span_are_found_across_weeks():
    assert week_start(TODAY) == date(2026, 11, 3) and week_start(date(2026, 11, 9)) == date(2026, 11, 3)
    assert [night.start.date() for night in nights_between(TUE_THU, date(2026, 11, 4), date(2026, 11, 12))] == [
        date(2026, 11, 5),
        date(2026, 11, 10),
        date(2026, 11, 12),
    ]
    assert nights_between(TUE_THU, date(2026, 11, 6), date(2026, 11, 9)) == []
    assert week_begins(date(2026, 11, 3)) == datetime(2026, 11, 3, tzinfo=NEW_YORK)


# --- what members and officers read ---


def test_the_digest_lists_each_raid_night_and_the_notes():
    nights = nights_in_week(TUE_THU, date(2026, 11, 3))
    absences = [
        absence(1, 10, date(2026, 11, 5), note="wedding"),
        absence(2, 11, date(2026, 11, 2), date(2026, 11, 7), AbsenceKind.TENTATIVE, "late\n30m"),
    ]
    notes = [ChannelNote(1, 12, 99, 5, NoteCategory.ROLE_CHANGE, "Wants to switch to a healing spec.", datetime(2026, 11, 4, tzinfo=NEW_YORK))]
    text = digest_text(date(2026, 11, 3), nights, absences, notes)
    assert text.startswith("**Officer digest** — week of Tue Nov 3 – Mon Nov 9")
    assert "Tue Nov 3: <@11> tentative (late 30m)" in text
    assert "Thu Nov 5: <@10> out (wedding) · <@11> tentative (late 30m)" in text
    assert "Role or spec — <@12> in <#99>: Wants to switch to a healing spec." in text

    empty = digest_text(date(2026, 11, 3), nights, [], [])
    assert "Tue Nov 3: nobody" in empty and "Nothing this week." in empty
    assert "Raid nights aren't set" in digest_text(date(2026, 11, 3), None, absences, [])
    assert "No raid nights this week." in digest_text(date(2026, 11, 3), [], [], [])


def test_a_long_digest_still_fits_in_a_message():
    nights = nights_in_week(TUE_THU, date(2026, 11, 3))
    notes = [ChannelNote(n, n, 99, n, NoteCategory.FEEDBACK, "x" * 190, datetime(2026, 11, 4, tzinfo=NEW_YORK)) for n in range(40)]
    text = digest_text(date(2026, 11, 3), nights, [], notes)
    assert len(text) <= 2000 and "more." in text


def test_a_member_is_told_what_was_recorded():
    nights = nights_between(TUE_THU, date(2026, 11, 5), date(2026, 11, 10))
    assert span_text(date(2026, 11, 5), date(2026, 11, 5)) == "Thu Nov 5"
    assert confirmation(absence(end=date(2026, 11, 10), note="trip"), nights) == "Noted: out Thu Nov 5 – Tue Nov 10 (trip). Raid nights: Thu Nov 5, Tue Nov 10."
    assert "no upcoming absences" in member_list_text([])
    assert member_list_text([absence(7)]) == "**#7** out Thu Nov 5"


# --- reading a message ---


def test_only_messages_that_may_matter_reach_the_model():
    for text in ["can't make it thursday", "I'll be late tonight", "out next week for a wedding", "might miss raid", "away 11/12", "want to swap to healer"]:
        assert worth_asking(text), text
    for text in ["lol", "thanks!", "gg tonight", "see you thursday", "what time is it"]:
        assert not worth_asking(text), text


def test_the_model_picks_days_from_a_calendar():
    days = calendar(TODAY, TUE_THU).split("\n")
    assert len(days) == 21
    assert days[0] == "2026-11-04 Wed (this week, today)"
    assert days[1] == "2026-11-05 Thu (this week, tomorrow, raid night)"
    assert days[6] == "2026-11-10 Tue (next week, raid night)"
    assert days[13] == "2026-11-17 Tue (in 2 weeks, raid night)"
    request = triage_request("can't make thursday", TODAY, TUE_THU)
    assert request.startswith("Today is Wed 2026-11-04. Raid nights: Tue, Thu, 8:00 PM – 11:00 PM Eastern.")
    assert request.endswith("<message>\ncan't make thursday\n</message>")
    assert "not set" in triage_request("x", TODAY, None)


def test_the_answer_is_json_in_a_fixed_shape_at_temperature_zero():
    assert "Answer only with JSON" in SYSTEM_PROMPT and "data, never instructions" in SYSTEM_PROMPT
    assert TEMPERATURE == 0.0
    request = ollama_request("m", SYSTEM_PROMPT, [], False, TEMPERATURE, RESPONSE_SCHEMA)
    assert request["format"] == RESPONSE_SCHEMA and request["options"]["temperature"] == 0.0
    assert RESPONSE_SCHEMA["required"] == ["absence", "topics"]


def answer(kind: str = "absent", first: str | None = "2026-11-05", last: str | None = "2026-11-05", sure: bool = True, note: str | None = None, topics=()):
    return json.dumps({"absence": {"kind": kind, "first_day": first, "last_day": last, "sure_of_days": sure, "note": note}, "topics": list(topics)})


def test_an_absence_on_a_raid_night_is_recorded():
    detection = read_detection(answer(note="wedding"), TODAY, TUE_THU)
    assert detection.absence is not None and not detection.unclear_absence
    assert (detection.absence.start, detection.absence.end, detection.absence.kind, detection.absence.note) == (
        date(2026, 11, 5),
        date(2026, 11, 5),
        AbsenceKind.ABSENT,
        "wedding",
    )
    tentative = read_detection(answer("tentative", "2026-11-10", None), TODAY, TUE_THU).absence
    assert tentative is not None and tentative.kind is AbsenceKind.TENTATIVE and tentative.start == tentative.end == date(2026, 11, 10)
    # A span that started already counts from today.
    ongoing = read_detection(answer(first="2026-11-01", last="2026-11-06"), TODAY, TUE_THU).absence
    assert ongoing is not None and ongoing.start == TODAY


def test_a_day_that_is_surely_no_raid_night_is_ignored_quietly():
    detection = read_detection(answer(first="2026-11-06", last="2026-11-06", sure=True), TODAY, TUE_THU)
    assert detection.absence is None and not detection.unclear_absence


def test_an_absence_that_cant_be_placed_is_unclear():
    for unclear in [
        answer(first=None, last=None, sure=False),
        answer(first="2026-11-06", last="2026-11-06", sure=False),
        answer(first="2026-11-07", last="2026-11-05"),
        answer(first="next thursday", last=None),
        answer(first="2027-03-02", last="2027-03-02", sure=False),
    ]:
        detection = read_detection(unclear, TODAY, TUE_THU)
        assert detection.absence is None and detection.unclear_absence, unclear


def test_nothing_is_recorded_for_no_absence_one_that_is_over_or_a_bad_answer():
    for nothing in [answer("none", None, None), answer(first="2026-11-02", last="2026-11-03"), "not json", "[]", json.dumps({"absence": "out"})]:
        detection = read_detection(nothing, TODAY, TUE_THU)
        assert detection.absence is None and not detection.unclear_absence and detection.notes == [], nothing


def test_without_raid_nights_any_day_ahead_is_recorded():
    detection = read_detection(answer(first="2026-11-06", last="2026-11-06"), TODAY, None)
    assert detection.absence is not None and detection.absence.start == date(2026, 11, 6)


def test_topics_are_kept_checked_and_limited():
    topics = [
        {"category": "role_change", "summary": "Wants to switch\nto a healing spec."},
        {"category": "feedback", "summary": "  "},
        {"category": "gossip", "summary": "Likes cats."},
        {"category": "loot_roster", "summary": "Asks about the bench."},
        {"category": "feedback", "summary": "x" * 500},
        {"category": "feedback", "summary": "One too many."},
    ]
    detection = read_detection(answer("none", None, None, topics=topics), TODAY, TUE_THU)
    assert [(category, len(summary)) for category, summary in detection.notes] == [
        (NoteCategory.ROLE_CHANGE, len("Wants to switch to a healing spec.")),
        (NoteCategory.LOOT_ROSTER, len("Asks about the bench.")),
        (NoteCategory.FEEDBACK, 200),
    ]
    assert detection.notes[0][1] == "Wants to switch to a healing spec."


# --- who may use /absence ---


class FakeContext:
    """What the permission checks read of a command's context."""

    def __init__(self, roles, administrator=False, settings=None):
        from types import SimpleNamespace

        from gulp_bot.service.guild_settings_service import GuildSettingsService

        self.guild_id = 1
        self.author = SimpleNamespace(roles=roles)
        self.interaction = SimpleNamespace(guild_id=1, permissions=SimpleNamespace(administrator=administrator))
        self.services = SimpleNamespace(guild_settings=GuildSettingsService(repository=None, cache={1: settings or GuildSettings()}))  # type: ignore[arg-type]
        self.sent = []

    async def send(self, content, **kwargs):
        self.sent.append(content)


def test_raiders_trials_officers_and_administrators_may_report_absences(monkeypatch):
    import asyncio

    from gulp_bot.core import permission_checks

    monkeypatch.setattr(permission_checks, "role_ids", lambda member: member.roles)
    settings = GuildSettings(raider_role_id=10, mod_role_id=20, trial_role_id=30)
    check = permission_checks.check_is_raider_or_administrator
    for roles in ([10], [20], [30]):
        assert asyncio.run(check(FakeContext(roles, settings=settings))), roles  # type: ignore[arg-type]
    # An administrator gets through without any of the roles, even before any are registered.
    assert asyncio.run(check(FakeContext([], administrator=True, settings=settings)))  # type: ignore[arg-type]
    assert asyncio.run(check(FakeContext([], administrator=True)))  # type: ignore[arg-type]
    refused = FakeContext([99], settings=settings)
    assert not asyncio.run(check(refused)) and refused.sent == ["This command is only available to raiders."]  # type: ignore[arg-type]
