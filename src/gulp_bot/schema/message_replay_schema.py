"""Delete-and-replay: the messages kept so that a deleted one can be reposted."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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
    """The newest MESSAGE_DELETE audit log entry already accounted for."""

    entry_id: int
    count: int


@dataclass(frozen=True)
class DeletionKey:
    """Whose deletions an audit log entry is about: one author's messages, in one channel of one guild."""

    guild_id: int
    author_id: int
    channel_id: int
