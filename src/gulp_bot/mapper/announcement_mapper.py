"""`announcements` rows into `Announcement`, and announcements into what is posted and listed."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ..core.database import from_bigint
from ..core.embed import MAX_LIST_CHARS
from ..core.schedule_parsing import describe_schedule, next_fire_discord_timestamp, parse_stored_schedule
from ..core.text import limited_lines
from ..schema.announcement_schema import Announcement
from ..schema.guild_settings_schema import GuildSettings

log = logging.getLogger(__name__)

# How much of each message `/announcement list` shows.
PREVIEW_CHARS = 80


def announcement_from_row(row: Mapping[str, Any]) -> Announcement | None:
    """One row. `None` for a row whose cron expression can't be read: expressions are validated before
    they're stored, so that only happens if the row was edited by hand, and skipping it beats taking every
    other announcement down with it."""
    schedule = parse_stored_schedule(row["cron"])
    if schedule is None:
        log.warning("Skipping announcement %s: unreadable cron expression `%s`", row["id"], row["cron"])
        return None
    return Announcement(
        id=row["id"],
        guild_id=from_bigint(row["guild_id"]),
        channel_id=from_bigint(row["channel_id"]),
        schedule=schedule,
        message=row["message"],
        created_by=from_bigint(row["created_by"]),
    )


def announcements_from_rows(rows: list[Any]) -> list[Announcement]:
    return [announcement for announcement in map(announcement_from_row, rows) if announcement is not None]


def render_announcement(message: str, settings: GuildSettings) -> str:
    """`message` as it is posted: the `@Raider`/`@Trial` placeholders become mentions of the roles the
    guild registered. A role the guild hasn't registered is left as its plain-text placeholder."""
    for placeholder, role_id in (("@Raider", settings.raider_role_id), ("@Trial", settings.trial_role_id)):
        if role_id is not None:
            message = message.replace(placeholder, f"<@&{role_id}>")
    return message


def announcement_preview(message: str) -> str:
    """The start of `message` on one line, with formatting neutralised so it reads as a quote of the text
    rather than rendering (or pinging) inside the list."""
    flattened = " ".join(message.split()).replace("`", "'")
    shown = flattened[:PREVIEW_CHARS]
    if len(flattened) > PREVIEW_CHARS:
        shown += "…"
    return f"`{shown}`"


def announcement_list_description(announcements: list[Announcement]) -> str:
    """The `/announcement list` embed's description: one entry per announcement, cut short to fit."""
    entries = [
        f"**#{entry.id}** — <#{entry.channel_id}> — {describe_schedule(entry.schedule)} — next {next_fire_discord_timestamp(entry.schedule, 'R', never='never')}\n"
        f"by <@{entry.created_by}>: {announcement_preview(entry.message)}\n\n"
        for entry in announcements
    ]
    return limited_lines(entries, MAX_LIST_CHARS)
