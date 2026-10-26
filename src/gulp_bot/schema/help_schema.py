"""A command as `/help` describes it."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HelpParameter:
    name: str
    required: bool
    description: str


@dataclass(frozen=True)
class HelpEntry:
    """A command as `/help` sees it."""

    name: str
    description: str
    help_text: str | None = None
    category: str | None = None
    hidden: bool = False
    parameters: list[HelpParameter] = field(default_factory=list)
    subcommands: list[HelpEntry] = field(default_factory=list)
