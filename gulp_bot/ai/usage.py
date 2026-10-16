"""What the assistant has used and where it has failed, for the developers' `/ai` commands. No Discord
in here, so it can be tested without it. All of it lives in memory: a restart starts the counts from
zero, and the Anthropic Console stays the authority on what was really billed."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .text import single_line, truncate_chars

# Models whose price is known, as the API names them. Offered when a developer picks a model.
KNOWN_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]

# US dollars per million tokens, `(model, input, output)`, from Anthropic's price list. Haiku 5.5 costs
# more for a prompt of over 100,000 tokens; the assistant's prompts are far below that.
PRICES = [
    ("claude-haiku-5-5", 0.10, 0.50),
    ("claude-sonnet-5-5", 2.0, 10.0),
    ("claude-opus-5-5", 4.0, 20.0),
    ("claude-fable-5-1", 10.0, 50.0),
    ("claude-mythos-5-1", 10.0, 50.0),
]

# Put after the name of a model that runs on the guild's own hardware, in the usage counts.
LOCAL_SUFFIX = " (local)"


def local_label(model: str) -> str:
    """How a local model's usage is counted: under its name with ` (local)`, which costs nothing."""
    return f"{model}{LOCAL_SUFFIX}"


def is_local_label(label: str) -> bool:
    """Whether `label` is a local model's, as `local_label` makes them."""
    return label.endswith(LOCAL_SUFFIX)


def price_per_million(model: str) -> tuple[float, float] | None:
    """The `(input, output)` price per million tokens of `model`, or `None` if it isn't known. A dated
    version of a model (`claude-sonnet-5-5-20261001`) costs what the model does, and a local one
    (`local_label`) costs nothing."""
    if is_local_label(model):
        return (0.0, 0.0)
    for name, input_price, output_price in PRICES:
        if model == name or (model.startswith(name) and model[len(name) :].startswith("-")):
            return (input_price, output_price)
    return None


@dataclass
class Tokens:
    """What a number of requests used."""

    requests: int = 0
    input: int = 0
    output: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.requests += 1
        self.input += input_tokens
        self.output += output_tokens

    def cost(self, model: str) -> float | None:
        """What these tokens cost with `model`, in US dollars, or `None` if its price isn't known."""
        price = price_per_million(model)
        if price is None:
            return None
        input_price, output_price = price
        return (self.input * input_price + self.output * output_price) / 1_000_000.0


@dataclass
class _Entry:
    at: float
    model: str
    input: int
    output: int


class UsageLog:
    """The tokens used by each model, since the bot started and in the last `window` seconds."""

    def __init__(self, window: float):
        self._window = window
        self._totals: dict[str, Tokens] = {}
        self._recent: deque[_Entry] = deque()

    def record(self, model: str, input_tokens: int, output_tokens: int, now: float) -> None:
        """Counts a request to `model` that used `input_tokens` and `output_tokens`."""
        self._totals.setdefault(model, Tokens()).add(input_tokens, output_tokens)
        while self._recent and now - self._recent[0].at >= self._window:
            self._recent.popleft()
        self._recent.append(_Entry(now, model, input_tokens, output_tokens))

    def since_start(self) -> list[tuple[str, Tokens]]:
        """Everything since the bot started, by model name."""
        return _sorted({model: Tokens(tokens.requests, tokens.input, tokens.output) for model, tokens in self._totals.items()})

    def recent(self, now: float) -> list[tuple[str, Tokens]]:
        """The last `window`, by model name."""
        by_model: dict[str, Tokens] = {}
        for entry in self._recent:
            if now - entry.at < self._window:
                by_model.setdefault(entry.model, Tokens()).add(entry.input, entry.output)
        return _sorted(by_model)


def _sorted(by_model: dict[str, Tokens]) -> list[tuple[str, Tokens]]:
    return sorted(by_model.items(), key=lambda row: row[0])


def describe(rows: list[tuple[str, Tokens]]) -> str:
    """The usage `rows` as lines for a message: one per model, then a total if there are several."""
    if not rows:
        return "No requests."

    lines = []
    total = Tokens()
    cost = 0.0
    unpriced = False

    for model, tokens in rows:
        total.requests += tokens.requests
        total.input += tokens.input
        total.output += tokens.output

        model_cost = tokens.cost(model)
        if model_cost is not None and is_local_label(model):
            price = "free, it runs locally"
        elif model_cost is not None:
            cost += model_cost
            price = f"about {format_cost(model_cost)}"
        else:
            unpriced = True
            price = "price unknown"
        lines.append(f"`{model}`: {_requests(tokens.requests)}, {format_count(tokens.input)} in / {format_count(tokens.output)} out tokens, {price}")

    if len(rows) > 1:
        note = " (not counting the models whose price is unknown)" if unpriced else ""
        lines.append(
            f"**Total**: {_requests(total.requests)}, {format_count(total.input)} in / {format_count(total.output)} out tokens, about {format_cost(cost)}{note}"
        )
    return "\n".join(lines)


def _requests(count: int) -> str:
    """`1 request`, `2 requests`."""
    return f"{format_count(count)} request{'' if count == 1 else 's'}"


def format_count(count: int) -> str:
    """`1234567` as `1,234,567`."""
    return f"{count:,}"


def format_cost(usd: float) -> str:
    """US dollars: cents from a dollar up, a fraction of a cent below."""
    return f"${usd:.2f}" if usd >= 1.0 else f"${usd:.4f}"


class ModelNameProblem(ValueError):
    pass


def normalize_model(name: str) -> str | None:
    """What a developer typed as a model: `None` for "default" (the model the bot was configured with),
    or the name, if it looks like one; otherwise raises `ModelNameProblem` saying what is wrong.
    Whether the API knows it is checked separately."""
    name = name.strip()
    if name.lower() == "default":
        return None
    if not name or len(name) > 64:
        raise ModelNameProblem("A model name is 1 to 64 characters long.")
    # `:` for a local model's tag (`qwen3.5:9b`), `/` for one pulled from a registry (`hf.co/...`).
    if not all((char.isascii() and char.isalnum()) or char in "-._:/" for char in name):
        raise ModelNameProblem("A model name has only letters, digits, `-`, `.`, `_`, `:` and `/`.")
    return name


# The longest explanation of a failure that is kept.
MAX_DETAIL_CHARS = 200


@dataclass(frozen=True)
class Failure:
    """A request that got no answer."""

    # When it happened, in Unix seconds.
    at_unix: int
    # What went wrong, on one line.
    detail: str


class FailureLog:
    """The last few failures of the API, and how many there have been."""

    def __init__(self, max_kept: int):
        self._max = max_kept
        self._total = 0
        self._items: deque[Failure] = deque()

    def record(self, at_unix: int, detail: str) -> None:
        self._total += 1
        self._items.append(Failure(at_unix, truncate_chars(single_line(detail), MAX_DETAIL_CHARS)))
        while len(self._items) > self._max:
            self._items.popleft()

    @property
    def total(self) -> int:
        """How many failures there have been since the bot started, kept or not."""
        return self._total

    def latest(self) -> list[Failure]:
        """The kept failures, the latest first."""
        return list(reversed(self._items))
