"""Adds every command resource to the bot, in the order `/help` lists them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import (
    absence_resource,
    ai_admin_resource,
    announcement_resource,
    debug_resource,
    settings_resource,
    tldr_resource,
    utility_resource,
    warcraft_logs_resource,
    wow_guild_resource,
)

if TYPE_CHECKING:
    from ..bot import GulpBot

# Commands used with the prefix as well as a slash: adding one adds its slash form to the command tree too.
PREFIX_AND_SLASH_COMMANDS = [
    *wow_guild_resource.COMMANDS,
    *utility_resource.COMMANDS,
    *debug_resource.COMMANDS,
    *settings_resource.COMMANDS,
]

# Commands that exist only as slash commands.
SLASH_COMMANDS = [
    *announcement_resource.COMMANDS,
    *absence_resource.COMMANDS,
    *warcraft_logs_resource.COMMANDS,
    *ai_admin_resource.COMMANDS,
    *tldr_resource.COMMANDS,
]


def register_all_resources(bot: GulpBot) -> None:
    for command in PREFIX_AND_SLASH_COMMANDS:
        bot.add_command(command)
    for app_command in SLASH_COMMANDS:
        bot.tree.add_command(app_command)
