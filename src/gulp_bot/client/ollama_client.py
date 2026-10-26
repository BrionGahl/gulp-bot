"""A model on the guild's own hardware, served by Ollama. Nothing leaves the machine."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from ..core.http_client import HttpClient, RequestFailed, RequestTimeout, status_text
from ..exception.ai_exception import ModelServerUnreachable, unavailable
from ..mapper.ai_model_mapper import api_error_message, ollama_reply, ollama_request
from ..schema.ai_schema import CheckOk, ModelCheck, ModelReply, Turn, Unreachable

# Gives up on a local model after this long. Longer than for the API: the first question after a while
# loads the model into the GPU, a question waits for the one before it to finish, and a model that thinks
# may write a few thousand tokens before it answers.
REQUEST_TIMEOUT = 240.0

# Gives up on asking which models are installed after this long, when a developer checks the server.
CHECK_TIMEOUT = 15.0

# How long listing the models may take while a developer types a model name.
LIST_TIMEOUT = 2.0


def is_installed(installed: Sequence[str], model: str) -> bool:
    """Whether `model` is among the `installed` ones. Ollama names a model given without a tag
    `name:latest`, so `qwen3.5` is installed if `qwen3.5:latest` is."""
    return any(name == model or (":" not in model and name == f"{model}:latest") for name in installed)


class OllamaClient:
    def __init__(self, http: HttpClient, url: str):
        """`url` is where the Ollama server listens, without a trailing slash."""
        self._http = http
        self.url = url
        # The model answers one question at a time on its GPU; the others wait here for their turn rather
        # than all loading the GPU at once.
        self._queue = asyncio.Semaphore(1)

    @property
    def is_local(self) -> bool:
        return True

    async def complete(self, model: str, system: str, turns: Sequence[Turn], effort: str | None, think: bool | None) -> ModelReply:
        """`effort` is ignored: a local model has no such setting. `think` is sent as is: `None` leaves
        thinking to the model."""
        request = ollama_request(model, system, turns, think)

        # Held until the answer is in, so the GPU works on one question at a time.
        async with self._queue:
            try:
                response = await self._http.request("POST", f"{self.url}/api/chat", timeout=REQUEST_TIMEOUT, json=request)
            except (RequestTimeout, RequestFailed) as e:
                raise unavailable(f"the request to the local model failed: {e}") from None

        if not response.ok:
            raise unavailable(f"the local model returned {status_text(response.status)}: {api_error_message(response.body)}")
        try:
            body = json.loads(response.body)
        except ValueError as e:
            raise unavailable(f"the response couldn't be parsed: {e}") from None
        return ollama_reply(body)

    async def check(self, model: str) -> ModelCheck:
        try:
            installed = await self._models(CHECK_TIMEOUT)
        except ModelServerUnreachable as e:
            return Unreachable(str(e))
        return CheckOk(is_installed(installed, model))

    async def installed_models(self) -> list[str]:
        """Nothing if the server doesn't answer in time: a developer is typing."""
        try:
            return await self._models(LIST_TIMEOUT)
        except ModelServerUnreachable:
            return []

    async def _models(self, timeout: float) -> list[str]:
        """The names of the models the server has installed."""
        try:
            response = await self._http.request("GET", f"{self.url}/api/tags", timeout=timeout)
        except (RequestTimeout, RequestFailed) as e:
            raise ModelServerUnreachable(f"the request failed: {e}") from None
        if not response.ok:
            raise ModelServerUnreachable(f"the server returned {status_text(response.status)}: {api_error_message(response.body)}")
        try:
            models = json.loads(response.body).get("models") or []
            return [str(model["name"]) for model in models]
        except (ValueError, AttributeError, KeyError, TypeError) as e:
            raise ModelServerUnreachable(f"the list of models couldn't be parsed: {e}") from None
