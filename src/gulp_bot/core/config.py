"""Process-wide configuration from the environment.

Per-server values (role, channel and user IDs, the Bart / WowUtils / Warcraft Logs credentials, the
raid notes schedule) are not here: administrators register those per guild, see `service/guild_settings_service.py`.
"""

from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import DEFAULT_SYSTEM_PROMPT_PATH


class AiBackend(enum.Enum):
    """Which model answers the AI assistant's questions."""

    # Claude, through the Anthropic API. Needs `ANTHROPIC_API_KEY`.
    ANTHROPIC = "anthropic"
    # A model on the guild's own hardware, served by Ollama. Nothing leaves the machine.
    LOCAL = "local"


# `LOG_LEVEL` values, as the Rust bot accepted them, and the `logging` level each one means.
# Python has no TRACE, so it is DEBUG; OFF silences everything.
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
    # Anthropic API key for the AI assistant (`/ask`, "Bot please"). Without it the assistant is off.
    anthropic_api_key: str | None = field(repr=False)
    # The Claude model that answers. Default: `claude-sonnet-5-5`.
    anthropic_model: str
    # How much effort the model puts into an answer (`output_config.effort`): `low`, `medium`,
    # `high`... Default `low`; `off` doesn't send the setting at all (`None`).
    anthropic_effort: str | None
    # The most questions the assistant answers in 24 hours, from everybody together: what keeps the
    # bill from running away (or, with a local model, the GPU from being kept busy). Default 100.
    anthropic_daily_limit: int
    # Which model answers: Claude through the Anthropic API, or a model on the guild's own hardware
    # through Ollama (`AI_BACKEND=local`). Default: Anthropic.
    ai_backend: AiBackend
    # Where the Ollama server listens, for `AI_BACKEND=local`. Default: `http://ollama:11434`.
    local_model_url: str
    # The model Ollama runs, for `AI_BACKEND=local`. Default: `qwen3.5:9b`.
    local_model: str
    # Whether the local model "thinks" before it answers (`think` in Ollama's API). Default `False`:
    # thinking makes chat answers slower without making them better. `None` sends nothing and leaves
    # it to the model (`LOCAL_MODEL_THINK=default`), for a model that rejects the setting.
    local_model_think: bool | None
    # The file with what the AI assistant knows about the guild. Default: `prompts/system_prompt.md` at
    # the root of the repository (`/app/prompts/system_prompt.md` in the image).
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

        effort = optional("ANTHROPIC_EFFORT")
        if effort is None:
            anthropic_effort: str | None = "low"
        elif effort.lower() == "off":
            anthropic_effort = None
        else:
            anthropic_effort = effort.lower()

        daily_limit = optional("ANTHROPIC_DAILY_LIMIT")
        if daily_limit is None:
            anthropic_daily_limit = 100
        elif daily_limit.isascii() and daily_limit.isdigit():
            anthropic_daily_limit = int(daily_limit)
        else:
            raise ConfigError("Failed to parse `ANTHROPIC_DAILY_LIMIT` env variable. It must be a whole number.")

        return cls(
            discord_token=required("DISCORD_TOKEN"),
            bot_name=env.get("BOT_NAME", "gulp-bot"),
            database_url=required("DATABASE_URL"),
            log_level=LOG_LEVELS[log_level_name.strip().lower()],
            anthropic_api_key=optional("ANTHROPIC_API_KEY"),
            anthropic_model=optional("ANTHROPIC_MODEL") or "claude-sonnet-5-5",
            anthropic_effort=anthropic_effort,
            anthropic_daily_limit=anthropic_daily_limit,
            ai_backend=parse_backend(optional("AI_BACKEND")),
            local_model_url=optional("LOCAL_MODEL_URL") or "http://ollama:11434",
            local_model=optional("LOCAL_MODEL") or "qwen3.5:9b",
            local_model_think=parse_think(optional("LOCAL_MODEL_THINK")),
            system_prompt_path=Path(optional("SYSTEM_PROMPT_PATH") or DEFAULT_SYSTEM_PROMPT_PATH),
        )


def parse_backend(value: str | None) -> AiBackend:
    """`AI_BACKEND`: `anthropic` (or unset) or `local` (`ollama` works too), in any case."""
    match None if value is None else value.lower():
        case None | "anthropic":
            return AiBackend.ANTHROPIC
        case "local" | "ollama":
            return AiBackend.LOCAL
        case other:
            raise ConfigError(f"Failed to parse `AI_BACKEND` env variable `{other}`. Valid values: anthropic, local")


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
