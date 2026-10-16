"""An announcement a guild's officers scheduled."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.cron import Schedule


@dataclass(frozen=True)
class Announcement:
    """`message` is posted to `channel_id` whenever `schedule` fires."""

    id: int
    guild_id: int
    channel_id: int
    schedule: Schedule
    message: str
    created_by: int


@dataclass(frozen=True)
class NewAnnouncement:
    """An announcement an officer is scheduling, before it has an ID."""

    guild_id: int
    channel_id: int
    schedule: Schedule
    message: str
    created_by: int
