"""What the AI features keep track of: the tokens used and the latest failures. All of it lives in memory: a
restart forgets it. Times are `time.monotonic()` seconds unless they say otherwise."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..core.text import single_line, truncate_chars
from ..schema.ai_schema import ApiFailure, Tokens

# The longest explanation of a failure that is kept.
MAX_FAILURE_DETAIL_CHARS = 200


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
