"""Personal officer channels: a private text channel for each trial or raider."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegisteredOfficerChannel:
    """A member who has a personal officer channel. `channel_id` is `None` for a channel the bot claimed
    but never managed to record."""

    user_id: int
    channel_id: int | None
