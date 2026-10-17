"""The replay users' recent messages, kept in memory so that a deleted one can be reposted."""

from __future__ import annotations

from collections import OrderedDict

from ..schema.message_replay_schema import StoredMessage

# How many of the replay users' most recent messages to keep. Only messages authored by a guild's
# registered replay users are ever stored, so this stays cheap.
MAX_STORED_MESSAGES = 200


class ReplayMessageMemoryRepository:
    """The latest `MAX_STORED_MESSAGES` messages, by message ID; the oldest are forgotten first."""

    def __init__(self, max_kept: int = MAX_STORED_MESSAGES) -> None:
        self._max = max_kept
        self._messages: OrderedDict[int, StoredMessage] = OrderedDict()

    def remember(self, message_id: int, message: StoredMessage) -> None:
        self._messages[message_id] = message
        while len(self._messages) > self._max:
            self._messages.popitem(last=False)

    def take(self, message_id: int) -> StoredMessage | None:
        """Removes and returns the message, if it is kept."""
        return self._messages.pop(message_id, None)

    def forget(self, message_id: int) -> None:
        self._messages.pop(message_id, None)
