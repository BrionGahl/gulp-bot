"""Embeds as plain data, in the JSON shape Discord takes them.

The mappers build embeds this way so that they can be tested without Discord; `to_discord_embed` turns
one into what discord.py sends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import discord

# The purple of the bot's embeds.
EMBED_COLOUR = 0xAF69EE

# The footer of the embeds that answer commands.
SOURCE_FOOTER = "/source to view repository"

# Discord's limits on an embed: its title, its description, one field's value, and all of its text
# together (title, description, field names and values, footer).
MAX_TITLE_CHARS = 256
MAX_DESCRIPTION_CHARS = 4096
MAX_FIELD_CHARS = 1024
MAX_EMBED_CHARS = 6000

Embed = dict[str, Any]


def new_embed(
    title: str | None = None,
    url: str | None = None,
    description: str | None = None,
    footer: str | None = None,
    timestamp: str | None = None,
) -> Embed:
    """A rich embed in the bot's colour, with whichever of these parts are given."""
    embed: Embed = {"type": "rich", "color": EMBED_COLOUR}
    if title is not None:
        embed["title"] = title
    if url is not None:
        embed["url"] = url
    if description is not None:
        embed["description"] = description
    if footer is not None:
        embed["footer"] = {"text": footer}
    if timestamp is not None:
        embed["timestamp"] = timestamp
    return embed


def command_embed(bot_name: str, title: str | None = None, description: str | None = None) -> Embed:
    """The embed a command answers with: the bot's name above, the repository note below."""
    embed = new_embed(title=title, description=description, footer=SOURCE_FOOTER)
    embed["author"] = {"name": bot_name}
    return embed


def add_field(embed: Embed, name: str, value: str, inline: bool = False) -> None:
    embed.setdefault("fields", []).append({"name": name, "value": value, "inline": inline})


def to_discord_embed(embed: Embed) -> discord.Embed:
    """An embed built as plain data as discord.py's object. (discord.py is imported only here, so that
    everything else in this module works without it.)"""
    import discord

    return discord.Embed.from_dict(embed)
