"""Whether a role, channel or member an officer named really belongs to their server. Asked of Discord rather
than trusted from the command's arguments, since a prefix command can be handed any ID."""

from __future__ import annotations

import discord

from ..core.discord_helpers import fetch_guild

TEXT_CHANNEL_TYPES = [discord.ChannelType.text, discord.ChannelType.news]
VOICE_CHANNEL_TYPES = [discord.ChannelType.voice, discord.ChannelType.stage_voice]


class GuildValidationService:
    def __init__(self, discord_client: discord.Client):
        self._discord = discord_client

    async def role_problem(self, guild_id: int, role_id: int) -> str | None:
        """Why `role_id` can't be registered in `guild_id`, or `None` if it can."""
        if role_id == guild_id:
            return "`@everyone` can't be registered; pick a specific role."
        roles = await (await fetch_guild(self._discord, guild_id)).fetch_roles()
        if not any(role.id == role_id for role in roles):
            return "That role doesn't belong to this server, so it can't be registered here."
        return None

    async def channel_problem(self, guild_id: int, channel_id: int, allowed: list[discord.ChannelType], expected: str) -> str | None:
        """Why `channel_id` can't be registered in `guild_id`, or `None` if it can: it has to be one of the
        server's channels, and of one of the `allowed` types (`expected` names them, e.g. "a text channel")."""
        channels = await (await fetch_guild(self._discord, guild_id)).fetch_channels()
        channel = next((channel for channel in channels if channel.id == channel_id), None)
        if channel is None:
            return "That channel doesn't belong to this server, so it can't be registered here."
        if channel.type not in allowed:
            return f"<#{channel_id}> must be {expected}."
        return None

    async def is_member(self, guild_id: int, user_id: int) -> bool:
        """Whether `user_id` is a member of `guild_id`."""
        try:
            await (await fetch_guild(self._discord, guild_id)).fetch_member(user_id)
        except discord.NotFound:
            return False
        return True

    async def existing_role_ids(self, guild_id: int) -> set[int]:
        """The IDs of the server's roles, as Discord has them now."""
        return {role.id for role in await (await fetch_guild(self._discord, guild_id)).fetch_roles()}
