"""Personal officer channels: a private text channel for each trial or raider, seen only by them and the
officers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .db import from_db, from_db_opt, rows_affected, to_db
from .guild_settings import GuildSettings

if TYPE_CHECKING:
    import asyncpg
    import discord

    from .bot import GulpBot

log = logging.getLogger(__name__)

CHANNEL_TOPIC_PREFIX = "Personal officer channel for"


async def handle_role_update(bot: GulpBot, member: discord.Member) -> None:
    """If `member` currently has the trial or raider role (and isn't themselves an officer), creates a
    private text channel for them under the personal officer channels category, visible only to them and
    the officer role. No-ops if the `personal_officer_channels` table already has a row for them, whether
    the bot created that channel or an administrator registered an existing one.

    Deliberately doesn't look at what changed: idempotency comes entirely from the table, so an unrelated
    member update (nickname, avatar) of someone who already has a channel does nothing."""
    import discord

    from .helper import role_ids

    guild = member.guild
    settings = bot.guild_settings.get(guild.id)
    member_roles = role_ids(member)

    if not has_trial_or_raider_role(settings, member_roles):
        return

    # The feature needs both of these registered for the member's guild; without them it's off.
    category_id, officer_role_id = settings.personal_officer_category_id, settings.mod_role_id
    if category_id is None or officer_role_id is None:
        return
    if officer_role_id in member_roles:
        return

    # Claim the member in the table before touching Discord. The insert is atomic, so of any number of
    # events for the same member racing each other, exactly one gets to create a channel; the rest see
    # the existing row and stop here.
    try:
        if not await claim(bot.db, guild.id, member.id):
            return
    except Exception as e:
        log.error("Failed to check for an existing personal officer channel for %s: %s", member.name, e)
        return

    try:
        officer_role = guild.get_role(officer_role_id) or next((role for role in await guild.fetch_roles() if role.id == officer_role_id), None)
        if officer_role is None:
            raise RuntimeError(f"the officer role {officer_role_id} doesn't exist in the server")
        category = guild.get_channel(category_id) or await guild.fetch_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError(f"channel {category_id} is not a category")

        allowed = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await guild.create_text_channel(
            channel_name_for(member),
            category=category,
            topic=f"{topic_marker_for(member.id)} — visible only to them and officers.",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                officer_role: allowed,
                member: allowed,
            },
        )
    except Exception as e:
        log.error("Failed to create personal officer channel for %s: %s", member.name, e)
        # Give the claim back so a later member update can try again.
        try:
            await unregister(bot.db, guild.id, member.id)
        except Exception as e:
            log.error("Failed to release the personal officer channel claim for %s: %s", member.name, e)
        return

    log.info("Created personal officer channel #%s for %s", channel.name, member.name)
    try:
        await register(bot.db, guild.id, member.id, channel.id)
    except Exception as e:
        # The claim row is still there, so this can't lead to a duplicate channel; the row just doesn't
        # say which channel it is.
        log.error("Created personal officer channel #%s for %s but failed to record it: %s", channel.name, member.name, e)


async def claim(db: asyncpg.Pool, guild_id: int, user_id: int) -> bool:
    """Inserts the row for this member if they don't have one. Returns whether it was inserted, i.e.
    whether the caller now owns creating their channel."""
    status = await db.execute(
        "INSERT INTO personal_officer_channels (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        to_db(guild_id),
        to_db(user_id),
    )
    return rows_affected(status) == 1


async def register(db: asyncpg.Pool, guild_id: int, user_id: int, channel_id: int) -> None:
    """Records `channel_id` as this member's personal officer channel, replacing whatever was recorded
    before. Used both after the bot creates a channel and when an administrator registers one that
    already existed."""
    await db.execute(
        "INSERT INTO personal_officer_channels (guild_id, user_id, channel_id) VALUES ($1, $2, $3) "
        "ON CONFLICT (guild_id, user_id) DO UPDATE SET channel_id = EXCLUDED.channel_id",
        to_db(guild_id),
        to_db(user_id),
        to_db(channel_id),
    )


async def unregister(db: asyncpg.Pool, guild_id: int, user_id: int) -> bool:
    """Forgets this member's personal officer channel, so the bot will create one for them again. Returns
    whether they had a row."""
    status = await db.execute("DELETE FROM personal_officer_channels WHERE guild_id = $1 AND user_id = $2", to_db(guild_id), to_db(user_id))
    return rows_affected(status) == 1


async def list_registered(db: asyncpg.Pool, guild_id: int) -> list[tuple[int, int | None]]:
    """Every member of `guild_id` with a row, oldest first. The channel is `None` for a row whose channel
    was never recorded (see `handle_role_update`)."""
    rows = await db.fetch(
        "SELECT user_id, channel_id FROM personal_officer_channels WHERE guild_id = $1 ORDER BY created_at, user_id",
        to_db(guild_id),
    )
    return [(from_db(row["user_id"]), from_db_opt(row["channel_id"])) for row in rows]


def has_trial_or_raider_role(settings: GuildSettings, member_roles: list[int]) -> bool:
    return any(role is not None and role in member_roles for role in (settings.trial_role_id, settings.raider_role_id))


def topic_marker_for(user_id: int) -> str:
    return f"{CHANNEL_TOPIC_PREFIX} <@{user_id}>"


def channel_name_for(member: discord.Member) -> str:
    return channel_name(member.nick or member.name)


def channel_name(base: str) -> str:
    """A channel name from a member's name: lowercase ASCII letters and digits, anything else a dash."""
    sanitized = "".join(char if char.isascii() and char.isalnum() else "-" for char in base.lower())
    return sanitized.strip("-") or "member"
