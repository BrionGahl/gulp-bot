"""Who may use which command.

Each `check_*` tells a member who isn't allowed why, privately, and returns whether they are; `is_*`
only answers, for places a check can't go (autocomplete). Roles are looked up for the guild the command
was used in, so a role registered for another server never grants anything here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import discord
from discord import app_commands
from discord.ext import commands

from .discord_context import GulpContext, slash_context
from .discord_helpers import member_is_administrator, role_ids

log = logging.getLogger(__name__)

Check = Callable[[GulpContext], Coroutine[Any, Any, bool]]
T = TypeVar("T")

ONLY_IN_A_SERVER = "This command is only available in a server."


async def _deny(ctx: GulpContext, reason: str) -> bool:
    await ctx.send(reason, ephemeral=True)
    return False


def _holds(ctx: GulpContext, role_id: int | None) -> bool:
    return role_id is not None and role_id in role_ids(ctx.author)


async def check_is_moderator(ctx: GulpContext) -> bool:
    guild_id = ctx.guild_id
    if guild_id is None:
        return await _deny(ctx, ONLY_IN_A_SERVER)
    mod_role_id = ctx.services.guild_settings.get(guild_id).mod_role_id
    if mod_role_id is None:
        return await _deny(ctx, "No moderator role is registered for this server. An administrator can set one with `/settings mod_role`.")
    if not _holds(ctx, mod_role_id):
        return await _deny(ctx, "This command is only available to moderators.")
    return True


async def check_is_raider(ctx: GulpContext) -> bool:
    guild_id = ctx.guild_id
    if guild_id is None:
        return await _deny(ctx, ONLY_IN_A_SERVER)
    settings = ctx.services.guild_settings.get(guild_id)
    allowed_roles = [role for role in (settings.raider_role_id, settings.mod_role_id, settings.trial_role_id) if role is not None]
    if not allowed_roles:
        return await _deny(ctx, "No raider roles are registered for this server. An administrator or moderator can set them with `/settings`.")
    if not any(_holds(ctx, role) for role in allowed_roles):
        return await _deny(ctx, "This command is only available to raiders.")
    return True


async def check_is_admin_or_moderator(ctx: GulpContext) -> bool:
    """Gate for the `/settings` and `/wcl` commands: server administrators, plus holders of the registered
    moderator role. Administrators are always allowed so that a server with no moderator role registered
    yet (or one registered wrongly) can still be configured."""
    guild_id = ctx.guild_id
    if guild_id is None:
        return await _deny(ctx, ONLY_IN_A_SERVER)
    if not is_admin_or_moderator(ctx, guild_id):
        return await _deny(ctx, "This command is only available to administrators and moderators.")
    return True


async def check_is_administrator(ctx: GulpContext) -> bool:
    """Gate for changing which role is the moderator role or the developer role. Moderators can use the
    rest of `/settings`, but if they could also reassign these roles they could hand `/settings` (or the
    developer commands) access to anyone, or lock the other moderators out."""
    if not is_administrator(ctx):
        return await _deny(ctx, "Only administrators can change the moderator and developer roles.")
    return True


async def check_is_developer(ctx: GulpContext) -> bool:
    """Gate for the `/ai` commands: holders of the registered developer role, and nobody else. Not even
    administrators, who are Officers everywhere but have to be given the developer role like anyone else;
    an administrator registers the role with `/settings dev_role`."""
    guild_id = ctx.guild_id
    if guild_id is None:
        return await _deny(ctx, ONLY_IN_A_SERVER)
    if ctx.services.guild_settings.get(guild_id).dev_role_id is None:
        return await _deny(ctx, "No developer role is registered for this server. An administrator can set one with `/settings dev_role`.")
    if not is_developer(ctx, guild_id):
        log.warning("Refused %s (%s) a developer command in guild %s", ctx.author.id, ctx.author.name, guild_id)
        return await _deny(ctx, "This command is only available to developers.")
    return True


def is_admin_or_moderator(ctx: GulpContext, guild_id: int) -> bool:
    """Whether the invoking member is an administrator or holds `guild_id`'s registered moderator role."""
    return is_administrator(ctx) or _holds(ctx, ctx.services.guild_settings.get(guild_id).mod_role_id)


def is_developer(ctx: GulpContext, guild_id: int) -> bool:
    """Whether the invoking member holds `guild_id`'s registered developer role."""
    return _holds(ctx, ctx.services.guild_settings.get(guild_id).dev_role_id)


def is_administrator(ctx: GulpContext) -> bool:
    """Whether the invoking member has the Administrator permission in the guild the command was used in
    (the guild owner always does)."""
    if ctx.interaction is not None:
        # Discord sends the member's resolved permissions with every interaction.
        return ctx.interaction.guild_id is not None and ctx.interaction.permissions.administrator
    # A message carries only the member's roles, resolved against the cached guild. If the guild isn't
    # cached this is `False`, which errs on the side of denying.
    return member_is_administrator(ctx.author)


def interaction_is_admin_or_moderator(interaction: discord.Interaction) -> bool:
    """`is_admin_or_moderator`, straight from an interaction: for autocomplete, which runs no checks."""
    guild_id = interaction.guild_id
    if guild_id is None:
        return False
    mod_role_id = interaction.client.services.guild_settings.get(guild_id).mod_role_id  # type: ignore[attr-defined]
    return interaction.permissions.administrator or (mod_role_id is not None and mod_role_id in role_ids(interaction.user))


def interaction_is_developer(interaction: discord.Interaction) -> bool:
    """`is_developer`, straight from an interaction: for autocomplete, which runs no checks."""
    guild_id = interaction.guild_id
    if guild_id is None:
        return False
    dev_role_id = interaction.client.services.guild_settings.get(guild_id).dev_role_id  # type: ignore[attr-defined]
    return dev_role_id is not None and dev_role_id in role_ids(interaction.user)


def prefix_check(predicate: Check) -> Callable[[T], T]:
    """`predicate` as the check of a command that can be used with the prefix as well."""
    return commands.check(predicate)


def slash_check(predicate: Check) -> Callable[[T], T]:
    """`predicate` as the check of a command that exists only as a slash command."""

    async def check(interaction: discord.Interaction) -> bool:
        return await predicate(await slash_context(interaction))

    return app_commands.check(check)
