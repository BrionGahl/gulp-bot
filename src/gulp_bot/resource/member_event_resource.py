"""What the bot does when a member or a channel changes: a new trial or raider gets a personal officer
channel, and a deleted one is forgotten."""

from __future__ import annotations

import discord

from ..container import Services
from ..core.discord_helpers import role_ids


async def on_member_updated(services: Services, member: discord.Member) -> None:
    await services.officer_channels.create_for_member_if_needed(member, role_ids(member))


async def on_channel_deleted(services: Services, channel: discord.abc.GuildChannel) -> None:
    await services.officer_channels.forget_deleted_channel(channel.guild.id, channel.id, channel.name)
