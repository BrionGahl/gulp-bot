"""`/source`, `/help` and `/register`."""

import logging
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import check_is_moderator, prefix_check
from ..context import GulpContext
from ..helper import base_embed
from .base import meta
from .help_text import HelpEntry, Parameter, find_command, overview, render_command_help

log = logging.getLogger(__name__)


@commands.hybrid_command(name="source", description="Links to the bot GitHub repo")
async def source(ctx: GulpContext) -> None:
    await ctx.send("https://github.com/BrionGahl/gulp-bot")


meta(source, category="Utilities", ephemeral=True)


def _entry(command: app_commands.Command[Any, ..., Any] | app_commands.Group) -> HelpEntry:
    extras = command.extras
    description = "" if command.description == "…" else command.description
    if isinstance(command, app_commands.Group):
        return HelpEntry(
            name=command.name,
            description=description,
            help_text=extras.get("help_text"),
            category=extras.get("category"),
            hidden=bool(extras.get("hidden", False)),
            subcommands=[_entry(child) for child in command.commands],
        )
    return HelpEntry(
        name=command.name,
        description=description,
        help_text=extras.get("help_text"),
        category=extras.get("category"),
        hidden=bool(extras.get("hidden", False)),
        parameters=[
            Parameter(parameter.name, parameter.required, "" if parameter.description == "…" else parameter.description) for parameter in command.parameters
        ],
    )


def help_entries(tree: app_commands.CommandTree[Any]) -> list[HelpEntry]:
    """The bot's commands as `/help` sees them, in the order they were added. Every command has a slash
    form, so the slash commands are all of them."""
    return [_entry(command) for command in tree.get_commands() if isinstance(command, (app_commands.Command, app_commands.Group))]


@commands.hybrid_command(name="help", description="List the bot's commands, or show details for one of them")
@app_commands.describe(command="Command to show details for, e.g. settings")
async def help_command(ctx: GulpContext, *, command: str | None = None) -> None:
    entries = help_entries(ctx.bot.tree)
    if command is None or not command.strip():
        # The overview is the list of top-level commands by category, built from the registered
        # commands' own descriptions so it can't drift out of date.
        await ctx.send(overview(entries, "/" if ctx.interaction is not None else ctx.prefix or "?"), ephemeral=True)
        return

    # One command's page goes in an embed: `/settings` with all of its subcommands is over the
    # 2000-character limit of a message, and an embed description has room for twice that.
    found = find_command(entries, command)
    if found is None:
        await ctx.send(f"There's no command called `{command.strip()}`. Use `/help` to see them all.")
        return
    path, entry = found
    embed = base_embed(ctx.bot.config.bot_name)
    embed.title = f"/{path}"
    embed.description = render_command_help(path, entry)
    await ctx.send(embed=embed)


meta(
    help_command,
    category="Utilities",
    help_text="Give a command name to see its full description, parameters and subcommands, e.g. `/help settings` or `/help settings raid_voice`.",
    ephemeral=True,
)


@help_command.autocomplete("command")
async def autocomplete_command(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    tree: app_commands.CommandTree[Any] = interaction.client.tree  # type: ignore[attr-defined]
    names = [entry.name for entry in help_entries(tree) if not entry.hidden and entry.name.startswith(partial)]
    return [app_commands.Choice(name=name, value=name) for name in names[:25]]


class _RegisterButtons(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.pressed: str | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def _press(self, interaction: discord.Interaction, choice: str) -> None:
        self.pressed = choice
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Register in guild", style=discord.ButtonStyle.primary, emoji="📋")
    async def register_guild(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._press(interaction, "register.guild")

    @discord.ui.button(label="Delete in guild", style=discord.ButtonStyle.danger, emoji="🗑")
    async def unregister_guild(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._press(interaction, "unregister.guild")

    @discord.ui.button(label="Register globally", style=discord.ButtonStyle.primary, emoji="📋")
    async def register_global(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._press(interaction, "register.global")

    @discord.ui.button(label="Unregister globally", style=discord.ButtonStyle.danger, emoji="🗑")
    async def unregister_global(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._press(interaction, "unregister.global")


@commands.hybrid_command(name="register", description="Register slash commands in this guild or globally")
@prefix_check(check_is_moderator)
async def register(ctx: GulpContext) -> None:
    bot = ctx.bot
    if not await bot.is_owner(ctx.author):
        await ctx.send("Can only be used by bot owner")
        return

    view = _RegisterButtons(ctx.author.id)
    message = await ctx.send("Choose what to do with the commands:", view=view)
    await view.wait()
    try:
        await message.edit(view=None)
    except discord.HTTPException:
        pass
    if view.pressed is None:
        await ctx.send(":warning: You didn't interact in time - please run the command again.")
        return

    register_commands = view.pressed.startswith("register")
    is_global = view.pressed.endswith("global")
    application_id = bot.application_id
    if application_id is None:
        raise commands.CommandError("The bot doesn't know its application ID yet.")
    payload = [command.to_dict(bot.tree) for command in bot.tree.get_commands()] if register_commands else []

    started = time.monotonic()
    if is_global:
        await ctx.send(f":gear: Registering {len(payload)} global commands..." if register_commands else ":gear: Unregistering global commands...")
        await bot.http.bulk_upsert_global_commands(application_id, payload=payload)
    else:
        if ctx.guild_id is None:
            await ctx.send(":x: Must be called in guild")
            return
        await ctx.send(f":gear: Registering {len(payload)} guild commands..." if register_commands else ":gear: Unregistering guild commands...")
        await bot.http.bulk_upsert_guild_commands(application_id, ctx.guild_id, payload=payload)
    await ctx.send(f":white_check_mark: Done! Took {int((time.monotonic() - started) * 1000)}ms")


meta(register, category="Utilities", hidden=True, ephemeral=True)
