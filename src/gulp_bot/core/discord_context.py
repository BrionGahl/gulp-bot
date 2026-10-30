"""The context every command runs with, whether it was invoked as a slash command or with the `?` prefix."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from ..bot import GulpBot
    from ..container import Services


# Told to a member who uses a server command anywhere else.
ONLY_IN_A_SERVER = "This command is only available in a server."
# Told to a member who isn't allowed a command.
ONLY_FOR_RAIDERS = "This command is only available to raiders."
ONLY_FOR_DEVELOPERS = "This command is only available to developers."
# Told when a command needs the raid notes forum and the server has none registered.
NO_RAID_NOTES_CHANNEL = "No raid notes channel is registered for this server. An administrator or moderator can set one with `/settings raid_notes_channel`."


class GulpContext(commands.Context["GulpBot"]):
    """A command's context. A command marked ephemeral (`extras["ephemeral"]`) answers only the member who
    used it, unless a message says otherwise, and defers the same way; and `defer` shows the typing
    indicator for a prefix command, which has nothing else to defer."""

    @property
    def services(self) -> Services:
        """Everything the command can call on (see `container.py`)."""
        return self.bot.services

    @property
    def ephemeral_command(self) -> bool:
        extras = getattr(self.command, "extras", None) or {}
        return bool(extras.get("ephemeral", False))

    @property
    def guild_id(self) -> int | None:
        if self.interaction is not None:
            return self.interaction.guild_id
        return None if self.guild is None else self.guild.id

    async def send(self, content: str | None = None, **kwargs: Any) -> discord.Message:  # type: ignore[override]
        if self.interaction is not None and "ephemeral" not in kwargs:
            kwargs["ephemeral"] = self.ephemeral_command
        return await super().send(content, **kwargs)

    async def defer(self, *, ephemeral: bool | None = None) -> None:
        if self.interaction is None:
            await self.typing()
            return
        if self.interaction.response.is_done():
            return
        await self.interaction.response.defer(ephemeral=self.ephemeral_command if ephemeral is None else ephemeral)


async def slash_context(interaction: discord.Interaction[Any]) -> GulpContext:
    """The context of a command that exists only as a slash command, made once per interaction so that its
    checks and its body share it."""
    existing = interaction.extras.get("gulp_context")
    if isinstance(existing, GulpContext):
        return existing
    ctx = await GulpContext.from_interaction(interaction)
    interaction.extras["gulp_context"] = ctx
    return ctx


def bot_of(interaction: discord.Interaction[Any]) -> GulpBot:
    """The bot an interaction came to: for autocomplete and buttons, which get no context."""
    return cast("GulpBot", interaction.client)


def services_of(interaction: discord.Interaction[Any]) -> Services:
    """Everything the bot can call on, from an interaction: for autocomplete and buttons."""
    return bot_of(interaction).services


def require_guild(ctx: GulpContext, message: str = ONLY_IN_A_SERVER) -> int:
    """The guild the command was used in. Fails the command, telling the member `message`, outside a server."""
    guild_id = ctx.guild_id
    if guild_id is None:
        raise commands.CommandError(message)
    return guild_id


async def guild_context(interaction: discord.Interaction[Any]) -> tuple[GulpContext, int]:
    """The context of a slash command and the guild it was used in. Fails the command outside a server,
    which Discord doesn't allow for the bot's server commands anyway."""
    ctx = await slash_context(interaction)
    return ctx, require_guild(ctx)
