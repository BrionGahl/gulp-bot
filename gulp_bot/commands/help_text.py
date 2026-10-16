"""`/help`: the list of commands, and one command's page. Works on a plain description of the commands,
so that it can be tested without Discord; `utilities.help_entries` makes one from the bot's commands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

# Shown under the command list. Says who can use what, which the generated list doesn't.
HELP_FOOTER = (
    "Use /help <command> for details and subcommands, e.g. /help settings.\n"
    "Settings and Warcraft Logs: administrators and moderators. Announcements and Debug: moderators.\n"
    "get_timeline_reminders and submit_droptimizer: raiders, trials and moderators."
)


@dataclass(frozen=True)
class Parameter:
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
    parameters: list[Parameter] = field(default_factory=list)
    subcommands: list[HelpEntry] = field(default_factory=list)


def overview(commands: Sequence[HelpEntry], prefix: str = "/") -> str:
    """The list of top-level commands by category, in the order the categories first appear, in a code
    block, with `HELP_FOOTER` under it."""
    categories: dict[str | None, list[HelpEntry]] = {}
    for command in commands:
        categories.setdefault(command.category, []).append(command)

    menu = "```\n"
    for category, members in categories.items():
        listed = [command for command in members if not command.hidden]
        if not listed:
            continue
        menu += f"{category or 'Commands'}:\n"
        for command in listed:
            padding = max(12 - (len(prefix) + len(command.name)), 0) + 1
            menu += f"  {prefix}{command.name}{' ' * padding}{command.description}\n"
    menu += f"\n{HELP_FOOTER}\n```"
    return menu


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


def render_command_help(path: str, command: HelpEntry) -> str:
    """The body of one command's help page: its description and longer help text, then its parameters
    and subcommands."""
    text = command.description or "No description."
    if command.help_text:
        text += f"\n\n{command.help_text}"

    if command.parameters:
        text += "\n\n**Parameters**"
        for parameter in command.parameters:
            required = "required" if parameter.required else "optional"
            text += f"\n`{parameter.name}` ({required}) — {parameter.description}"

    if command.subcommands:
        text += "\n\n**Subcommands**"
        for subcommand in command.subcommands:
            text += f"\n`/{path} {subcommand.name}` — {subcommand.description}"

    return text


def with_paths(commands: Sequence[HelpEntry], prefix: str = "") -> list[tuple[str, HelpEntry]]:
    """Every command with its full name, at every depth: `settings`, `settings wcl`, `settings wcl source`."""
    found: list[tuple[str, HelpEntry]] = []
    for command in commands:
        path = command.name if not prefix else f"{prefix} {command.name}"
        found.extend(with_paths(command.subcommands, path))
        found.append((path, command))
    return found
