"""Where the files the bot reads, but that aren't code, are: at the root of the repository (and of the
image), next to `src/`. This file is `src/gulp_bot/core/paths.py`."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The database migrations, applied at startup.
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

# What the AI assistant knows about the guild, unless `SYSTEM_PROMPT_PATH` points somewhere else.
DEFAULT_SYSTEM_PROMPT_PATH = PROJECT_ROOT / "ext_resources" / "system_prompt.md"
