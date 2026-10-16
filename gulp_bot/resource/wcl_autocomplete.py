"""Autocomplete for the Warcraft Logs commands of `/settings wcl` and `/wcl`. Autocomplete runs no checks, so
the officers are checked here: anyone else is offered nothing."""

from __future__ import annotations

import discord
from discord import app_commands

from ..core.permission_checks import interaction_is_admin_or_moderator
from .resource_support import filter_choices


async def autocomplete_tracked_ability(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    """The names of this server's tracked abilities that contain what has been typed so far."""
    names: list[str] = []
    if interaction.guild_id is not None and interaction_is_admin_or_moderator(interaction):
        try:
            tracked = await interaction.client.services.tracked_abilities.list_tracked(interaction.guild_id)  # type: ignore[attr-defined]
            names = [ability.name for ability in tracked]
        except Exception:
            names = []
    return filter_choices(names, partial)


async def autocomplete_ignored_player(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    """The ignored players whose names contain what has been typed so far."""
    names: list[str] = []
    if interaction.guild_id is not None and interaction_is_admin_or_moderator(interaction):
        try:
            names = await interaction.client.services.ignored_players.list_ignored(interaction.guild_id)  # type: ignore[attr-defined]
        except Exception:
            names = []
    return filter_choices(names, partial)
