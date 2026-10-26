"""What the command resources share: their help metadata, and the ways they answer."""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..core.discord_context import GulpContext
from ..core.discord_helpers import NO_MENTIONS
from ..core.text import split_message


def meta(command: Any, *, category: str | None = None, help_text: str | None = None, hidden: bool = False, ephemeral: bool = False) -> None:
    """Records what `/help` shows about `command` beyond its description (its category and longer help
    text), whether `/help` hides it, and whether it answers only the member who used it. Works for a
    prefix-and-slash command (on both of its halves) and for a slash-only one."""
    extras: dict[str, Any] = {"ephemeral": ephemeral, "hidden": hidden}
    if category is not None:
        extras["category"] = category
    if help_text is not None:
        extras["help_text"] = help_text
    command.extras.update(extras)
    app_command = getattr(command, "app_command", None)
    if app_command is not None and app_command is not discord.utils.MISSING:
        app_command.extras.update(extras)


def require_guild(ctx: GulpContext, message: str = "Settings can only be changed in a server.") -> int:
    """The guild the command was used in. Fails the command outside a server."""
    guild_id = ctx.guild_id
    if guild_id is None:
        raise commands.CommandError(message)
    return guild_id


async def reply(ctx: GulpContext, content: str, **kwargs: Any) -> None:
    """Replies without pinging anyone: these messages mention the roles, channels and users involved, and a
    prefix-command reply is a normal message that would otherwise notify them."""
    await ctx.send(content, allowed_mentions=NO_MENTIONS, **kwargs)


async def reply_in_chunks(ctx: GulpContext, content: str) -> list[discord.Message]:
    """`reply`, in as many messages as it takes."""
    return [await ctx.send(chunk, allowed_mentions=NO_MENTIONS) for chunk in split_message(content)]


async def refuse(ctx: GulpContext, content: str) -> None:
    """Tells only the member who ran the command why nothing was done."""
    await ctx.send(content, ephemeral=True, allowed_mentions=NO_MENTIONS)


def choice_value(given: str, choices: list[app_commands.Choice[str]]) -> str | None:
    """The value of the choice `given` picks: a slash command sends the value, a prefix command may give the
    name ("Moderator role") or the value ("mod_role"), in any case."""
    wanted = given.strip().lower()
    for choice in choices:
        if wanted in (choice.value.lower(), choice.name.lower()):
            return choice.value
    return None


async def subcommand_required(ctx: GulpContext) -> None:
    """What a group used without one of its subcommands (with the prefix) answers."""
    command = ctx.command
    # In the order they were declared, which the slash command group keeps.
    app_command = getattr(command, "app_command", None)
    children = app_command.commands if isinstance(app_command, app_commands.Group) else getattr(command, "commands", [])
    names = [sub.name for sub in children]
    await ctx.send(f"You must specify one of the following subcommands: {', '.join(names)}", ephemeral=True)


def filter_choices(names: list[str], partial: str) -> list[app_commands.Choice[str]]:
    """The autocomplete choices among `names` that contain what has been typed so far (at most 25)."""
    partial = partial.lower()
    return [app_commands.Choice(name=name, value=name) for name in names if partial in name.lower()][:25]
