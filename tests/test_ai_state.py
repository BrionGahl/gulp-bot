import pytest

from gulp_bot.core.rate_limiter import RateLimiter
from gulp_bot.exception.ai_exception import ModelNameProblem
from gulp_bot.mapper.ai_usage_mapper import format_count, usage_lines
from gulp_bot.repository.ai_memory_repository import (
    MAX_FAILURE_DETAIL_CHARS,
    AnswerMessageRepository,
    ApiFailureRepository,
    ConversationHistoryRepository,
    TokenUsageRepository,
)
from gulp_bot.schema.ai_schema import Access, ApiFailure, TldrBusy, TldrReady, TldrWait, Tokens, TranscriptLine, Turn, TurnRole
from gulp_bot.service.ai_access_service import access_from_roles
from gulp_bot.service.ai_model_catalog_service import normalize_model_name
from gulp_bot.service.tldr_service import MAX_LINE_CHARS, TLDR_COOLDOWN, TldrCooldowns, conversation_to_summarize, describe_span, transcript

RAIDER, OFFICER, TRIAL, DEVELOPER = 10, 20, 30, 40


def access_of(roles):
    return access_from_roles(roles, RAIDER, OFFICER, DEVELOPER, False)


# --- access_from_roles ---


def test_an_administrator_is_an_officer_whatever_roles_they_hold():
    assert access_from_roles([], RAIDER, OFFICER, None, True) is Access.OFFICER
    assert access_from_roles([RAIDER], RAIDER, OFFICER, None, True) is Access.OFFICER
    assert access_from_roles([], None, None, None, True) is Access.OFFICER
    assert access_from_roles([], None, None, None, True).is_officer()


def test_the_more_trusted_have_the_higher_level():
    assert Access.DENIED.level() < Access.RAIDER.level() < Access.OFFICER.level() < Access.DEVELOPER.level()


def test_a_raider_is_allowed_but_is_not_an_officer():
    raider = access_of([1, RAIDER])
    assert raider is Access.RAIDER and raider.is_allowed() and not raider.is_officer()


def test_an_officer_is_allowed_with_or_without_the_raider_role():
    assert access_of([OFFICER]) is Access.OFFICER
    assert access_of([RAIDER, OFFICER]) is Access.OFFICER
    assert access_of([OFFICER]).is_officer()


def test_everybody_else_is_denied():
    assert access_of([]) is Access.DENIED
    assert access_of([1, 2, 3]) is Access.DENIED
    # A trial is not a raider.
    assert access_of([TRIAL]) is Access.DENIED
    assert not access_of([]).is_allowed() and not access_of([]).is_officer()


def test_roles_that_are_not_registered_grant_nothing():
    assert access_from_roles([RAIDER, OFFICER, DEVELOPER], None, None, None, False) is Access.DENIED
    assert access_from_roles([RAIDER], None, OFFICER, None, False) is Access.DENIED
    assert access_from_roles([OFFICER], RAIDER, None, None, False) is Access.DENIED
    assert access_from_roles([RAIDER], RAIDER, None, None, False) is Access.RAIDER


def test_a_developer_is_above_an_officer_and_still_an_officer():
    developer = access_of([DEVELOPER])
    assert developer is Access.DEVELOPER and developer.is_allowed() and developer.is_officer()
    assert access_of([RAIDER, OFFICER, DEVELOPER]) is Access.DEVELOPER
    assert access_from_roles([DEVELOPER], RAIDER, OFFICER, DEVELOPER, True) is Access.DEVELOPER


def test_nobody_else_is_a_developer():
    assert access_of([RAIDER]) is not Access.DEVELOPER
    assert access_of([RAIDER, OFFICER]) is not Access.DEVELOPER
    # Not even an administrator.
    assert access_from_roles([], RAIDER, OFFICER, DEVELOPER, True) is Access.OFFICER
    assert access_from_roles([], None, None, None, True) is not Access.DEVELOPER


def test_an_unregistered_developer_role_makes_nobody_a_developer():
    assert access_from_roles([DEVELOPER], RAIDER, OFFICER, None, False) is Access.DENIED
    assert access_from_roles([DEVELOPER, RAIDER], RAIDER, OFFICER, None, False) is Access.RAIDER


# --- history ---

HOUR = 3600.0
LEVEL_RAIDER, LEVEL_OFFICER, LEVEL_DEVELOPER = 1, 2, 3


def test_a_new_channel_remembers_nothing():
    assert ConversationHistoryRepository(6, HOUR).turns_visible_to(1, LEVEL_OFFICER, 0.0) == []


def test_exchanges_are_remembered_in_order_per_channel():
    history = ConversationHistoryRepository(6, HOUR)
    history.record_exchange(1, "q1", "a1", LEVEL_RAIDER, 0.0)
    history.record_exchange(1, "q2", "a2", LEVEL_RAIDER, 0.0)
    history.record_exchange(2, "other", "chat", LEVEL_RAIDER, 0.0)
    assert history.turns_visible_to(1, LEVEL_RAIDER, 0.0) == [Turn.user("q1"), Turn.assistant("a1"), Turn.user("q2"), Turn.assistant("a2")]
    assert history.turns_visible_to(2, LEVEL_RAIDER, 0.0) == [Turn.user("other"), Turn.assistant("chat")]


def test_what_an_officer_was_told_is_not_shown_to_a_raider():
    history = ConversationHistoryRepository(10, HOUR)
    history.record_exchange(1, "raider q", "raider a", LEVEL_RAIDER, 0.0)
    history.record_exchange(1, "officer q", "secret a", LEVEL_OFFICER, 0.0)
    history.record_exchange(1, "raider q2", "raider a2", LEVEL_RAIDER, 0.0)
    seen = history.turns_visible_to(1, LEVEL_RAIDER, 0.0)
    assert seen == [Turn.user("raider q"), Turn.assistant("raider a"), Turn.user("raider q2"), Turn.assistant("raider a2")]
    assert all("secret" not in turn.content for turn in seen)
    assert len(history.turns_visible_to(1, LEVEL_OFFICER, 0.0)) == 6


def test_what_a_developer_was_told_is_shown_to_nobody_below():
    history = ConversationHistoryRepository(10, HOUR)
    history.record_exchange(1, "dev q", "dev secret", LEVEL_DEVELOPER, 0.0)
    history.record_exchange(1, "officer q", "officer a", LEVEL_OFFICER, 0.0)
    assert history.turns_visible_to(1, LEVEL_RAIDER, 0.0) == []
    assert history.turns_visible_to(1, LEVEL_OFFICER, 0.0) == [Turn.user("officer q"), Turn.assistant("officer a")]
    assert len(history.turns_visible_to(1, LEVEL_DEVELOPER, 0.0)) == 4


def test_the_oldest_turns_are_dropped_and_the_user_still_speaks_first():
    history = ConversationHistoryRepository(4, HOUR)
    for n in range(1, 6):
        history.record_exchange(1, f"q{n}", f"a{n}", LEVEL_RAIDER, 0.0)
    assert history.turns_visible_to(1, LEVEL_RAIDER, 0.0) == [Turn.user("q4"), Turn.assistant("a4"), Turn.user("q5"), Turn.assistant("a5")]

    # An odd limit would cut an answer loose from its question; the orphan is dropped.
    history = ConversationHistoryRepository(3, HOUR)
    for n in range(1, 4):
        history.record_exchange(1, f"q{n}", f"a{n}", LEVEL_RAIDER, 0.0)
    turns = history.turns_visible_to(1, LEVEL_RAIDER, 0.0)
    assert turns[0].role is TurnRole.USER
    assert turns == [Turn.user("q3"), Turn.assistant("a3")]


def test_a_quiet_channel_is_forgotten():
    history = ConversationHistoryRepository(6, 60.0)
    history.record_exchange(1, "q", "a", LEVEL_RAIDER, 0.0)
    assert len(history.turns_visible_to(1, LEVEL_RAIDER, 59.0)) == 2
    assert history.turns_visible_to(1, LEVEL_RAIDER, 60.0) == []
    history.record_exchange(1, "new", "chat", LEVEL_RAIDER, 61.0)
    assert history.turns_visible_to(1, LEVEL_RAIDER, 61.0) == [Turn.user("new"), Turn.assistant("chat")]


def test_roles_are_named_as_the_api_names_them():
    assert TurnRole.USER.value == "user" and TurnRole.ASSISTANT.value == "assistant"


def test_a_user_is_limited_within_the_window_only():
    limiter = RateLimiter(2, 60.0)
    assert limiter.check(1, 0.0) is None
    assert limiter.check(1, 10.0) is None
    # The third is refused, and told when the first leaves the window.
    assert limiter.check(1, 20.0) == 40.0
    # A refused request doesn't count.
    assert limiter.check(1, 60.0) is None
    assert limiter.check(1, 61.0) == 9.0


def test_users_are_limited_separately():
    limiter = RateLimiter(1, 60.0)
    assert limiter.check(1, 0.0) is None
    assert limiter.check(1, 0.0) is not None
    assert limiter.check(2, 0.0) is None


def test_a_request_can_be_taken_back():
    limiter = RateLimiter(1, 60.0)
    assert limiter.check(1, 0.0) is None
    assert limiter.check(1, 0.0) is not None
    limiter.undo(1)
    assert limiter.check(1, 0.0) is None
    # Taking back what was never counted does nothing.
    limiter.undo(7)
    assert limiter.check(7, 0.0) is None


def test_idle_users_are_not_kept():
    limiter = RateLimiter(1, 60.0)
    for user in range(100):
        assert limiter.check(user, 0.0) is None
    assert limiter.check(1000, 120.0) is None
    assert len(limiter.requests) == 1


def test_a_users_requests_can_be_counted_and_cleared():
    limiter = RateLimiter(5, 60.0)
    assert limiter.max == 5
    assert limiter.count(1, 0.0) == 0
    limiter.check(1, 0.0)
    limiter.check(1, 30.0)
    limiter.check(2, 0.0)
    assert limiter.count(1, 30.0) == 2
    # The first has left the window by now, though nobody has asked since.
    assert limiter.count(1, 70.0) == 1
    assert limiter.count(2, 0.0) == 1
    assert limiter.count(3, 0.0) == 0
    assert limiter.clear(1, 30.0) == 2
    assert limiter.count(1, 30.0) == 0
    assert limiter.count(2, 0.0) == 1
    assert limiter.clear(1, 30.0) == 0


def test_a_user_who_was_cleared_may_ask_again_at_once():
    limiter = RateLimiter(1, 60.0)
    assert limiter.check(1, 0.0) is None
    assert limiter.check(1, 0.0) is not None
    limiter.clear(1, 0.0)
    assert limiter.check(1, 0.0) is None


def test_the_last_answers_are_remembered():
    answers = AnswerMessageRepository(2)
    assert not answers.contains(1)
    answers.remember(1)
    answers.remember(2)
    answers.remember(2)
    assert answers.contains(1) and answers.contains(2)
    answers.remember(3)
    assert not answers.contains(1) and answers.contains(2) and answers.contains(3)


# --- usage ---

DAY = 24 * 3600.0


def test_usage_is_counted_per_model_since_the_start():
    usage = TokenUsageRepository(DAY)
    usage.record("b", 100, 10, 0.0)
    usage.record("a", 50, 5, 0.0)
    usage.record("b", 100, 10, 0.0)
    assert usage.since_start() == [("a", Tokens(1, 50, 5)), ("b", Tokens(2, 200, 20))]


def test_the_recent_usage_only_counts_the_window():
    usage = TokenUsageRepository(100.0)
    usage.record("m", 10, 1, 0.0)
    usage.record("m", 20, 2, 60.0)
    assert usage.recent(120.0) == [("m", Tokens(1, 20, 2))]
    usage.record("m", 30, 3, 120.0)
    assert usage.recent(120.0) == [("m", Tokens(2, 50, 5))]
    assert usage.since_start() == [("m", Tokens(3, 60, 6))]
    assert usage.recent(1000.0) == []


def test_usage_is_described_per_model_with_a_total():
    assert usage_lines([]) == "No requests."
    assert usage_lines([("qwen3.5:9b", Tokens(2, 1_500, 300))]) == "`qwen3.5:9b`: 2 requests, 1,500 in / 300 out tokens"
    text = usage_lines([("qwen3.5:9b", Tokens(1, 1_000_000, 0)), ("gemma4:12b", Tokens(1, 10, 10))])
    assert "`gemma4:12b`: 1 request, 10 in / 10 out tokens" in text
    assert text.endswith("**Total**: 2 requests, 1,000,010 in / 10 out tokens")


def test_numbers_are_formatted_for_reading():
    assert format_count(0) == "0"
    assert format_count(999) == "999"
    assert format_count(1_000) == "1,000"
    assert format_count(1_234_567) == "1,234,567"


def test_a_model_name_is_checked_for_its_shape():
    assert normalize_model_name(" gemma4:12b ") == "gemma4:12b"
    assert normalize_model_name("qwen3.5:9b") == "qwen3.5:9b"
    assert normalize_model_name("hf.co/org/model:Q4_K_M") == "hf.co/org/model:Q4_K_M"
    assert normalize_model_name("Default") is None
    for bad in ["", "   ", "two words", "model\nname", "`code`", "m" * 65]:
        with pytest.raises(ModelNameProblem):
            normalize_model_name(bad)
    assert normalize_model_name("m" * 64) == "m" * 64


def test_the_last_failures_are_kept_latest_first():
    failures = ApiFailureRepository(2)
    assert failures.latest() == [] and failures.total == 0
    failures.record(1, "first")
    failures.record(2, "second")
    failures.record(3, "third")
    assert failures.total == 3
    assert failures.latest() == [ApiFailure(3, "third"), ApiFailure(2, "second")]


def test_a_failure_is_kept_on_one_short_line():
    failures = ApiFailureRepository(5)
    failures.record(1, "the API returned 500:\nline two")
    failures.record(2, "x" * 500)
    latest = failures.latest()
    assert latest[1].detail == "the API returned 500: line two"
    assert len(latest[0].detail) == MAX_FAILURE_DETAIL_CHARS


# --- /tldr ---

NOW = 1_000_000
MINUTE = 60


def line(minutes_ago, text):
    return TranscriptLine(NOW - minutes_ago * MINUTE, f"-{minutes_ago}m", "Kail", text)


def texts(lines):
    return [entry.text for entry in lines]


def test_the_conversation_is_the_last_four_hours_oldest_first():
    newest_first = [line(1, "c"), line(30, "b"), line(200, "a"), line(250, "too old")]
    close = [line(1, "c"), line(50, "b"), line(100, "a2"), line(150, "a1"), line(200, "a0"), line(241, "too old")]
    assert texts(conversation_to_summarize(close, NOW)) == ["a0", "a1", "a2", "b", "c"]
    assert texts(conversation_to_summarize(newest_first, NOW)) == ["b", "c"]


def test_a_pause_of_an_hour_ends_the_conversation():
    assert texts(conversation_to_summarize([line(5, "now"), line(30, "earlier"), line(91, "before the break"), line(95, "older")], NOW)) == ["earlier", "now"]
    assert texts(conversation_to_summarize([line(5, "now"), line(64, "still the same chat")], NOW)) == ["still the same chat", "now"]
    assert texts(conversation_to_summarize([line(5, "now"), line(65, "another chat")], NOW)) == ["now"]


def test_a_quiet_channel_has_no_conversation():
    assert conversation_to_summarize([], NOW) == []
    assert conversation_to_summarize([line(300, "long ago")], NOW) == []
    assert texts(conversation_to_summarize([line(90, "a"), line(100, "b")], NOW)) == ["b", "a"]


def test_the_transcript_has_a_line_per_message():
    lines = [TranscriptLine(1, "20:01", "Kail", "who pulled?"), TranscriptLine(2, "20:02", "Sonodruud", "not me\nI swear")]
    assert transcript(lines, 10_000) == ("[20:01] Kail: who pulled?\n[20:02] Sonodruud: not me I swear", 0)


def test_a_long_message_is_cut_and_the_oldest_lines_go_first():
    text, _ = transcript([TranscriptLine(1, "20:00", "Kail", "x" * 5000)], 10_000)
    assert text.count("x") == MAX_LINE_CHARS
    lines = [TranscriptLine(n, "20:00", "K", f"m{n}") for n in range(10)]
    assert transcript(lines, 14 * 3) == ("[20:00] K: m7\n[20:00] K: m8\n[20:00] K: m9", 7)
    assert transcript(lines, 5) == ("", 10)


def test_a_span_is_described_in_hours_and_minutes():
    assert describe_span(0) == "1m"
    assert describe_span(30) == "1m"
    assert describe_span(45 * 60) == "45m"
    assert describe_span(2 * 3600) == "2h"
    assert describe_span(3 * 3600 + 20 * 60) == "3h 20m"
    assert describe_span(-5) == "1m"


def test_a_channel_waits_an_hour_and_is_pointed_at_the_last_summary():
    cooldowns = TldrCooldowns(TLDR_COOLDOWN)
    assert cooldowns.start(1, 0.0) == TldrReady()
    assert cooldowns.start(1, 0.0) == TldrBusy()
    assert cooldowns.start(2, 0.0) == TldrReady()
    cooldowns.finish(1, "https://discord.com/channels/9/1/77", 500, 0.0)
    assert cooldowns.start(1, 20 * 60.0) == TldrWait("https://discord.com/channels/9/1/77", 500, 40 * 60.0)
    assert cooldowns.start(1, TLDR_COOLDOWN) == TldrReady()


def test_a_summary_that_failed_does_not_hold_the_channel():
    cooldowns = TldrCooldowns(TLDR_COOLDOWN)
    assert cooldowns.start(1, 0.0) == TldrReady()
    cooldowns.cancel(1)
    assert cooldowns.start(1, 0.0) == TldrReady()
    cooldowns.finish(1, "link", 1, 0.0)
    cooldowns.cancel(1)
    assert isinstance(cooldowns.start(1, 0.0), TldrWait)
