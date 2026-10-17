"""Small Discord helpers shared by the resources and the services that act in Discord."""

from __future__ import annotations

import discord

# For messages that mention people or roles only to name them: nobody is pinged, not even @everyone.
NO_MENTIONS = discord.AllowedMentions.none()


def role_ids(member: discord.abc.User | None) -> list[int]:
    """The IDs of the roles `member` holds, as Discord sent them: including roles the bot's cache doesn't
    know. Nothing for a user who isn't a member of a server."""
    if not isinstance(member, discord.Member):
        return []
    # `Member.roles` leaves out roles missing from the cache; the IDs themselves are what counts here.
    return [int(role_id) for role_id in member._roles]


def member_is_administrator(member: discord.abc.User) -> bool:
    """Whether `member` owns their server or holds a role with the Administrator permission."""
    if not isinstance(member, discord.Member):
        return False
    return member.guild_permissions.administrator


async def fetch_guild(client: discord.Client, guild_id: int) -> discord.Guild:
    """The guild from the cache, or from Discord if it isn't cached."""
    return client.get_guild(guild_id) or await client.fetch_guild(guild_id)
