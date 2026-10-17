"""What the AI assistant needs of a model, whichever serves it."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..schema.ai_schema import ModelCheck, ModelReply, Turn


class ChatModelClient(Protocol):
    """A model server: Claude through the Anthropic API, or a model on the guild's own hardware."""

    @property
    def is_local(self) -> bool:
        """Whether the model runs on the guild's own hardware rather than the Anthropic API."""
        ...

    async def complete(self, model: str, system: str, turns: Sequence[Turn], effort: str | None) -> ModelReply:
        """One request to `model` with `turns` as the conversation. Raises `Unavailable` if there is no
        answer."""
        ...

    async def check(self, model: str) -> ModelCheck:
        """Whether the server can be reached with the bot's credentials, and has `model`. Costs no tokens."""
        ...

    async def installed_models(self) -> list[str]:
        """The models a developer may pick from on a local server; nothing for the Anthropic API."""
        ...
