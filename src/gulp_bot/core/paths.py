"""Where the files the bot reads, but that aren't code, are, relative to the root of the repository (and
of the image). This file is `src/gulp_bot/core/paths.py`."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The database migrations, applied at startup: `migrations/`, next to `src/`.
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

# What the AI assistant knows about the guild, unless `SYSTEM_PROMPT_PATH` points somewhere else:
# `src/prompts/`, next to the `gulp_bot` package rather than in it.
DEFAULT_SYSTEM_PROMPT_PATH = PROJECT_ROOT / "src" / "prompts" / "system_prompt.md"
