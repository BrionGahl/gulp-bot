"""`/source`, `/help` and `/register`."""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..core.discord_context import GulpContext, bot_of
from ..core.embed import command_embed, to_discord_embed
from ..mapper.help_entry_mapper import help_entries
from ..mapper.help_text_mapper import command_help_page, help_overview
from ..service.help_service import find_command
from .resource_support import filter_choices, meta

log = logging.getLogger(__name__)

REPOSITORY_URL = "https://github.com/BrionGahl/gulp-bot"


@commands.hybrid_command(name="source", description="Links to the bot GitHub repo")
async def source(ctx: GulpContext) -> None:
    await ctx.send(REPOSITORY_URL)


meta(source, category="Utilities", ephemeral=True)


@commands.hybrid_command(name="help", description="List the bot's commands, or show details for one of them")
@app_commands.describe(command="Command to show details for, e.g. settings")
async def help_command(ctx: GulpContext, *, command: str | None = None) -> None:
    # Built from the registered commands' own descriptions, so it can't drift out of date.
    entries = help_entries(ctx.bot.tree)
    if command is None or not command.strip():
        await ctx.send(help_overview(entries, "/" if ctx.interaction is not None else ctx.prefix or "?"), ephemeral=True)
        return

    found = find_command(entries, command)
    if found is None:
        await ctx.send(f"There's no command called `{command.strip()}`. Use `/help` to see them all.")
        return
    # One command's page goes in an embed: `/settings` with all of its subcommands is over the 2000-character
    # limit of a message, and an embed description has room for twice that.
    path, entry = found
    await ctx.send(embed=to_discord_embed(command_embed(ctx.services.config.bot_name, title=f"/{path}", description=command_help_page(path, entry))))


meta(
    help_command,
    category="Utilities",
    help_text="Give a command name to see its full description, parameters and subcommands, e.g. `/help settings` or `/help settings raid_voice`.",
    ephemeral=True,
)


@help_command.autocomplete("command")
async def autocomplete_command(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    tree: app_commands.CommandTree[Any] = bot_of(interaction).tree
    return filter_choices([entry.name for entry in help_entries(tree) if not entry.hidden], partial)


class RegisterButtons(discord.ui.View):
    """The four choices `/register` offers, for the member who ran it only."""

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
async def register(ctx: GulpContext) -> None:
    # Only the bot's owner, checked here rather than with a role check: the owner has to be able to run it
    # in a server that hasn't registered any roles yet.
    bot = ctx.bot
    if not await bot.is_owner(ctx.author):
        await ctx.send("Can only be used by bot owner")
        return

    view = RegisterButtons(ctx.author.id)
    message = await ctx.send("Choose what to do with the commands:", view=view)
    await view.wait()
    # Only to take the buttons away: if the message is gone, so are they.
    with contextlib.suppress(discord.HTTPException):
        await message.edit(view=None)
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

COMMANDS = [source, help_command, register]
