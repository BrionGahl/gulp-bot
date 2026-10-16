"""Claude, through the Anthropic API."""

from __future__ import annotations

import json
from collections.abc import Sequence

from ..core.http_client import HttpClient, RequestFailed, RequestTimeout, status_text
from ..exception.ai_exception import unavailable
from ..mapper.ai_model_mapper import anthropic_reply, anthropic_request, api_error_message
from ..schema.ai_schema import CheckOk, KeyRejected, ModelCheck, ModelReply, Turn, Unreachable

API_URL = "https://api.anthropic.com/v1/messages"
# Lists the models the key may use, without costing a token: how a key is checked.
MODELS_URL = "https://api.anthropic.com/v1/models"
API_VERSION = "2023-06-01"

# Gives up on a request that takes longer than this many seconds.
REQUEST_TIMEOUT = 60.0

# Gives up on checking the key after this long. Short: a developer is waiting for the result.
KEY_CHECK_TIMEOUT = 15.0


class AnthropicClient:
    def __init__(self, http: HttpClient, api_key: str):
        self._http = http
        self._api_key = api_key

    def __repr__(self) -> str:
        # So that the API key can't end up in a log.
        return "AnthropicClient(...)"

    @property
    def is_local(self) -> bool:
        return False

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "anthropic-version": API_VERSION}

    async def complete(self, model: str, system: str, turns: Sequence[Turn], effort: str | None) -> ModelReply:
        try:
            response = await self._http.request(
                "POST", API_URL, timeout=REQUEST_TIMEOUT, headers=self._headers(), json=anthropic_request(model, system, turns, effort)
            )
        except (RequestTimeout, RequestFailed) as e:
            raise unavailable(f"the request failed: {e}") from None

        if not response.ok:
            raise unavailable(f"the API returned {status_text(response.status)}: {api_error_message(response.body)}")
        try:
            body = json.loads(response.body)
        except ValueError as e:
            raise unavailable(f"the response couldn't be parsed: {e}") from None
        return anthropic_reply(body)

    async def check(self, model: str) -> ModelCheck:
        """Asks the API for the models the key may use, which tells whether the key works and whether
        `model` is one of them."""
        try:
            response = await self._http.request("GET", f"{MODELS_URL}?limit=1000", timeout=KEY_CHECK_TIMEOUT, headers=self._headers())
        except (RequestTimeout, RequestFailed) as e:
            return Unreachable(f"the request failed: {e}")

        if response.status in (401, 403):
            return KeyRejected(api_error_message(response.body))
        if not response.ok:
            return Unreachable(f"the API returned {status_text(response.status)}: {api_error_message(response.body)}")
        try:
            listed = json.loads(response.body).get("data") or []
            return CheckOk(any(entry.get("id") == model for entry in listed))
        except (ValueError, AttributeError) as e:
            return Unreachable(f"the list of models couldn't be parsed: {e}")

    async def installed_models(self) -> list[str]:
        return []
