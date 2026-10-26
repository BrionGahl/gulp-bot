"""HTTP requests to the outside APIs (Anthropic, Ollama, Warcraft Logs, WowUtils).

Callers get the status and the body back, or one of two errors: `RequestTimeout` if the server took
too long, `RequestFailed` if it couldn't be reached at all. `Http` is the one real client; tests hand
the API clients a fake with the same methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import aiohttp


def status_text(code: int) -> str:
    """A status code with its reason, as in `404 Not Found`."""
    try:
        return f"{code} {HTTPStatus(code).phrase}"
    except ValueError:
        return f"{code} <unknown status code>"


class RequestTimeout(Exception):
    """The server took longer than allowed to answer."""


class RequestFailed(Exception):
    """The request couldn't be made: the server couldn't be reached, or the connection broke."""


@dataclass(frozen=True)
class Response:
    status: int
    body: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class HttpClient(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        timeout: float,
        headers: dict[str, str] | None = None,
        json: Any = None,
        form: dict[str, str] | None = None,
        basic_auth: tuple[str, str] | None = None,
    ) -> Response: ...


class Http:
    """The bot's HTTP client: one connection pool for every API it calls."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        import aiohttp

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def request(
        self,
        method: str,
        url: str,
        *,
        timeout: float,
        headers: dict[str, str] | None = None,
        json: Any = None,
        form: dict[str, str] | None = None,
        basic_auth: tuple[str, str] | None = None,
    ) -> Response:
        import aiohttp

        auth = aiohttp.BasicAuth(*basic_auth) if basic_auth is not None else None
        try:
            async with self._client().request(
                method,
                url,
                headers=headers,
                json=json,
                data=form,
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                body = await response.text(errors="replace")
                return Response(response.status, body)
        except TimeoutError:
            raise RequestTimeout(f"no answer within {timeout:g} seconds") from None
        except aiohttp.ClientError as e:
            raise RequestFailed(str(e) or type(e).__name__) from None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
