"""Who may use which command. Each `check_*` tells a member who isn't allowed why, privately, and
returns whether they are; `is_*` only answers, for places a check can't go (autocomplete)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import discord
from discord import app_commands
from discord.ext import commands

from .context import GulpContext, slash_context
from .helper import role_ids

log = logging.getLogger(__name__)

Check = Callable[[GulpContext], Coroutine[Any, Any, bool]]
T = TypeVar("T")


async def _reply_denied(ctx: GulpContext, content: str) -> None:
    await ctx.send(content, ephemeral=True)


def _holds(ctx: GulpContext, role_id: int) -> bool:
    return role_id in role_ids(ctx.author)


async def check_is_moderator(ctx: GulpContext) -> bool:
    guild_id = ctx.guild_id
    if guild_id is None:
        await _reply_denied(ctx, "This command is only available in a server.")
        return False
    # Looked up by the guild the command was used in, so a role registered for another server never
    # grants access here.
    mod_role_id = ctx.bot.guild_settings.get(guild_id).mod_role_id
    if mod_role_id is None:
        await _reply_denied(ctx, "No moderator role is registered for this server. An administrator can set one with `/settings mod_role`.")
        return False

    allowed = _holds(ctx, mod_role_id)
    if not allowed:
        await _reply_denied(ctx, "This command is only available to moderators.")
    return allowed


async def check_is_raider(ctx: GulpContext) -> bool:
    guild_id = ctx.guild_id
    if guild_id is None:
        await _reply_denied(ctx, "This command is only available in a server.")
        return False
    settings = ctx.bot.guild_settings.get(guild_id)
    allowed_roles = [role for role in (settings.raider_role_id, settings.mod_role_id, settings.trial_role_id) if role is not None]
    if not allowed_roles:
        await _reply_denied(ctx, "No raider roles are registered for this server. An administrator or moderator can set them with `/settings`.")
        return False

    allowed = any(_holds(ctx, role) for role in allowed_roles)
    if not allowed:
        await _reply_denied(ctx, "This command is only available to raiders.")
    return allowed


async def check_is_admin_or_moderator(ctx: GulpContext) -> bool:
    """Gate for the `/settings` commands: server administrators, plus holders of the registered
    moderator role. Administrators are always allowed so that a server with no moderator role
    registered yet (or one registered wrongly) can still be configured."""
    guild_id = ctx.guild_id
    if guild_id is None:
        await _reply_denied(ctx, "This command is only available in a server.")
        return False

    allowed = is_admin_or_moderator(ctx, guild_id)
    if not allowed:
        await _reply_denied(ctx, "This command is only available to administrators and moderators.")
    return allowed


def is_admin_or_moderator(ctx: GulpContext, guild_id: int) -> bool:
    """Whether the invoking member is an administrator or holds `guild_id`'s registered moderator role."""
    mod_role_id = ctx.bot.guild_settings.get(guild_id).mod_role_id
    return is_administrator(ctx) or (mod_role_id is not None and _holds(ctx, mod_role_id))


async def check_is_administrator(ctx: GulpContext) -> bool:
    """Gate for changing which role is the moderator role or the developer role. Moderators can use the
    rest of `/settings`, but if they could also reassign these roles they could hand `/settings` (or
    the developer commands) access to anyone, or lock the other moderators out."""
    allowed = is_administrator(ctx)
    if not allowed:
        await _reply_denied(ctx, "Only administrators can change the moderator and developer roles.")
    return allowed


async def check_is_developer(ctx: GulpContext) -> bool:
    """Gate for the `/ai` commands: holders of the registered developer role, and nobody else. Not even
    administrators, who are Officers everywhere but have to be given the developer role like anyone
    else; an administrator registers the role with `/settings dev_role`."""
    guild_id = ctx.guild_id
    if guild_id is None:
        await _reply_denied(ctx, "This command is only available in a server.")
        return False
    if ctx.bot.guild_settings.get(guild_id).dev_role_id is None:
        await _reply_denied(ctx, "No developer role is registered for this server. An administrator can set one with `/settings dev_role`.")
        return False

    allowed = is_developer(ctx, guild_id)
    if not allowed:
        log.warning("Refused %s (%s) a developer command in guild %s", ctx.author.id, ctx.author.name, guild_id)
        await _reply_denied(ctx, "This command is only available to developers.")
    return allowed


def is_developer(ctx: GulpContext, guild_id: int) -> bool:
    """Whether the invoking member holds `guild_id`'s registered developer role."""
    dev_role_id = ctx.bot.guild_settings.get(guild_id).dev_role_id
    return dev_role_id is not None and _holds(ctx, dev_role_id)


def is_administrator(ctx: GulpContext) -> bool:
    """Whether the invoking member has the Administrator permission in the guild the command was used
    in (the guild owner always does)."""
    if ctx.interaction is not None:
        # Discord sends the member's resolved permissions with every interaction.
        return ctx.interaction.guild_id is not None and ctx.interaction.permissions.administrator
    # A message carries only the member's roles, resolved against the cached guild. If the guild
    # isn't cached this is `False`, which errs on the side of denying.
    return member_is_administrator(ctx.author)


def member_is_administrator(member: discord.abc.User) -> bool:
    """Whether `member` owns their server or holds a role with the Administrator permission."""
    if not isinstance(member, discord.Member):
        return False
    return member.guild_permissions.administrator


def prefix_check(predicate: Check) -> Callable[[T], T]:
    """`predicate` as the check of a command that can be used with the prefix as well."""
    return commands.check(predicate)


def slash_check(predicate: Check) -> Callable[[T], T]:
    """`predicate` as the check of a command that exists only as a slash command."""

    async def check(interaction: discord.Interaction) -> bool:
        return await predicate(await slash_context(interaction))

    return app_commands.check(check)
