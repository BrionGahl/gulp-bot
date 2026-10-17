"""The models the assistant knows the price of, what a number of tokens costs, and what counts as a model
name. No Discord in here."""

from __future__ import annotations

from ..exception.ai_exception import ModelNameProblem
from ..schema.ai_schema import Tokens

# Models whose price is known, as the API names them. Offered when a developer picks a model.
KNOWN_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]

# US dollars per million tokens, `(model, input, output)`, from Anthropic's price list. Haiku 5.5 costs more
# for a prompt of over 100,000 tokens; the assistant's prompts are far below that.
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


def cost_of(tokens: Tokens, model: str) -> float | None:
    """What `tokens` cost with `model`, in US dollars, or `None` if its price isn't known."""
    price = price_per_million(model)
    if price is None:
        return None
    input_price, output_price = price
    return (tokens.input * input_price + tokens.output * output_price) / 1_000_000.0


def normalize_model_name(name: str) -> str | None:
    """What a developer typed as a model: `None` for "default" (the model the bot was configured with), or
    the name, if it looks like one; otherwise raises `ModelNameProblem` saying what is wrong. Whether the
    API knows it is checked separately."""
    name = name.strip()
    if name.lower() == "default":
        return None
    if not name or len(name) > 64:
        raise ModelNameProblem("A model name is 1 to 64 characters long.")
    # `:` for a local model's tag (`qwen3.5:9b`), `/` for one pulled from a registry (`hf.co/...`).
    if not all((char.isascii() and char.isalnum()) or char in "-._:/" for char in name):
        raise ModelNameProblem("A model name has only letters, digits, `-`, `.`, `_`, `:` and `/`.")
    return name
