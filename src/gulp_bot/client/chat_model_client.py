"""What the AI features need of the server that runs their model."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from ..schema.ai_schema import ModelCheck, ModelReply, Turn


class ChatModelClient(Protocol):
    """A model server on the guild's own hardware."""

    async def complete(
        self, model: str, system: str, turns: Sequence[Turn], think: bool | None, temperature: float | None = None, schema: dict[str, Any] | None = None
    ) -> ModelReply:
        """One request to `model` with `turns` as the conversation; `think` says whether it thinks first
        and `temperature` how much it varies its wording (`None` sends nothing, for either). With a JSON
        `schema`, the answer is JSON in that shape. Raises `Unavailable` if there is no answer."""
        ...

    async def check(self, model: str) -> ModelCheck:
        """Whether the server can be reached, and has `model`."""
        ...

    async def installed_models(self) -> list[str]:
        """The models a developer may pick from."""
        ...
