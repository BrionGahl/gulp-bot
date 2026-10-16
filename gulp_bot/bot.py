"""The bot: its connection to Discord, what it holds for the commands, and the events it handles."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from . import announcements, clips, get_them, health, message_replay, personal_officer_channels, raid_attendance, raid_notes
from .ai import Ai
from .ai.discord_messages import answer_trigger
from .config import Config
from .context import GulpContext, slash_context
from .guild_settings import GuildSettingsStore
from .helper import NO_MENTIONS
from .message_replay import MessageReplay
from .warcraftlogs import WarcraftLogsClients
from .web import Http

if TYPE_CHECKING:
    import asyncpg

log = logging.getLogger(__name__)

PREFIX = "?"


class GulpTree(app_commands.CommandTree["GulpBot"]):
    """The slash commands. A command that fails tells the member what went wrong, as the prefix commands
    do; a check that fails has already said why."""

    async def on_error(self, interaction: discord.Interaction[GulpBot], error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            return
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        name = interaction.command.qualified_name if interaction.command else "?"
        log.error("An error occurred in command /%s: %s", name, original, exc_info=original)
        ctx = await slash_context(interaction)
        await _say_error(ctx, original)


async def _say_error(ctx: GulpContext, error: BaseException) -> None:
    """Tells the member who ran a command that it failed, and why."""
    text = str(error) or type(error).__name__
    try:
        await ctx.send(text[:2000], ephemeral=True, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException as e:
        log.error("Failed to report an error to %s: %s", ctx.author.name, e)


class GulpBot(commands.Bot):
    """The bot, and everything its commands and background jobs share."""

    def __init__(self, config: Config, db: asyncpg.Pool, guild_settings: GuildSettingsStore):
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
        # Per-guild role/channel/user IDs registered by server administrators.
        self.guild_settings = guild_settings
        # Database pool, for the tables that are queried directly rather than cached in memory.
        self.db = db
        self.http_client = Http()
        # Buffers each guild's replay users' recent messages so a deleted one can be reposted.
        self.message_replay = MessageReplay()
        # Warcraft Logs API clients, one per guild that has registered its client credentials.
        self.warcraft_logs = WarcraftLogsClients(self.http_client)
        # The AI assistant behind `/ask` and "Bot please"; `None` without `ANTHROPIC_API_KEY`, unless it
        # runs on a local model (`AI_BACKEND=local`), which needs no key.
        self.ai: Ai | None = Ai.from_config(self.http_client, config)
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Runs `coroutine` on its own, keeping hold of it until it is done (the event loop only keeps a
        weak reference to a task)."""
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("A background task failed: %s", task.exception(), exc_info=task.exception())

    async def get_context(self, origin: Any, /, *, cls: Any = GulpContext) -> Any:  # type: ignore[override]
        return await super().get_context(origin, cls=cls)

    async def setup_hook(self) -> None:
        from .commands import register_all

        register_all(self)
        self.spawn(health.serve())
        self.spawn(raid_notes.schedule_weekly_posts(self))
        self.spawn(raid_attendance.schedule_snapshots(self))
        self.spawn(announcements.schedule_announcements(self))

    async def on_ready(self) -> None:
        if self.user is not None:
            log.info("Logged in as %s", self.user.name)
        if not getattr(self, "_synced", False):
            # Registered on every start, so the slash commands Discord shows always match this version.
            await self.tree.sync()
            self._synced = True

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.http_client.close()
        await super().close()

    # --- events ---

    async def on_message(self, message: discord.Message) -> None:
        await clips.enforce_clips_channel(self, message)
        message_replay.remember_message(self, message)
        # On a task of its own: the assistant takes seconds to answer, and a command in the same message
        # shouldn't wait for it.
        self.spawn(self._converse(message))
        await self.process_commands(message)

    async def _converse(self, message: discord.Message) -> None:
        # "bot get them" is a command, not a question for the assistant.
        if not await get_them.handle(self, message):
            await answer_trigger(self, message)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await message_replay.handle_message_delete(self, payload.message_id, payload.guild_id)

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        message_replay.forget_messages(self, list(payload.message_ids))

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        await personal_officer_channels.handle_role_update(self, after)

    # --- commands ---

    async def on_command(self, ctx: commands.Context[Any]) -> None:
        channel = getattr(ctx.channel, "name", None) or "<unknown>"
        log.info("%s in %s used slash command '%s'", ctx.author.name, channel, ctx.invoked_with)

    async def on_command_completion(self, ctx: commands.Context[Any]) -> None:
        if ctx.command is not None:
            log.info("Executed command %s!", ctx.command.qualified_name)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        # The prefix and hybrid commands are logged in `on_command`; these are the slash-only ones.
        command = interaction.command
        if interaction.type is discord.InteractionType.application_command and isinstance(command, app_commands.Command):
            if not getattr(command, "__commands_is_hybrid_app_command__", False):
                channel = getattr(interaction.channel, "name", None) or "<unknown>"
                log.info("%s in %s used slash command '%s'", interaction.user.name, channel, command.qualified_name.split(" ")[0])

    async def on_app_command_completion(
        self, interaction: discord.Interaction, command: app_commands.Command[Any, ..., Any] | app_commands.ContextMenu
    ) -> None:
        # The prefix-and-slash commands are logged in `on_command_completion`.
        if not getattr(command, "__commands_is_hybrid_app_command__", False):
            log.info("Executed command %s!", command.qualified_name)

    async def on_command_error(self, ctx: commands.Context[Any], error: commands.CommandError) -> None:  # type: ignore[override]
        if isinstance(error, (commands.CheckFailure, commands.CommandNotFound)) and not isinstance(error, commands.NoPrivateMessage):
            return
        assert isinstance(ctx, GulpContext)
        if isinstance(error, commands.NoPrivateMessage):
            await _say_error(ctx, Exception("You cannot run this command in DMs."))
            return
        if isinstance(error, (commands.UserInputError, commands.ConversionError)):
            await _say_error(ctx, error)
            return
        original: BaseException = error
        while isinstance(original, (commands.CommandInvokeError, commands.HybridCommandError, app_commands.CommandInvokeError)):
            original = original.original
        if isinstance(original, app_commands.CheckFailure):
            return
        name = ctx.command.qualified_name if ctx.command else "?"
        log.error("An error occurred in command %s: %s", name, original, exc_info=original)
        await _say_error(ctx, original)
