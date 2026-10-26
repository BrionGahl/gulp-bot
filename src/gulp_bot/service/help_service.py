"""`/help`: finding a command by the name a member typed. No Discord in here; commands are `HelpEntry`s."""

from __future__ import annotations

from collections.abc import Sequence

from ..schema.help_schema import HelpEntry


def find_command(commands: Sequence[HelpEntry], query: str) -> tuple[str, HelpEntry] | None:
    """Resolves a query like `settings raid_voice` (a leading `/` or `?` is ignored) to that command,
    descending into subcommands. Returns the command's full name alongside it."""
    candidates: Sequence[HelpEntry] = commands
    found: HelpEntry | None = None
    path: list[str] = []
    for word in query.strip().lstrip("/?").split():
        match = next((command for command in candidates if command.name.lower() == word.lower() and not command.hidden), None)
        if match is None:
            return None
        path.append(match.name)
        candidates = match.subcommands
        found = match
    return None if found is None else (" ".join(path), found)


def with_paths(commands: Sequence[HelpEntry], prefix: str = "") -> list[tuple[str, HelpEntry]]:
    """Every command with its full name, at every depth: `settings`, `settings wcl`, `settings wcl source`."""
    found: list[tuple[str, HelpEntry]] = []
    for command in commands:
        path = command.name if not prefix else f"{prefix} {command.name}"
        found.extend(with_paths(command.subcommands, path))
        found.append((path, command))
    return found
