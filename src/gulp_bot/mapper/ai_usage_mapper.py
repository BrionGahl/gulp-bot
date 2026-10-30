"""What the model has used, as the lines `/ai usage` shows."""

from __future__ import annotations

from ..schema.ai_schema import Tokens


def usage_lines(rows: list[tuple[str, Tokens]]) -> str:
    """The usage `rows` as lines for a message: one per model, then a total if there are several."""
    if not rows:
        return "No requests."

    lines = []
    total = Tokens()

    for model, tokens in rows:
        total.requests += tokens.requests
        total.input += tokens.input
        total.output += tokens.output
        lines.append(f"`{model}`: {_requests(tokens.requests)}, {format_count(tokens.input)} in / {format_count(tokens.output)} out tokens")

    if len(rows) > 1:
        lines.append(f"**Total**: {_requests(total.requests)}, {format_count(total.input)} in / {format_count(total.output)} out tokens")
    return "\n".join(lines)


def _requests(count: int) -> str:
    """`1 request`, `2 requests`."""
    return f"{format_count(count)} request{'' if count == 1 else 's'}"


def format_count(count: int) -> str:
    """`1234567` as `1,234,567`."""
    return f"{count:,}"


def describe_think(think: bool | None) -> str:
    """Whether the model thinks, as a developer reads it."""
    return "left to the model" if think is None else ("**on**" if think else "**off**")
