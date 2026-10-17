"""The bot's slash commands into `HelpEntry`s. Every command has a slash form, so the slash commands are all
of them."""

from __future__ import annotations

from typing import Any

from discord import app_commands

from ..schema.help_schema import HelpEntry, HelpParameter

# What discord.py puts in place of a missing description.
_NO_DESCRIPTION = "…"


def _text(description: str) -> str:
    return "" if description == _NO_DESCRIPTION else description


def help_entry(command: app_commands.Command[Any, ..., Any] | app_commands.Group) -> HelpEntry:
    extras = command.extras
    common = {
        "name": command.name,
        "description": _text(command.description),
        "help_text": extras.get("help_text"),
        "category": extras.get("category"),
        "hidden": bool(extras.get("hidden", False)),
    }
    if isinstance(command, app_commands.Group):
        return HelpEntry(**common, subcommands=[help_entry(child) for child in command.commands])
    return HelpEntry(**common, parameters=[HelpParameter(parameter.name, parameter.required, _text(parameter.description)) for parameter in command.parameters])


def help_entries(tree: app_commands.CommandTree[Any]) -> list[HelpEntry]:
    """The bot's commands as `/help` sees them, in the order they were added."""
    return [help_entry(command) for command in tree.get_commands() if isinstance(command, (app_commands.Command, app_commands.Group))]
