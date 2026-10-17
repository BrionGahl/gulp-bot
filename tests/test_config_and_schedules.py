from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from gulp_bot.core.config import AiBackend, Config, ConfigError, parse_backend, parse_think
from gulp_bot.core.cron import CronError, Schedule
from gulp_bot.core.paths import DEFAULT_SYSTEM_PROMPT_PATH, MIGRATIONS_DIR
from gulp_bot.core.schedule_parsing import EXAMPLE_CRON, parse_user_schedule
from gulp_bot.exception.schedule_exception import ScheduleProblem

NEW_YORK = ZoneInfo("America/New_York")


def env(**extra):
    return {"DISCORD_TOKEN": "discord-secret-token", "DATABASE_URL": "postgres://gulp:db-secret-password@db:5432/gulp", **extra}


def test_repr_never_holds_a_secret():
    config = Config.from_env(env(ANTHROPIC_API_KEY="anthropic-secret-key", AI_BACKEND="local"))
    shown = repr(config)
    assert "gulp-bot" in shown and "claude-sonnet-5-5" in shown and "qwen3.5:9b" in shown
    for secret in ["discord-secret-token", "db-secret-password", "anthropic-secret-key"]:
        assert secret not in shown


def test_the_defaults_are_the_rust_bots():
    config = Config.from_env(env())
    assert config.bot_name == "gulp-bot"
    assert config.anthropic_model == "claude-sonnet-5-5"
    assert config.anthropic_effort == "low"
    assert config.anthropic_daily_limit == 100
    assert config.ai_backend is AiBackend.ANTHROPIC
    assert config.local_model_url == "http://ollama:11434"
    assert config.local_model == "qwen3.5:9b"
    assert config.local_model_think is False
    assert config.anthropic_api_key is None
    assert config.system_prompt_path == DEFAULT_SYSTEM_PROMPT_PATH


def test_the_prompt_and_the_migrations_are_found():
    root = Path(__file__).resolve().parent.parent
    assert DEFAULT_SYSTEM_PROMPT_PATH == root / "src" / "prompts" / "system_prompt.md" and DEFAULT_SYSTEM_PROMPT_PATH.is_file()
    assert MIGRATIONS_DIR == root / "migrations" and MIGRATIONS_DIR.is_dir()
    assert Config.from_env(env(SYSTEM_PROMPT_PATH="/etc/gulp/prompt.md")).system_prompt_path == Path("/etc/gulp/prompt.md")
    assert Config.from_env(env(SYSTEM_PROMPT_PATH="")).system_prompt_path == DEFAULT_SYSTEM_PROMPT_PATH


def test_empty_variables_mean_unset_and_off_sends_no_effort():
    config = Config.from_env(env(ANTHROPIC_API_KEY="", ANTHROPIC_MODEL=" ", ANTHROPIC_EFFORT="OFF", ANTHROPIC_DAILY_LIMIT="7"))
    assert config.anthropic_api_key is None
    assert config.anthropic_model == "claude-sonnet-5-5"
    assert config.anthropic_effort is None
    assert config.anthropic_daily_limit == 7
    assert Config.from_env(env(ANTHROPIC_EFFORT="High")).anthropic_effort == "high"


def test_bad_variables_are_refused():
    with pytest.raises(ConfigError):
        Config.from_env({"DATABASE_URL": "x"})
    with pytest.raises(ConfigError):
        Config.from_env(env(LOG_LEVEL="loud"))
    with pytest.raises(ConfigError):
        Config.from_env(env(ANTHROPIC_DAILY_LIMIT="lots"))


def test_the_backend_is_anthropic_unless_set_to_local():
    assert parse_backend(None) is AiBackend.ANTHROPIC
    assert parse_backend("anthropic") is AiBackend.ANTHROPIC
    assert parse_backend("Local") is AiBackend.LOCAL
    assert parse_backend("ollama") is AiBackend.LOCAL
    with pytest.raises(ConfigError):
        parse_backend("openai")


def test_the_local_model_does_not_think_unless_asked_to():
    assert parse_think(None) is False
    assert parse_think("off") is False
    assert parse_think("TRUE") is True
    assert parse_think("default") is None
    with pytest.raises(ConfigError):
        parse_think("maybe")


def test_parse_schedule_accepts_weekly_and_rejects_unusable_expressions():
    parse_user_schedule(EXAMPLE_CRON)
    parse_user_schedule("0 30 17 * * Tue,Fri *")
    # Hourly is exactly at the floor, so allowed.
    parse_user_schedule("0 0 * * * * *")

    for expression, problem in [
        ("every friday", "cron expression"),
        ("* * * * * * *", "too often"),
        ("0 0,30 9 * * Fri *", "too often"),
        ("0 0 9 * * Fri 2020", "never fires"),
    ]:
        with pytest.raises(ScheduleProblem, match=problem):
            parse_user_schedule(expression)


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


def test_the_cron_syntax_of_the_rust_bot_is_read():
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
