"""What `/ai status` and `/ai check` show the developers."""

from __future__ import annotations

from ..schema.ai_schema import ApiFailure, Finding
from ..schema.guild_settings_schema import AiSettings
from .ai_usage_mapper import describe_think


def status_text(
    settings: AiSettings,
    model: str,
    configured_model: str,
    think: bool | None,
    configured_think: bool | None,
    answered: int,
    limit: int,
    failure_total: int,
    failures: list[ApiFailure],
) -> str:
    """`/ai status`: whether the AI features are on, what they run on, and what has failed lately."""
    if settings.disabled:
        state = "**off**: nobody can use them"
    elif settings.maintenance:
        state = "**on, in maintenance mode**: only Officers and Developers can use them"
    else:
        state = "**on**"
    model_note = f"set with `/ai model`; the bot was configured with `{configured_model}`" if settings.model is not None else "as configured"
    think_note = f"set with `/ai think`; the bot was configured with {describe_think(configured_think)}" if settings.think is not None else "as configured"
    text = (
        f"**AI features**\nState: {state}\nModel: `{model}`, runs locally, through Ollama ({model_note})\n"
        f"Thinking: {describe_think(think)} ({think_note})\n"
        f"Requests answered in the last 24 hours: {answered} of {limit}\n"
    )
    if not failures:
        return text + "No failures since the bot started."
    text += f"Failures since the bot started: {failure_total}. The latest:"
    return text + "".join(f"\n• <t:{failure.at_unix}:R>: {failure.detail}" for failure in failures)


def check_text(findings: list[tuple[Finding, str]]) -> str:
    """`/ai check`: one line per finding, then how many need a look."""
    problems = sum(1 for finding, _ in findings if finding is not Finding.GOOD)
    lines = "\n".join(f"{finding.value} {text}" for finding, text in findings)
    summary = "**Nothing to fix.**" if problems == 0 else f"**{problems} thing(s) to look at.**"
    return f"**AI features check**\n{lines}\n\n{summary}"
