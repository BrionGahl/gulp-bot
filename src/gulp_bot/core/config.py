"""Process-wide configuration from the environment.

Per-server values (role, channel and user IDs, the Bart / WowUtils / Warcraft Logs credentials, the
raid notes schedule) are not here: administrators register those per guild, see `service/guild_settings_service.py`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import DEFAULT_SYSTEM_PROMPT_PATH

# The `LOG_LEVEL` values the bot accepts, and the `logging` level each one means. Python has no TRACE, so
# it is DEBUG; OFF silences everything.
LOG_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "off": logging.CRITICAL + 10,
}


class ConfigError(Exception):
    """An environment variable is missing or can't be read. The message says which."""


@dataclass(frozen=True)
class Config:
    discord_token: str = field(repr=False)
    bot_name: str
    # Postgres connection URL for the database holding per-guild settings. Holds the password.
    database_url: str = field(repr=False)
    log_level: int
    # The most questions the AI assistant (`/ask`, "Bot please") answers in 24 hours, from everybody
    # together: what keeps the GPU from being kept busy. Default 100.
    ai_daily_limit: int
    # Where the Ollama server that runs the assistant's model listens. Default: `http://ollama:11434`.
    local_model_url: str
    # The model Ollama runs. Default: `qwen3.5:9b`.
    local_model: str
    # Whether the local model "thinks" before it answers (`think` in Ollama's API). Default `False`:
    # thinking makes chat answers slower without making them better. `None` sends nothing and leaves
    # it to the model (`LOCAL_MODEL_THINK=default`), for a model that rejects the setting.
    local_model_think: bool | None
    # The file with what the AI assistant knows about the guild. Default:
    # `ext_resources/system_prompt.md` at the root of the repository (`/app/ext_resources/system_prompt.md`
    # in the image).
    system_prompt_path: Path = DEFAULT_SYSTEM_PROMPT_PATH

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = dict(os.environ) if env is None else env

        def required(name: str) -> str:
            value = env.get(name)
            if value is None:
                raise ConfigError(f"Missing `{name}` env variable.")
            return value

        def optional(name: str) -> str | None:
            # Missing or empty (docker compose passes unset ones on as empty): either means "not set".
            value = env.get(name, "").strip()
            return value or None

        log_level_name = env.get("LOG_LEVEL", "INFO")
        if log_level_name.strip().lower() not in LOG_LEVELS:
            raise ConfigError("Failed to parse `LOG_LEVEL` env variable. Valid values: TRACE, DEBUG, INFO, WARN, ERROR")

        daily_limit = optional("AI_DAILY_LIMIT")
        if daily_limit is None:
            ai_daily_limit = 100
        elif daily_limit.isascii() and daily_limit.isdigit():
            ai_daily_limit = int(daily_limit)
        else:
            raise ConfigError("Failed to parse `AI_DAILY_LIMIT` env variable. It must be a whole number.")

        return cls(
            discord_token=required("DISCORD_TOKEN"),
            bot_name=env.get("BOT_NAME", "gulp-bot"),
            database_url=required("DATABASE_URL"),
            log_level=LOG_LEVELS[log_level_name.strip().lower()],
            ai_daily_limit=ai_daily_limit,
            local_model_url=optional("LOCAL_MODEL_URL") or "http://ollama:11434",
            local_model=optional("LOCAL_MODEL") or "qwen3.5:9b",
            local_model_think=parse_think(optional("LOCAL_MODEL_THINK")),
            system_prompt_path=Path(optional("SYSTEM_PROMPT_PATH") or DEFAULT_SYSTEM_PROMPT_PATH),
        )


def parse_think(value: str | None) -> bool | None:
    """`LOCAL_MODEL_THINK`: `false` (or unset), `true`, or `default` to send nothing."""
    match None if value is None else value.lower():
        case None | "false" | "off":
            return False
        case "true" | "on":
            return True
        case "default":
            return None
        case other:
            raise ConfigError(f"Failed to parse `LOCAL_MODEL_THINK` env variable `{other}`. Valid values: false, true, default")
