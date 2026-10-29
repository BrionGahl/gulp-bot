"""What the AI assistant remembers. All of it lives in memory: a restart forgets it. No Discord in here;
channels, users and messages are their numeric IDs, and times are `time.monotonic()` seconds unless they
say otherwise."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..core.text import single_line, truncate_chars
from ..schema.ai_schema import ApiFailure, Tokens, Turn, TurnRole

# The longest explanation of a failure that is kept.
MAX_FAILURE_DETAIL_CHARS = 200


@dataclass
class _StoredTurn:
    """A turn, and the access level of the member whose exchange it belongs to."""

    turn: Turn
    level: int


@dataclass
class _Conversation:
    turns: deque[_StoredTurn]
    last_used: float


class ConversationHistoryRepository:
    """The recent conversation of each channel. Only complete exchanges (a question and its answer) are
    stored, so what is sent to the model always alternates user, assistant, user, ... and starts with the
    user.

    A channel is shared by members of different access levels, so each exchange is remembered with the
    level of whoever asked, and is shown only to members of that level or higher. Otherwise something an
    Officer was told could be repeated to a raider who asks "what did you just say?".
    """

    def __init__(self, max_turns: int, forget_after: float):
        """Keeps the last `max_turns` turns of a channel, and forgets a channel that has been quiet for
        `forget_after` seconds: an hours-old chat is no use as context, but is read again on every request."""
        self._channels: dict[int, _Conversation] = {}
        self._max_turns = max_turns
        self._forget_after = forget_after

    def turns_visible_to(self, channel: int, level: int, now: float) -> list[Turn]:
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

        conversation.turns.append(_StoredTurn(Turn.user(question), level))
        conversation.turns.append(_StoredTurn(Turn.assistant(answer), level))
        conversation.last_used = now

        while len(conversation.turns) > self._max_turns:
            conversation.turns.popleft()
        # The cut may have left an answer first; the API wants the user to speak first.
        while conversation.turns and conversation.turns[0].turn.role is TurnRole.ASSISTANT:
            conversation.turns.popleft()

    def _forget_stale(self, now: float) -> None:
        self._channels = {channel: conversation for channel, conversation in self._channels.items() if now - conversation.last_used < self._forget_after}


class AnswerMessageRepository:
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


@dataclass
class _UsageEntry:
    at: float
    model: str
    input: int
    output: int


class TokenUsageRepository:
    """The tokens used by each model, since the bot started and in the last `window` seconds."""

    def __init__(self, window: float):
        self._window = window
        self._totals: dict[str, Tokens] = {}
        self._recent: deque[_UsageEntry] = deque()

    def record(self, model: str, input_tokens: int, output_tokens: int, now: float) -> None:
        """Counts a request to `model` that used `input_tokens` and `output_tokens`."""
        self._totals.setdefault(model, Tokens()).add(input_tokens, output_tokens)
        while self._recent and now - self._recent[0].at >= self._window:
            self._recent.popleft()
        self._recent.append(_UsageEntry(now, model, input_tokens, output_tokens))

    def since_start(self) -> list[tuple[str, Tokens]]:
        """Everything since the bot started, by model name."""
        return _by_model_name({model: Tokens(tokens.requests, tokens.input, tokens.output) for model, tokens in self._totals.items()})

    def recent(self, now: float) -> list[tuple[str, Tokens]]:
        """The last `window`, by model name."""
        by_model: dict[str, Tokens] = {}
        for entry in self._recent:
            if now - entry.at < self._window:
                by_model.setdefault(entry.model, Tokens()).add(entry.input, entry.output)
        return _by_model_name(by_model)


def _by_model_name(by_model: dict[str, Tokens]) -> list[tuple[str, Tokens]]:
    return sorted(by_model.items(), key=lambda row: row[0])


class ApiFailureRepository:
    """The last few requests that got no answer, and how many there have been."""

    def __init__(self, max_kept: int):
        self._max = max_kept
        self._total = 0
        self._items: deque[ApiFailure] = deque()

    def record(self, at_unix: int, detail: str) -> None:
        self._total += 1
        self._items.append(ApiFailure(at_unix, truncate_chars(single_line(detail), MAX_FAILURE_DETAIL_CHARS)))
        while len(self._items) > self._max:
            self._items.popleft()

    @property
    def total(self) -> int:
        """How many failures there have been since the bot started, kept or not."""
        return self._total

    def latest(self) -> list[ApiFailure]:
        """The kept failures, the latest first."""
        return list(reversed(self._items))
