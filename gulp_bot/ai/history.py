"""What the assistant remembers and who may ask it. No Discord in here, so it can be tested without it;
channels and users are their numeric IDs, and times are `time.monotonic()` seconds."""

from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass


class Role(enum.Enum):
    """Who said a turn, named as the API names them."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class Turn:
    role: Role
    content: str

    @classmethod
    def user(cls, content: str) -> Turn:
        return cls(Role.USER, content)

    @classmethod
    def assistant(cls, content: str) -> Turn:
        return cls(Role.ASSISTANT, content)


@dataclass
class _Stored:
    """A turn, and the access level of the member whose exchange it belongs to."""

    turn: Turn
    level: int


@dataclass
class _Conversation:
    turns: deque[_Stored]
    last_used: float


class History:
    """The recent conversation of each channel. Only complete exchanges (a question and its answer) are
    stored, so what is sent to the model always alternates user, assistant, user, ... and starts with
    the user. Kept in memory only: a restart forgets it.

    A channel is shared by members of different access levels, so each exchange is remembered with the
    level of whoever asked, and is shown only to members of that level or higher. Otherwise something an
    Officer was told could be repeated to a raider who asks "what did you just say?".
    """

    def __init__(self, max_turns: int, forget_after: float):
        """Keeps the last `max_turns` turns of a channel, and forgets a channel that has been quiet for
        `forget_after` seconds: an hours-old chat is no use as context, but is billed on every request."""
        self._channels: dict[int, _Conversation] = {}
        self._max_turns = max_turns
        self._forget_after = forget_after

    def snapshot(self, channel: int, level: int, now: float) -> list[Turn]:
        """The channel's remembered turns that a member of access `level` may see, oldest first."""
        self._forget_stale(now)
        conversation = self._channels.get(channel)
        if conversation is None:
            return []
        return [stored.turn for stored in conversation.turns if stored.level <= level]

    def record_exchange(self, channel: int, question: str, answer: str, level: int, now: float) -> None:
        """Remembers a question, asked by a member of access `level`, and the answer it got."""
        self._forget_stale(now)
        conversation = self._channels.setdefault(channel, _Conversation(deque(), now))

        conversation.turns.append(_Stored(Turn.user(question), level))
        conversation.turns.append(_Stored(Turn.assistant(answer), level))
        conversation.last_used = now

        while len(conversation.turns) > self._max_turns:
            conversation.turns.popleft()
        # The cut may have left an answer first; the API wants the user to speak first.
        while conversation.turns and conversation.turns[0].turn.role is Role.ASSISTANT:
            conversation.turns.popleft()

    def _forget_stale(self, now: float) -> None:
        self._channels = {channel: conversation for channel, conversation in self._channels.items() if now - conversation.last_used < self._forget_after}


class Answers:
    """The IDs of the last messages the assistant answered with, so that a member who replies to one of
    them is taken to be talking to it. Bounded: the oldest are forgotten first."""

    def __init__(self, max_kept: int):
        self._max = max_kept
        self._ids: deque[int] = deque()

    def remember(self, message_id: int) -> None:
        if message_id in self._ids:
            return
        self._ids.append(message_id)
        while len(self._ids) > self._max:
            self._ids.popleft()

    def contains(self, message_id: int) -> bool:
        return message_id in self._ids


class RateLimiter:
    """At most `max` requests per user in any `window` seconds."""

    def __init__(self, max_requests: int, window: float):
        self._max = max_requests
        self._window = window
        self.requests: dict[int, deque[float]] = {}

    def check(self, user: int, now: float) -> float | None:
        """Counts a request by `user` and returns `None`, or, if they may not ask now, returns how many
        seconds until they may (and counts nothing)."""
        # Other users' old requests are dropped too, so the map doesn't grow without bound.
        for times in self.requests.values():
            while times and now - times[0] >= self._window:
                times.popleft()
        self.requests = {key: times for key, times in self.requests.items() if times}

        times = self.requests.setdefault(user, deque())
        if len(times) >= self._max:
            oldest = times[0] if times else now
            if not times:
                del self.requests[user]
            return max(0.0, self._window - max(0.0, now - oldest))

        times.append(now)
        return None

    def count(self, user: int, now: float) -> int:
        """How many requests of `user` are within the window now."""
        return sum(1 for time in self.requests.get(user, ()) if now - time < self._window)

    @property
    def max(self) -> int:
        """How many requests a user may make in the window."""
        return self._max

    def clear(self, user: int, now: float) -> int:
        """Forgets every request counted for `user`, so they may ask a full allowance again. Returns how
        many were within the window."""
        counted = self.count(user, now)
        self.requests.pop(user, None)
        return counted

    def undo(self, user: int) -> None:
        """Takes back the request `check` last counted for `user`: for one that got no answer through no
        fault of theirs, or that another limit refused after this one had counted it."""
        times = self.requests.get(user)
        if times:
            times.pop()
            if not times:
                del self.requests[user]
