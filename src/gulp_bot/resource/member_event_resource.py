"""What the bot does when a member changes: a new trial or raider gets a personal officer channel."""

from __future__ import annotations

import discord

from ..container import Services
from ..core.discord_helpers import role_ids


async def on_member_updated(services: Services, member: discord.Member) -> None:
    await services.officer_channels.create_for_member_if_needed(member, role_ids(member))
