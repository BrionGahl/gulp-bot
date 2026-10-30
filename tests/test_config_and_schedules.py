from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from gulp_bot.core.config import Config, ConfigError, parse_think
from gulp_bot.core.cron import CronError, Schedule
from gulp_bot.core.paths import MIGRATIONS_DIR
from gulp_bot.core.schedule_parsing import describe_schedule, parse_weekly_schedule
from gulp_bot.exception.schedule_exception import ScheduleProblem

NEW_YORK = ZoneInfo("America/New_York")


def env(**extra):
    return {"DISCORD_TOKEN": "discord-secret-token", "DATABASE_URL": "postgres://gulp:db-secret-password@db:5432/gulp", **extra}


def test_repr_never_holds_a_secret():
    config = Config.from_env(env())
    shown = repr(config)
    assert "gulp-bot" in shown and "qwen3.5:9b" in shown
    for secret in ["discord-secret-token", "db-secret-password"]:
        assert secret not in shown


def test_the_defaults():
    config = Config.from_env(env())
    assert config.bot_name == "gulp-bot"
    assert config.ai_daily_limit == 100
    assert config.local_model_url == "http://ollama:11434"
    assert config.local_model == "qwen3.5:9b"
    assert config.local_model_think is False


def test_the_migrations_are_at_the_root_of_the_repository():
    root = Path(__file__).resolve().parent.parent
    assert root / "migrations" == MIGRATIONS_DIR and MIGRATIONS_DIR.is_dir()


def test_empty_variables_mean_unset():
    config = Config.from_env(env(LOCAL_MODEL=" ", LOCAL_MODEL_URL="", AI_DAILY_LIMIT="7"))
    assert config.local_model == "qwen3.5:9b"
    assert config.local_model_url == "http://ollama:11434"
    assert config.ai_daily_limit == 7


def test_bad_variables_are_refused():
    with pytest.raises(ConfigError):
        Config.from_env({"DATABASE_URL": "x"})
    with pytest.raises(ConfigError):
        Config.from_env(env(LOG_LEVEL="loud"))
    with pytest.raises(ConfigError):
        Config.from_env(env(AI_DAILY_LIMIT="lots"))


def test_the_local_model_does_not_think_unless_asked_to():
    assert parse_think(None) is False
    assert parse_think("off") is False
    assert parse_think("TRUE") is True
    assert parse_think("default") is None
    with pytest.raises(ConfigError):
        parse_think("maybe")


def test_a_schedule_is_typed_as_days_and_a_time():
    assert parse_weekly_schedule("Tue, Fri", "5:30pm").source == "0 30 17 * * Tue,Fri *"
    assert parse_weekly_schedule("friday", "09:00").after(at(2026, 10, 14)) == at(2026, 10, 16, 9)
    for days, time, problem in [("someday", "9am", "day of the week"), ("Fri", "noonish", "isn't a time"), ("", "9am", "at least one day")]:
        with pytest.raises(ScheduleProblem, match=problem):
            parse_weekly_schedule(days, time)


def test_a_schedule_is_described_as_days_and_a_time_when_it_is_one():
    assert describe_schedule(parse_weekly_schedule("Fri, Tue", "9am")) == "Tue, Fri, 9:00 AM Eastern"
    assert describe_schedule(parse_weekly_schedule("mon tue wed thu fri sat sun", "12am")) == "Every day, 12:00 AM Eastern"
    # A cron expression stored before days and times were asked for is shown as it is.
    for expression in ["0 0 9 1 * * *", "0 0,30 9 * * Fri *", "0 0 9 * * Fri 2030"]:
        assert describe_schedule(Schedule.parse(expression)) == f"`{expression}`"


def at(*parts):
    return datetime(*parts, tzinfo=NEW_YORK)


def test_fire_times_are_on_the_wall_clock_of_new_york():
    fridays = Schedule.parse("0 0 9 * * Fri *")
    # Thursday Oct 15 2026, noon: the next is Friday 9:00.
    assert fridays.after(at(2026, 10, 15, 12)) == at(2026, 10, 16, 9)
    # Exactly at a fire time: the next one, a week later.
    assert fridays.after(at(2026, 10, 16, 9)) == at(2026, 10, 23, 9)
    # Across the switch from EDT to EST (Nov 1 2026), still 9:00 local.
    after_switch = fridays.after(at(2026, 10, 31))
    assert after_switch == at(2026, 11, 6, 9)
    assert after_switch is not None and after_switch.timestamp() - at(2026, 11, 6).timestamp() == 9 * 3600
    assert str(after_switch.utcoffset()) == "-1 day, 19:00:00"


def test_the_stored_cron_syntax_is_read():
    # Days of the week count Sunday as 1.
    assert Schedule.parse("0 0 12 * * 1 *").after(at(2026, 10, 14)) == at(2026, 10, 18, 12)
    # Names, in any case, short or full, ranges and lists.
    assert Schedule.parse("0 0 20 * * tue,THU *").days_of_week == {3, 5}
    assert Schedule.parse("0 0 20 * * Mon-Fri").days_of_week == {2, 3, 4, 5, 6}
    assert Schedule.parse("0 0 20 * * Monday").days_of_week == {2}
    assert Schedule.parse("0 0 0 1 Jan-Mar *").months == {1, 2, 3}
    # Steps, from `*`, a value or a range; `?` is every value.
    assert Schedule.parse("0 */15 * * * ?").minutes == {0, 15, 30, 45}
    assert Schedule.parse("5/20 0 0 * * *").seconds == {5, 25, 45}
    assert Schedule.parse("0 0 1-10/3 * * *").hours == {1, 4, 7, 10}
    # Shorthands.
    assert Schedule.parse("@daily").after(at(2026, 10, 14, 12)) == at(2026, 10, 15)
    assert Schedule.parse("@weekly").after(at(2026, 10, 14)) == at(2026, 10, 18)
    # The source is kept as given.
    assert Schedule.parse("0 0 9 * * Fri *").source == "0 0 9 * * Fri *"


def test_a_day_needs_both_its_date_and_weekday():
    # The 13th that is a Friday: Nov 13 2026.
    assert Schedule.parse("0 0 0 13 * Fri *").after(at(2026, 10, 14)) == at(2026, 11, 13)


def test_bad_expressions_say_why():
    for expression in [
        "",
        "0 0 9 * *",
        "0 0 9 * * Fri * extra",
        "60 0 0 * * *",
        "0 0 24 * * *",
        "0 0 0 0 * *",
        "0 0 0 * 13 *",
        "0 0 0 * * 8",
        "0 0 5-1 * * *",
        "0 */0 * * * *",
        "0 0 0 * * Funday",
    ]:
        with pytest.raises(CronError):
            Schedule.parse(expression)


def test_a_time_the_clock_skips_never_fires():
    # 2:30 AM doesn't exist on Mar 8 2026 in New York.
    fire = Schedule.parse("0 30 2 * * *").after(at(2026, 3, 8, 0))
    assert fire == at(2026, 3, 9, 2, 30)
    assert fire is not None and fire.date() == date(2026, 3, 9)
