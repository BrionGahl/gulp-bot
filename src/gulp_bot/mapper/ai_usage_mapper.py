"""What the assistant has used, as the lines `/ai usage` shows."""

from __future__ import annotations

from ..schema.ai_schema import Tokens
from ..service.ai_model_catalog_service import cost_of, is_local_label


def usage_lines(rows: list[tuple[str, Tokens]]) -> str:
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

        model_cost = cost_of(tokens, model)
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


def format_price(value: float) -> str:
    """A price per million tokens as the Rust bot printed it: `2`, `0.1`."""
    return str(int(value)) if value == int(value) else repr(value)


def describe_effort(effort: str | None) -> str:
    """An effort as a developer reads it."""
    return "none sent" if effort is None else f"`{effort}`"
