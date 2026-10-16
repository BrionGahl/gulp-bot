"""Small Discord helpers shared by the commands and the background jobs."""

from __future__ import annotations

import discord

# The purple of the bot's embeds.
EMBED_COLOUR = 0xAF69EE

# For messages that mention people or roles only to name them: nobody is pinged, not even @everyone.
NO_MENTIONS = discord.AllowedMentions.none()


def base_embed(bot_name: str) -> discord.Embed:
    embed = discord.Embed(colour=EMBED_COLOUR)
    embed.set_author(name=bot_name)
    embed.set_footer(text="/source to view repository")
    return embed


def embed_from_dict(data: dict) -> discord.Embed:
    """An embed built as plain data (so that it can be tested without Discord) as discord.py's object."""
    return discord.Embed.from_dict(data)


def role_ids(member: discord.abc.User | None) -> list[int]:
    """The IDs of the roles `member` holds, as Discord sent them: including roles the bot's cache
    doesn't know. Nothing for a user who isn't a member of a server."""
    if not isinstance(member, discord.Member):
        return []
    # `Member.roles` leaves out roles missing from the cache; the IDs themselves are what counts here.
    return [int(role_id) for role_id in member._roles]
