"""Delete-and-replay: a registered member's self-deleted messages are reposted."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import discord

    from .bot import GulpBot

log = logging.getLogger(__name__)

# How many of the replay users' most recent messages to keep so we can repost one if it's deleted. Only
# messages authored by a guild's registered replay users are ever stored, so this stays cheap.
MAX_STORED_MESSAGES = 200

# Discord writes the audit log entry for a moderator deletion a beat after the gateway delivers the
# delete event, so we wait this many seconds before going to look for it.
AUDIT_LOG_LOOKUP_DELAY = 2.0

# On a fresh process we have no previous audit log state to diff against, so we fall back to "is the
# newest matching MESSAGE_DELETE entry recent enough to plausibly be the deletion we're handling?".
# Needs to comfortably cover `AUDIT_LOG_LOOKUP_DELAY` plus Discord's own lag.
COLD_START_RECENT_SECS = 20

# Discord's hard limit on embed description length; the replay is truncated to fit.
MAX_DESCRIPTION_CHARS = 4096

# The purple of the bot's embeds.
EMBED_COLOUR = 0xAF69EE


@dataclass(frozen=True)
class StoredAttachment:
    url: str
    is_image: bool


@dataclass(frozen=True)
class StoredMessage:
    channel_id: int
    author_name: str
    author_avatar_url: str | None
    author_id: int
    content: str
    timestamp: datetime
    attachments: list[StoredAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class SeenDeletion:
    entry_id: int
    count: int


class MessageReplay:
    """Remembers each guild's replay users' recent messages and, when one is deleted, reposts it, unless
    the audit log shows someone other than the author (i.e. a moderator) deleted it."""

    def __init__(self) -> None:
        self._messages: OrderedDict[int, StoredMessage] = OrderedDict()
        # The most recent MESSAGE_DELETE audit log entry we've already seen for each (guild, author,
        # channel). Discord coalesces repeat deletions by the same mod into a single entry (bumping its
        # `count`), so tracking this lets us still recognise each subsequent deletion. It's kept per author
        # and channel because the lookup is: with several replay users, one user's entry must not be
        # compared against another's.
        self._last_seen_deletion: dict[tuple[int, int, int], SeenDeletion] = {}

    def remember(self, message_id: int, message: StoredMessage) -> None:
        self._messages[message_id] = message
        while len(self._messages) > MAX_STORED_MESSAGES:
            self._messages.popitem(last=False)

    def take(self, message_id: int) -> StoredMessage | None:
        return self._messages.pop(message_id, None)

    def forget(self, message_id: int) -> None:
        self._messages.pop(message_id, None)

    def attributable(self, key: tuple[int, int, int], entry_id: int, count: int, age_secs: float) -> bool:
        """Whether the newest audit log entry for `key` (the guild, the author, the channel) is a deletion
        not yet accounted for, and remembers it as seen."""
        previous = self._last_seen_deletion.get(key)
        if previous is not None:
            # Discord coalesces repeat deletions by one mod into a single entry, keeping its id and
            # bumping `count`, so a genuinely new deletion is either a new id or a higher count.
            new = entry_id != previous.entry_id or count > previous.count
        else:
            # Nothing to diff against yet: trust it only if it's recent enough to be this deletion.
            new = age_secs <= COLD_START_RECENT_SECS
        self._last_seen_deletion[key] = SeenDeletion(entry_id, count)
        return new


def _avatar_url(user: discord.abc.User) -> str | None:
    return None if user.avatar is None else user.avatar.url


def remember_message(bot: GulpBot, message: discord.Message) -> None:
    """Stores the message if it was written by one of the replay users registered for the guild it was
    posted in. No-op otherwise."""
    if message.guild is None:
        return
    if message.author.id not in bot.guild_settings.get(message.guild.id).replay_user_ids:
        return

    bot.message_replay.remember(
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


def forget_messages(bot: GulpBot, message_ids: list[int]) -> None:
    """A bulk delete always needs Manage Messages and is always logged, so it's definitionally a moderator
    action: we never replay those, just drop any we were holding."""
    for message_id in message_ids:
        bot.message_replay.forget(message_id)


async def handle_message_delete(bot: GulpBot, message_id: int, guild_id: int | None) -> None:
    """Called for every deleted message. If it was one of a replay user's that we had stored, repost it in
    the same channel unless the audit log shows a moderator deleted it."""
    stored = bot.message_replay.take(message_id)
    if stored is None:
        return
    if not stored.content and not stored.attachments:
        return
    # Outside a guild there's no audit log to consult. We only ever store guild messages in practice, so
    # this is just a guard rather than a real case.
    if guild_id is None:
        return

    await asyncio.sleep(AUDIT_LOG_LOOKUP_DELAY)

    try:
        by_moderator = await deleted_by_moderator(bot, guild_id, stored.channel_id, stored.author_id)
    except Exception as e:
        log.warning("Couldn't read audit log to see who deleted message %s (%s); replaying it anyway", message_id, e)
        await replay(bot, stored)
        return

    if by_moderator:
        log.info("Not replaying deleted message %s: audit log shows a moderator deleted it", message_id)
    else:
        await replay(bot, stored)


async def deleted_by_moderator(bot: GulpBot, guild_id: int, channel_id: int, target: int) -> bool:
    """Whether the audit log shows a recent MESSAGE_DELETE, targeting `target` in `channel_id`, that we
    haven't already accounted for. A user deleting their own message never produces an audit log entry,
    so "an entry exists" is taken to mean a moderator did it."""
    import discord

    guild = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)

    # Entries come back newest first; take the most recent deletion of one of the target's messages in
    # this channel.
    found = None
    async for entry in guild.audit_logs(limit=25, action=discord.AuditLogAction.message_delete):
        extra: Any = entry.extra
        entry_target = entry.target
        if entry_target is not None and entry_target.id == target and extra is not None and extra.channel.id == channel_id:
            found = entry
            break

    if found is None:
        # No record of anyone but the author ever deleting the target's messages here.
        return False

    count = getattr(found.extra, "count", None) or 1
    age_secs = (datetime.now(UTC) - found.created_at).total_seconds()
    return bot.message_replay.attributable((guild_id, target, channel_id), found.id, count, age_secs)


async def render_test_replay(bot: GulpBot, channel_id: int, author: discord.abc.User, content: str) -> None:
    """Posts the delete-and-replay embed for `content` as if `author` had written and then deleted it, in
    `channel_id`. Backs the `/test_replay` debug command: this exercises the embed rendering only, not the
    audit log check or the message buffer."""
    stored = StoredMessage(channel_id, author.name, _avatar_url(author), author.id, content, datetime.now(UTC))
    await replay(bot, stored)


def replay_embed(stored: StoredMessage) -> dict[str, Any]:
    """The embed a deleted message is reposted as."""
    description = stored.content

    # One image attachment gets rendered inline on the embed; everything else (non-images, plus any extra
    # images) is listed as links in the description.
    inline_image = next((index for index, attachment in enumerate(stored.attachments) if attachment.is_image), None)
    links = [attachment.url for index, attachment in enumerate(stored.attachments) if index != inline_image]
    if links:
        if description:
            description += "\n\n"
        description += "\n".join(links)

    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[: MAX_DESCRIPTION_CHARS - 1] + "…"

    author: dict[str, Any] = {"name": f"{stored.author_name} deleted a message"}
    if stored.author_avatar_url is not None:
        author["icon_url"] = stored.author_avatar_url

    embed: dict[str, Any] = {"type": "rich", "author": author, "color": EMBED_COLOUR, "timestamp": stored.timestamp.isoformat()}
    if description:
        embed["description"] = description
    if inline_image is not None:
        embed["image"] = {"url": stored.attachments[inline_image].url}
    return embed


async def replay(bot: GulpBot, stored: StoredMessage) -> None:
    from .helper import NO_MENTIONS, embed_from_dict

    channel = bot.get_partial_messageable(stored.channel_id)
    try:
        # A reposted message must never ping anyone (including @everyone) the original mentioned.
        await channel.send(embed=embed_from_dict(replay_embed(stored)), allowed_mentions=NO_MENTIONS)
    except Exception as e:
        log.error("Failed to replay deleted message in %s: %s", stored.channel_id, e)
        return
    log.info("Replayed a message %s deleted in %s", stored.author_name, stored.channel_id)
