"""What the AI assistant needs of the server that runs its model."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..schema.ai_schema import ModelCheck, ModelReply, Turn


class ChatModelClient(Protocol):
    """A model server on the guild's own hardware."""

    async def complete(self, model: str, system: str, turns: Sequence[Turn], think: bool | None) -> ModelReply:
        """One request to `model` with `turns` as the conversation; `think` says whether it thinks first
        (`None` sends nothing). Raises `Unavailable` if there is no answer."""
        ...

    async def check(self, model: str) -> ModelCheck:
        """Whether the server can be reached, and has `model`."""
        ...

    async def installed_models(self) -> list[str]:
        """The models a developer may pick from."""
        ...
