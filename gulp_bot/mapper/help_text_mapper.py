"""`/help`'s text: the list of commands, and one command's page."""

from __future__ import annotations

from collections.abc import Sequence

from ..schema.help_schema import HelpEntry

# Shown under the command list. Says who can use what, which the generated list doesn't.
HELP_FOOTER = (
    "Use /help <command> for details and subcommands, e.g. /help settings.\n"
    "Settings and Warcraft Logs: administrators and moderators. Announcements and Debug: moderators.\n"
    "get_timeline_reminders and submit_droptimizer: raiders, trials and moderators."
)


def help_overview(commands: Sequence[HelpEntry], prefix: str = "/") -> str:
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


def command_help_page(path: str, command: HelpEntry) -> str:
    """The body of one command's help page: its description and longer help text, then its parameters and
    subcommands."""
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
