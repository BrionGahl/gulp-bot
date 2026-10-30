"""Personal officer channels: a private text channel for each trial or raider, seen only by them and the
officers."""

from __future__ import annotations

import asyncio
import logging

import discord

from ..repository.officer_channel_repository import OfficerChannelRepository
from ..schema.absence_schema import OfficerChannelOwner
from ..schema.guild_settings_schema import GuildSettings
from ..schema.officer_channel_schema import RegisteredOfficerChannel
from .guild_settings_service import GuildSettingsService

log = logging.getLogger(__name__)

CHANNEL_TOPIC_PREFIX = "Personal officer channel for"


def has_trial_or_raider_role(settings: GuildSettings, member_roles: list[int]) -> bool:
    return any(role is not None and role in member_roles for role in (settings.trial_role_id, settings.raider_role_id))


def channel_name(base: str) -> str:
    """A channel name from a member's name: lowercase ASCII letters and digits, anything else a dash."""
    sanitized = "".join(char if char.isascii() and char.isalnum() else "-" for char in base.lower())
    return sanitized.strip("-") or "member"


def channel_topic(user_id: int) -> str:
    return f"{CHANNEL_TOPIC_PREFIX} <@{user_id}> — visible only to them and officers."


class OfficerChannelService:
    def __init__(self, repository: OfficerChannelRepository, guild_settings: GuildSettingsService):
        self._repository = repository
        self._guild_settings = guild_settings
        # Whose each recorded channel is, by channel ID: read on every message the bot sees, so kept in
        # memory, loaded on the first lookup and kept up to date by every change below. Only correct because
        # a single bot process owns the database.
        self._owners: dict[int, OfficerChannelOwner] | None = None
        self._loading = asyncio.Lock()

    async def owner_of(self, channel_id: int) -> OfficerChannelOwner | None:
        """Whose personal officer channel `channel_id` is, if it is one."""
        if self._owners is None:
            async with self._loading:
                if self._owners is None:
                    self._owners = await self._repository.owners()
        return self._owners.get(channel_id)

    def _remember(self, guild_id: int, user_id: int, channel_id: int | None) -> None:
        """Keeps the cache in step with a change to the member's row: `channel_id` is their channel now, or
        `None` if they have none."""
        if self._owners is None:
            return
        self._owners = {channel: owner for channel, owner in self._owners.items() if owner != OfficerChannelOwner(guild_id, user_id)}
        if channel_id is not None:
            self._owners[channel_id] = OfficerChannelOwner(guild_id, user_id)

    async def create_for_member_if_needed(self, member: discord.Member, member_roles: list[int]) -> None:
        """If `member` has the trial or raider role (and isn't themselves an officer), creates a private text
        channel for them under the personal officer channels category, visible only to them and the officer
        role. Does nothing if they already have one on record, whether the bot created it or an
        administrator registered an existing one.

        Deliberately doesn't look at what changed: idempotency comes entirely from the table, so an
        unrelated member update (nickname, avatar) of someone who already has a channel does nothing."""
        guild = member.guild
        settings = self._guild_settings.get(guild.id)
        if not has_trial_or_raider_role(settings, member_roles):
            return

        # The feature needs both of these registered for the member's guild; without them it's off.
        category_id, officer_role_id = settings.personal_officer_category_id, settings.mod_role_id
        if category_id is None or officer_role_id is None or officer_role_id in member_roles:
            return

        # Claim the member in the table before touching Discord: of any number of events for the same member
        # racing each other, exactly one gets to create a channel; the rest stop here.
        try:
            if not await self._repository.claim(guild.id, member.id):
                return
        except Exception as e:
            log.exception("Failed to check for an existing personal officer channel for %s: %s", member.name, e)
            return

        try:
            channel = await self._create_channel(member, category_id, officer_role_id)
        except Exception as e:
            log.exception("Failed to create personal officer channel for %s: %s", member.name, e)
            # Give the claim back so a later member update can try again.
            try:
                await self._repository.delete(guild.id, member.id)
            except Exception as e:
                log.exception("Failed to release the personal officer channel claim for %s: %s", member.name, e)
            return

        log.info("Created personal officer channel #%s for %s", channel.name, member.name)
        try:
            await self._repository.save_channel(guild.id, member.id, channel.id)
            self._remember(guild.id, member.id, channel.id)
        except Exception as e:
            # The claim row is still there, so this can't lead to a duplicate channel; the row just doesn't say
            # which channel it is.
            log.exception("Created personal officer channel #%s for %s but failed to record it: %s", channel.name, member.name, e)

    async def _create_channel(self, member: discord.Member, category_id: int, officer_role_id: int) -> discord.TextChannel:
        guild = member.guild
        officer_role = guild.get_role(officer_role_id) or next((role for role in await guild.fetch_roles() if role.id == officer_role_id), None)
        if officer_role is None:
            raise RuntimeError(f"the officer role {officer_role_id} doesn't exist in the server")
        category = guild.get_channel(category_id) or await guild.fetch_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError(f"channel {category_id} is not a category")

        allowed = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        return await guild.create_text_channel(
            channel_name(member.nick or member.name),
            category=category,
            topic=channel_topic(member.id),
            overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False), officer_role: allowed, member: allowed},
        )

    async def register_existing(self, guild_id: int, user_id: int, channel_id: int) -> None:
        """Records an existing channel as this member's, so the bot won't create one for them."""
        await self._repository.save_channel(guild_id, user_id, channel_id)
        self._remember(guild_id, user_id, channel_id)

    async def forget(self, guild_id: int, user_id: int) -> bool:
        """Forgets this member's channel, so the bot will create one for them again. Returns whether they had
        one on record."""
        forgotten = await self._repository.delete(guild_id, user_id)
        self._remember(guild_id, user_id, None)
        return forgotten

    async def forget_deleted_channel(self, guild_id: int, channel_id: int, channel_name: str) -> None:
        """When a recorded personal officer channel is deleted (e.g. by an officer cleaning up after a member
        leaves the raid team), drops its row, so the bot creates a fresh channel if the member is given the
        trial or raider role again. Any other channel is no concern of this."""
        try:
            if await self._repository.delete_channel(guild_id, channel_id):
                if self._owners is not None:
                    self._owners.pop(channel_id, None)
                log.info("Personal officer channel #%s was deleted; forgot its registration", channel_name)
        except Exception as e:
            log.exception("Failed to forget deleted personal officer channel #%s: %s", channel_name, e)

    async def list_registered(self, guild_id: int) -> list[RegisteredOfficerChannel]:
        return await self._repository.list_for_guild(guild_id)
