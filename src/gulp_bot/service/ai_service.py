"""The bot's AI features: a model sums up a channel's chat for `/tldr`, and writes the recap of each raid night
(`raid_recap_service`).

The model runs on the guild's own hardware, served by Ollama (see `client/`), so nothing members write
leaves the machine. If the server is down, the member is told there was no answer, and `/ai check` says
why.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from ..client.chat_model_client import ChatModelClient
from ..client.ollama_client import OllamaClient
from ..core.config import Config
from ..core.http_client import HttpClient
from ..core.rate_limiter import RateLimiter
from ..exception.ai_exception import AiError, DailyLimit, Disabled, NotAllowed, Unavailable, unavailable
from ..repository.ai_memory_repository import ApiFailureRepository, TokenUsageRepository
from ..schema.ai_schema import Access, ApiFailure, ModelCheck, TldrCooldown, Turn, UsageReport
from ..schema.guild_settings_schema import AiSettings
from .ai_access_service import ensure_switches_allow
from .tldr_service import SYSTEM_PROMPT, TEMPERATURE, TLDR_COOLDOWN, TldrCooldowns, tldr_request, with_cut_short_note

log = logging.getLogger(__name__)

# The window of the limit on all requests together (`AI_DAILY_LIMIT` requests in it), which caps how busy
# the GPU is kept whatever the number of members. It slides: it is the last 24 hours, not a calendar day.
DAILY_WINDOW = 24.0 * 60 * 60

# The key the limit on all requests together counts under; it is not a user.
EVERYBODY = 0

# How many failed requests are kept for `/ai status`.
MAX_FAILURES_KEPT = 5


def _debug_option(value: str | None) -> str:
    return "None" if value is None else f'Some("{value}")'


class AiService:
    """Built once, when the bot starts."""

    def __init__(self, model_client: ChatModelClient, model: str, daily_limit: int, think: bool | None = False):
        self._model_client = model_client
        self._model = model
        # Whether the model thinks (`LOCAL_MODEL_THINK`); `None` leaves it to the model.
        self._think = think

        self.daily_limiter = RateLimiter(daily_limit, DAILY_WINDOW)
        self.token_usage = TokenUsageRepository(DAILY_WINDOW)
        self.failures = ApiFailureRepository(MAX_FAILURES_KEPT)
        self.tldr_cooldowns = TldrCooldowns(TLDR_COOLDOWN)

    def __repr__(self) -> str:
        return f"AiService(model={self._model!r}, think={self._think!r})"

    @classmethod
    def from_config(cls, http: HttpClient, config: Config) -> AiService:
        """Whether its server is up is found out when it is asked (and with `/ai check`)."""
        url = config.local_model_url.rstrip("/")
        log.info("AI features enabled, using model %s at %s", config.local_model, url)
        return cls(OllamaClient(http, url), config.local_model, config.ai_daily_limit, think=config.local_model_think)

    # --- What it runs on ---

    @property
    def configured_model(self) -> str:
        """The model the bot was configured with (`LOCAL_MODEL`)."""
        return self._model

    @property
    def configured_think(self) -> bool | None:
        """Whether the model thinks as the bot was configured (`LOCAL_MODEL_THINK`); `None` if that is left
        to the model."""
        return self._think

    def resolve_think(self, settings: AiSettings) -> bool | None:
        """Whether the model thinks in a guild whose developers have set `settings`: what they chose with
        `/ai think`, else what the bot was configured with. `None` sends nothing, leaving it to the model."""
        return self._think if settings.think is None else settings.think.think

    def resolve_model(self, settings: AiSettings) -> str:
        """The model to use in a guild whose developers have set `settings`: what they chose with
        `/ai model`, else what the bot was configured with."""
        return settings.model if settings.model is not None else self._model

    async def check_model(self, model: str) -> ModelCheck:
        """Whether the server answers and has `model`. Isn't counted in the limit."""
        return await self._model_client.check(model)

    async def installed_models(self) -> list[str]:
        """The models installed on the server, for a developer picking one; nothing if the server doesn't
        answer in time."""
        return await self._model_client.installed_models()

    # --- Summing up ---

    async def summarize(self, access: Access, settings: AiSettings, transcript: str, omitted: int) -> str:
        """A plain summary of a channel's conversation, written out in `transcript` (`omitted` older messages
        didn't fit), asked for by a member with `access`. Refuses anyone who isn't allowed, and everybody
        while the guild's developers have the AI features off (`settings`). Uses the model and thinking they
        chose, if they did. Counts towards the limit on all requests together. Raises `AiError` if there is
        no summary."""
        if not access.is_allowed():
            raise NotAllowed()
        ensure_switches_allow(settings, access)
        summary, cut_short = await self._complete(SYSTEM_PROMPT, [Turn.user(tldr_request(transcript, omitted))], settings, TEMPERATURE)
        return with_cut_short_note(summary, cut_short)

    async def generate(
        self, settings: AiSettings, system: str, prompt: str, temperature: float | None, schema: dict[str, Any] | None = None
    ) -> tuple[str, bool]:
        """What the model answers to `prompt`, with `system` as the system prompt, for something the bot does
        by itself rather than for a member, in a guild whose developers have set `settings`: refused while
        they have the AI features off. With a JSON `schema`, the answer is JSON in that shape. Counts towards the limit on all requests together. Returns the answer,
        and whether it ran out of tokens before it was finished. Raises `AiError` if there is no answer."""
        if settings.disabled:
            raise Disabled()
        return await self._complete(system, [Turn.user(prompt)], settings, temperature, schema)

    async def _complete(
        self, system: str, turns: Sequence[Turn], settings: AiSettings, temperature: float | None, schema: dict[str, Any] | None = None
    ) -> tuple[str, bool]:
        """One request with `system` as the system prompt and `turns` as the conversation, to the model the
        guild's `settings` choose, at `temperature` (`None` leaves the model's own), counted in the limit on
        all requests together. Returns the answer, and
        whether it ran out of tokens before it was finished. A request that gets no answer isn't counted.
        Raises `AiError` if there is no answer."""
        if self.daily_limiter.check(EVERYBODY, time.monotonic()) is not None:
            raise DailyLimit()
        model = self.resolve_model(settings)
        try:
            reply = await self._model_client.complete(model, system, turns, self.resolve_think(settings), temperature, schema)
            if reply.tokens is not None:
                input_tokens, output_tokens = reply.tokens
                log.info("%s answered with %d input and %d output tokens", model, input_tokens, output_tokens)
                # Counted whether or not the answer turns out usable: the tokens were used either way.
                self.token_usage.record(model, input_tokens, output_tokens, time.monotonic())
            if not reply.text:
                raise unavailable(f"the answer had no text (stop reason: {_debug_option(reply.stop_reason)})")
        except AiError as e:
            # No answer, no charge.
            self.daily_limiter.undo(EVERYBODY)
            if isinstance(e, Unavailable):
                self.failures.record(int(time.time()), e.detail)
            raise

        if reply.cut_short:
            log.info("%s ran out of tokens before the answer was finished", model)
        return reply.text, reply.cut_short

    # --- The /tldr cooldown ---

    def start_tldr(self, channel: int) -> TldrCooldown:
        """Whether `/tldr` may summarize `channel` now; if so, the channel is held until `finish_tldr` or
        `cancel_tldr`."""
        return self.tldr_cooldowns.start(channel, time.monotonic())

    def finish_tldr(self, channel: int, link: str, posted_unix: int) -> None:
        """The summary of `channel` was posted at `link`, at `posted_unix`: its hour of cooldown starts."""
        self.tldr_cooldowns.finish(channel, link, posted_unix, time.monotonic())

    def cancel_tldr(self, channel: int) -> None:
        """No summary of `channel` was posted after all, so it may be tried again at once."""
        self.tldr_cooldowns.cancel(channel)

    # --- What it has used ---

    def usage_report(self) -> UsageReport:
        """The tokens the requests have used."""
        return UsageReport(self.token_usage.recent(time.monotonic()), self.token_usage.since_start())

    def failure_report(self) -> tuple[int, list[ApiFailure]]:
        """How many requests have failed since the bot started, and the latest of them, newest first."""
        return self.failures.total, self.failures.latest()

    def daily_usage(self) -> tuple[int, int]:
        """How many requests have been answered in the last 24 hours, and how many may be."""
        return self.daily_limiter.count(EVERYBODY, time.monotonic()), self.daily_limiter.max
