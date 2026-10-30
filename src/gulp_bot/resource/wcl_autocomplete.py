"""Autocomplete for the Warcraft Logs commands of `/settings wcl` and `/wcl`. Autocomplete runs no checks, so
the officers are checked here: anyone else is offered nothing."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from ..container import Services
from ..core.discord_context import services_of
from ..core.permission_checks import interaction_is_admin_or_moderator
from .resource_support import filter_choices

log = logging.getLogger(__name__)


async def _officer_choices(
    interaction: discord.Interaction, partial: str, lookup: Callable[[Services, int], Awaitable[list[str]]]
) -> list[app_commands.Choice[str]]:
    """The names `lookup` gives for the server that contain what has been typed so far, for an officer; nothing
    for anyone else, or if the lookup fails (the officer is typing: an error is logged, not shown)."""
    guild_id = interaction.guild_id
    if guild_id is None or not interaction_is_admin_or_moderator(interaction):
        return []
    try:
        names = await lookup(services_of(interaction), guild_id)
    except Exception:
        log.exception("Autocomplete lookup failed for guild %s", guild_id)
        return []
    return filter_choices(names, partial)


async def autocomplete_tracked_ability(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    """The names of this server's tracked abilities that contain what has been typed so far."""

    async def names(services: Services, guild_id: int) -> list[str]:
        return [ability.name for ability in await services.tracked_abilities.list_tracked(guild_id)]

    return await _officer_choices(interaction, partial, names)


async def autocomplete_tracked_enemy(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    """The names of this server's tracked enemies that contain what has been typed so far."""
    return await _officer_choices(interaction, partial, lambda services, guild_id: services.tracked_enemies.list_tracked(guild_id))


async def autocomplete_ignored_player(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    """The ignored players whose names contain what has been typed so far."""
    return await _officer_choices(interaction, partial, lambda services, guild_id: services.ignored_players.list_ignored(guild_id))
