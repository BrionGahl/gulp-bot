"""The connection to Discord: which events reach which resource, and what happens when a command fails."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from .container import Services, build_services, scheduled_jobs
from .core import health_server
from .core.background_tasks import BackgroundTasks
from .core.config import Config
from .core.discord_context import GulpContext, slash_context
from .core.discord_helpers import NO_MENTIONS
from .core.http_client import Http
from .resource import member_event_resource, message_event_resource
from .resource.absence_resource import UndoAbsenceButton
from .resource.registry import register_all_resources
from .service.guild_settings_service import GuildSettingsService

if TYPE_CHECKING:
    import asyncpg

log = logging.getLogger(__name__)

PREFIX = "?"


class GulpTree(app_commands.CommandTree["GulpBot"]):
    """The slash commands. A command that fails tells the member what went wrong, as the prefix commands do;
    a check that fails has already said why."""

    async def on_error(self, interaction: discord.Interaction[GulpBot], error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            return
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        name = interaction.command.qualified_name if interaction.command else "?"
        log.error("An error occurred in command /%s: %s", name, original, exc_info=original)
        await tell_member_it_failed(await slash_context(interaction), original)


async def tell_member_it_failed(ctx: GulpContext, error: BaseException) -> None:
    """Tells the member who ran a command that it failed, and why."""
    text = str(error) or type(error).__name__
    try:
        await ctx.send(text[:2000], ephemeral=True, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException as e:
        log.error("Failed to report an error to %s: %s", ctx.author.name, e)


def _is_hybrid(command: object) -> bool:
    return bool(getattr(command, "__commands_is_hybrid_app_command__", False))


class GulpBot(commands.Bot):
    """The bot. Everything its commands and jobs call on is in `services`."""

    def __init__(self, config: Config, pool: asyncpg.Pool, guild_settings: GuildSettingsService):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        super().__init__(
            command_prefix=commands.when_mentioned_or(PREFIX),
            intents=intents,
            case_insensitive=True,
            help_command=None,
            tree_cls=GulpTree,
        )
        self.config = config
        self.tasks = BackgroundTasks()
        self.http_client = Http()
        self.services: Services = build_services(config, pool, self.http_client, guild_settings, self, self.tasks)
        self._synced = False

    async def get_context(self, origin: Any, /, *, cls: Any = GulpContext) -> Any:  # type: ignore[override]
        return await super().get_context(origin, cls=cls)

    async def setup_hook(self) -> None:
        register_all_resources(self)
        # Buttons the bot posted before a restart still answer.
        self.add_dynamic_items(UndoAbsenceButton)
        self.tasks.spawn(health_server.serve())
        for job in scheduled_jobs(self.services):
            self.tasks.spawn(job.run_forever())

    async def on_ready(self) -> None:
        if self.user is not None:
            log.info("Logged in as %s", self.user.name)
        if not self._synced:
            # Registered on every start, so the slash commands Discord shows always match this version.
            await self.tree.sync()
            self._synced = True

    async def close(self) -> None:
        self.tasks.cancel_all()
        await self.http_client.close()
        await super().close()

    # --- Events, each handed to its resource ---

    async def on_message(self, message: discord.Message) -> None:
        await message_event_resource.on_message(self.services, message)
        await self.process_commands(message)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        message_event_resource.on_message_edited(self.services, payload)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await message_event_resource.on_message_deleted(self.services, payload)

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        message_event_resource.on_messages_bulk_deleted(self.services, payload)

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        await member_event_resource.on_member_updated(self.services, after)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await member_event_resource.on_channel_deleted(self.services, channel)

    # --- Command logging and errors ---

    async def on_command(self, ctx: commands.Context[Any]) -> None:
        channel = getattr(ctx.channel, "name", None) or "<unknown>"
        log.info("%s in %s used slash command '%s'", ctx.author.name, channel, ctx.invoked_with)

    async def on_command_completion(self, ctx: commands.Context[Any]) -> None:
        if ctx.command is not None:
            log.info("Executed command %s!", ctx.command.qualified_name)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        # The prefix-and-slash commands are logged in `on_command`; these are the slash-only ones.
        command = interaction.command
        if interaction.type is discord.InteractionType.application_command and isinstance(command, app_commands.Command) and not _is_hybrid(command):
            channel = getattr(interaction.channel, "name", None) or "<unknown>"
            log.info("%s in %s used slash command '%s'", interaction.user.name, channel, command.qualified_name.split(" ")[0])

    async def on_app_command_completion(
        self, interaction: discord.Interaction, command: app_commands.Command[Any, ..., Any] | app_commands.ContextMenu
    ) -> None:
        # The prefix-and-slash commands are logged in `on_command_completion`.
        if not _is_hybrid(command):
            log.info("Executed command %s!", command.qualified_name)

    async def on_command_error(self, ctx: commands.Context[Any], error: commands.CommandError) -> None:  # type: ignore[override]
        if isinstance(error, (commands.CheckFailure, commands.CommandNotFound)) and not isinstance(error, commands.NoPrivateMessage):
            return
        assert isinstance(ctx, GulpContext)
        if isinstance(error, commands.NoPrivateMessage):
            await tell_member_it_failed(ctx, Exception("You cannot run this command in DMs."))
            return
        if isinstance(error, (commands.UserInputError, commands.ConversionError)):
            await tell_member_it_failed(ctx, error)
            return
        original: BaseException = error
        while isinstance(original, (commands.CommandInvokeError, commands.HybridCommandError, app_commands.CommandInvokeError)):
            original = original.original
        if isinstance(original, app_commands.CheckFailure):
            return
        name = ctx.command.qualified_name if ctx.command else "?"
        log.error("An error occurred in command %s: %s", name, original, exc_info=original)
        await tell_member_it_failed(ctx, original)
