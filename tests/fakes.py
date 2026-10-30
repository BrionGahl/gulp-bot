"""Stand-ins the tests share: an HTTP client that answers from a list and keeps what it was asked, the
answers Ollama and Warcraft Logs give, the AI service on that client, and a command context for the
permission checks."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from gulp_bot.client.ollama_client import OllamaClient
from gulp_bot.core.http_client import Response
from gulp_bot.schema.guild_settings_schema import GuildSettings
from gulp_bot.service.ai_service import AiService
from gulp_bot.service.guild_settings_service import GuildSettingsService

OLLAMA_URL = "http://ollama:11434"


def run(coroutine):
    return asyncio.run(coroutine)


class FakeHttp:
    """Answers each request with the next of `responses` (raising it, if it is an exception), and keeps what
    was asked."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def request(self, method, url, *, timeout, headers=None, json=None, form=None, basic_auth=None):
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json, "form": form, "basic_auth": basic_auth})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ollama_answer(text: str, done_reason: str = "stop", prompt_tokens: int = 100, output_tokens: int = 20) -> Response:
    """What Ollama sends back for an answer of `text`."""
    body = {"message": {"role": "assistant", "content": text}, "done_reason": done_reason, "prompt_eval_count": prompt_tokens, "eval_count": output_tokens}
    return Response(200, json.dumps(body))


def ai_service(http: FakeHttp | None = None, model: str = "qwen3.5:9b", think: bool | None = False, daily_limit: int = 100) -> AiService:
    """The AI service, asking `http` instead of an Ollama server."""
    return AiService(OllamaClient(http or FakeHttp(), OLLAMA_URL), model, daily_limit, think=think)  # type: ignore[arg-type]


def token_response(token: str = "test") -> Response:
    """What Warcraft Logs sends back for an access token."""
    return Response(200, json.dumps({"access_token": token, "expires_in": 3600}))


class FakeContext:
    """What the permission checks read of a command's context: the member's roles, whether they are an
    administrator, the server's settings, and what the check says to them."""

    def __init__(self, roles: list[int], administrator: bool = False, settings: GuildSettings | None = None):
        self.guild_id = 1
        self.author = SimpleNamespace(roles=roles)
        self.interaction = SimpleNamespace(guild_id=1, permissions=SimpleNamespace(administrator=administrator))
        self.services = SimpleNamespace(guild_settings=GuildSettingsService(repository=None, cache={1: settings or GuildSettings()}))  # type: ignore[arg-type]
        self.sent: list[Any] = []

    async def send(self, content, **kwargs):
        self.sent.append(content)
