"""The context every command runs with, whether it was invoked as a slash command or with the `?` prefix."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from ..bot import GulpBot  # noqa: F401
    from ..container import Services


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
