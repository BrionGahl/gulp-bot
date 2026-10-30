"""Delete-and-replay: a registered member's self-deleted messages are reposted, unless the audit log shows
someone else (a moderator) deleted them."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import discord

from ..core.discord_helpers import NO_MENTIONS
from ..core.embed import to_discord_embed
from ..mapper.message_replay_mapper import replay_embed
from ..repository.replay_message_memory_repository import ReplayMessageMemoryRepository
from ..schema.message_replay_schema import DeletionKey, SeenDeletion, StoredAttachment, StoredMessage
from .guild_settings_service import GuildSettingsService

log = logging.getLogger(__name__)

# How many of the newest message deletions in the audit log are read to tell who deleted a message.
AUDIT_LOG_ENTRIES = 25

# Discord writes the audit log entry for a moderator deletion a beat after the gateway delivers the delete
# event, so we wait this many seconds before going to look for it.
AUDIT_LOG_LOOKUP_DELAY = 2.0

# On a fresh process we have no previous audit log state to diff against, so we fall back to "is the newest
# matching MESSAGE_DELETE entry recent enough to plausibly be the deletion we're handling?". Needs to
# comfortably cover `AUDIT_LOG_LOOKUP_DELAY` plus Discord's own lag.
COLD_START_RECENT_SECS = 20


class ModeratorDeletionTracker:
    """The most recent MESSAGE_DELETE audit log entry already seen for each guild, author and channel.
    Discord coalesces repeat deletions by the same moderator into a single entry (bumping its `count`), so
    tracking this lets each later deletion still be recognised. It is kept per author and channel because
    that is what the lookup is about: with several replay users, one user's entry must not be compared
    against another's."""

    def __init__(self) -> None:
        self._last_seen: dict[DeletionKey, SeenDeletion] = {}

    def is_new_deletion(self, key: DeletionKey, entry_id: int, count: int, age_secs: float) -> bool:
        """Whether the newest audit log entry for `key` is a deletion not yet accounted for, and remembers
        it as seen."""
        previous = self._last_seen.get(key)
        if previous is not None:
            # A genuinely new deletion is either a new entry or a higher count on the same one.
            new = entry_id != previous.entry_id or count > previous.count
        else:
            # Nothing to diff against yet: trust it only if it's recent enough to be this deletion.
            new = age_secs <= COLD_START_RECENT_SECS
        self._last_seen[key] = SeenDeletion(entry_id, count)
        return new


def _avatar_url(user: discord.abc.User) -> str | None:
    return None if user.avatar is None else user.avatar.url


class MessageReplayService:
    def __init__(self, guild_settings: GuildSettingsService, messages: ReplayMessageMemoryRepository, discord_client: discord.Client):
        self._guild_settings = guild_settings
        self._messages = messages
        self._deletions = ModeratorDeletionTracker()
        self._discord = discord_client

    def remember(self, message: discord.Message) -> None:
        """Keeps the message if it was written by one of the replay users registered for its guild."""
        if message.guild is None:
            return
        if message.author.id not in self._guild_settings.get(message.guild.id).replay_user_ids:
            return
        self._messages.remember(
            message.id,
            StoredMessage(
                channel_id=message.channel.id,
                author_name=message.author.name,
                author_avatar_url=_avatar_url(message.author),
                author_id=message.author.id,
                content=message.content,
                timestamp=message.created_at,
                attachments=[StoredAttachment(attachment.url, (attachment.content_type or "").startswith("image/")) for attachment in message.attachments],
            ),
        )

    def forget(self, message_ids: list[int]) -> None:
        """Drops these messages, so that their deletion isn't replayed: a bulk delete (which always needs
        Manage Messages and is always logged, so is a moderator's), or "bot get them"."""
        for message_id in message_ids:
            self._messages.forget(message_id)

    async def replay_if_self_deleted(self, message_id: int, guild_id: int | None) -> None:
        """Called for every deleted message. If it was a replay user's that we kept, reposts it in the same
        channel unless the audit log shows a moderator deleted it."""
        stored = self._messages.take(message_id)
        if stored is None or (not stored.content and not stored.attachments):
            return
        # Outside a guild there's no audit log to consult. Only guild messages are ever kept, so this is
        # just a guard rather than a real case.
        if guild_id is None:
            return

        await asyncio.sleep(AUDIT_LOG_LOOKUP_DELAY)
        try:
            by_moderator = await self._deleted_by_moderator(DeletionKey(guild_id, stored.author_id, stored.channel_id))
        except Exception as e:
            log.warning("Couldn't read audit log to see who deleted message %s (%s); replaying it anyway", message_id, e)
            await self.replay(stored)
            return

        if by_moderator:
            log.info("Not replaying deleted message %s: audit log shows a moderator deleted it", message_id)
        else:
            await self.replay(stored)

    async def _deleted_by_moderator(self, key: DeletionKey) -> bool:
        """Whether the audit log shows a recent MESSAGE_DELETE of the author's messages in the channel that we
        haven't already accounted for. A user deleting their own message never produces an audit log entry,
        so "an entry exists" is taken to mean a moderator did it."""
        guild = self._discord.get_guild(key.guild_id) or await self._discord.fetch_guild(key.guild_id)

        # Entries come back newest first; take the most recent deletion of one of the author's messages in
        # this channel.
        found = None
        async for entry in guild.audit_logs(limit=AUDIT_LOG_ENTRIES, action=discord.AuditLogAction.message_delete):
            extra: Any = entry.extra
            target = entry.target
            if target is not None and target.id == key.author_id and extra is not None and extra.channel.id == key.channel_id:
                found = entry
                break
        if found is None:
            # No record of anyone but the author ever deleting their messages here.
            return False

        count = getattr(found.extra, "count", None) or 1
        age_secs = (datetime.now(UTC) - found.created_at).total_seconds()
        return self._deletions.is_new_deletion(key, found.id, count, age_secs)

    async def replay(self, stored: StoredMessage) -> None:
        """Reposts `stored` in its channel, pinging nobody the original mentioned (not even @everyone)."""
        channel = self._discord.get_partial_messageable(stored.channel_id)
        try:
            await channel.send(embed=to_discord_embed(replay_embed(stored)), allowed_mentions=NO_MENTIONS)
        except Exception as e:
            log.exception("Failed to replay deleted message in %s: %s", stored.channel_id, e)
            return
        log.info("Replayed a message %s deleted in %s", stored.author_name, stored.channel_id)

    async def post_sample_replay(self, channel_id: int, author: discord.abc.User, content: str) -> None:
        """Posts the replay embed for `content` as if `author` had written and then deleted it: a preview of
        the rendering only, without the audit log check or the message buffer."""
        await self.replay(StoredMessage(channel_id, author.name, _avatar_url(author), author.id, content, datetime.now(UTC)))
