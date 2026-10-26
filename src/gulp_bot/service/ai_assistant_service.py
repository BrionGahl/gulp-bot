"""The guild's AI assistant: a model answers `/ask`, `/tldr` and any message that says "Bot please".

The model is Claude, through the Anthropic API, or one on the guild's own hardware, served by Ollama
(`AI_BACKEND=local`); everything but the request itself is the same for both (see `client/`). Without
`ANTHROPIC_API_KEY` (and without a local model) there is no assistant at all, and it stays quiet.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import datetime

from ..client.anthropic_client import AnthropicClient
from ..client.chat_model_client import ChatModelClient
from ..client.ollama_client import OllamaClient
from ..core.config import AiBackend, Config
from ..core.http_client import HttpClient
from ..core.rate_limiter import RateLimiter
from ..exception.ai_exception import AskError, DailyLimit, NotAllowed, RateLimited, Unavailable, unavailable
from ..repository.ai_memory_repository import AnswerMessageRepository, ApiFailureRepository, ConversationHistoryRepository, TokenUsageRepository
from ..schema.ai_schema import Access, ApiFailure, MemberUsage, ModelCheck, Question, TldrCooldown, Turn, UsageReport
from ..schema.guild_settings_schema import AiSettings
from .ai_access_service import ensure_switches_allow
from .ai_model_catalog_service import local_label
from .ai_prompt_service import CHICAGO, load_guild_prompt, render_question, system_prompt, system_prompt_for_request, tldr_request, with_cut_short_note
from .tldr_service import TLDR_COOLDOWN, TldrCooldowns

log = logging.getLogger(__name__)

# How many turns (a question and an answer are two) of a channel are sent along with a question. Every one
# of them is billed again on each request, so this is also what keeps the cost down.
MAX_HISTORY_TURNS = 8

# A channel nobody has asked in for this many seconds starts afresh.
FORGET_AFTER = 60.0 * 60

# Each member may ask this many times in the window below.
MAX_REQUESTS_PER_USER = 10
RATE_LIMIT_WINDOW = 10.0 * 60

# The window of the limit on all members together (`AI_DAILY_LIMIT` requests in it), which caps the
# bill whatever the number of members. It slides: it is the last 24 hours, not a calendar day.
DAILY_WINDOW = 24.0 * 60 * 60

# The key the limit on all members together counts under; it is not a user.
EVERYBODY = 0

# How many failed requests are kept for `/ai status`.
MAX_FAILURES_KEPT = 5

# How many of the assistant's answers are remembered, so that a reply to one of them carries the
# conversation on. An answer is a message; a long answer is several.
MAX_ANSWERS_KEPT = 500


def _debug_option(value: str | None) -> str:
    return "None" if value is None else f'Some("{value}")'


class AiAssistantService:
    """The assistant. Built once, when the bot starts."""

    def __init__(self, model_client: ChatModelClient, model: str, effort: str | None, system: str, daily_limit: int, think: bool | None = False):
        self._model_client = model_client
        self._model = model
        # The `output_config.effort` sent with each request; none if not set (and always none for a local
        # model, which has no such setting).
        self._effort = effort
        # Whether a local model thinks (`LOCAL_MODEL_THINK`); `None` leaves it to the model. Not used with
        # the Anthropic API.
        self._think = think
        # The system prompt without the date, which changes.
        self._system = system

        self.history = ConversationHistoryRepository(MAX_HISTORY_TURNS, FORGET_AFTER)
        # Per member.
        self.member_limiter = RateLimiter(MAX_REQUESTS_PER_USER, RATE_LIMIT_WINDOW)
        # All members together, under `EVERYBODY`.
        self.daily_limiter = RateLimiter(daily_limit, DAILY_WINDOW)
        self.token_usage = TokenUsageRepository(DAILY_WINDOW)
        self.failures = ApiFailureRepository(MAX_FAILURES_KEPT)
        self.answers = AnswerMessageRepository(MAX_ANSWERS_KEPT)
        self.tldr_cooldowns = TldrCooldowns(TLDR_COOLDOWN)

    def __repr__(self) -> str:
        return f"AiAssistantService(local={self.is_local}, model={self._model!r}, effort={self._effort!r}, think={self._think!r})"

    @classmethod
    def from_config(cls, http: HttpClient, config: Config) -> AiAssistantService | None:
        """The assistant, or `None` if it can't run: on the Anthropic API without a key. A local model needs
        no key; whether its server is up is found out when it is asked (and with `/ai check`)."""
        model_client: ChatModelClient
        if config.ai_backend is AiBackend.ANTHROPIC:
            if config.anthropic_api_key is None:
                return None
            log.info("AI assistant enabled, using model %s through the Anthropic API", config.anthropic_model)
            model_client, model, effort = AnthropicClient(http, config.anthropic_api_key), config.anthropic_model, config.anthropic_effort
        else:
            url = config.local_model_url.rstrip("/")
            log.info("AI assistant enabled, using local model %s at %s", config.local_model, url)
            model_client, model, effort = OllamaClient(http, url), config.local_model, None
        guild_prompt = load_guild_prompt(config.system_prompt_path)
        return cls(model_client, model, effort, system_prompt(config.bot_name, guild_prompt), config.ai_daily_limit, think=config.local_model_think)

    # --- What it runs on ---

    @property
    def is_local(self) -> bool:
        """Whether the model runs on the guild's own hardware (Ollama) rather than the Anthropic API."""
        return self._model_client.is_local

    @property
    def configured_model(self) -> str:
        """The model the bot was configured with (`ANTHROPIC_MODEL`, or `LOCAL_MODEL`)."""
        return self._model

    @property
    def configured_effort(self) -> str | None:
        """The effort the bot was configured with (`ANTHROPIC_EFFORT`); `None` if none is sent."""
        return self._effort

    @property
    def configured_think(self) -> bool | None:
        """Whether a local model thinks as the bot was configured (`LOCAL_MODEL_THINK`); `None` if that is
        left to the model."""
        return self._think

    def resolve_think(self, settings: AiSettings) -> bool | None:
        """Whether a local model thinks in a guild whose developers have set `settings`: what they chose with
        `/ai think`, else what the bot was configured with. `None` sends nothing, leaving it to the model."""
        return self._think if settings.think is None else settings.think.think

    def resolve(self, settings: AiSettings) -> tuple[str, str | None]:
        """The model and the effort to use in a guild whose developers have set `settings`: what they chose
        with `/ai model` and `/ai effort`, else what the bot was configured with. The effort is `None` when
        none is to be sent, which is always for a local model."""
        model = settings.model if settings.model is not None else self._model
        if self.is_local:
            return model, None
        if settings.effort is None:
            effort = self._effort
        elif settings.effort.lower() == "off":
            effort = None
        else:
            effort = settings.effort
        return model, effort

    async def check_model(self, model: str) -> ModelCheck:
        """Whether the API key works and the API lists `model`; or, for a local model, whether the server
        answers and has it. Costs no tokens and isn't counted in the limits."""
        return await self._model_client.check(model)

    async def installed_models(self) -> list[str]:
        """The models installed on the local server, for a developer picking one; nothing if the assistant
        isn't local or the server doesn't answer in time."""
        return await self._model_client.installed_models()

    # --- Asking ---

    async def ask(self, channel: int, user: int, access: Access, settings: AiSettings, question: Question) -> str:
        """Answers `question`, asked by user `user`, who has `access`, in `channel`, in the light of what was
        said in that channel before. Refuses anyone who isn't allowed, and everybody while the guild's
        developers have the assistant off (`settings`). Uses the model, effort and thinking they chose, if they did.
        Raises `AskError` if there is no answer."""
        if not access.is_allowed():
            raise NotAllowed()
        ensure_switches_allow(settings, access)
        model, effort = self.resolve(settings)
        think = self.resolve_think(settings)

        now = time.monotonic()
        rendered = render_question(question.speaker, question.text, question.replying_to)

        wait = self.member_limiter.check(user, now)
        if wait is not None:
            raise RateLimited(wait)
        if self.daily_limiter.check(EVERYBODY, now) is not None:
            # Not this member's doing, so it doesn't count against them.
            self.member_limiter.undo(user)
            raise DailyLimit()
        # Only what a member of this level may see, whatever else has been said in the channel.
        turns = self.history.turns_visible_to(channel, access.level(), now)
        turns.append(Turn.user(rendered))

        try:
            answer, cut_short = await self._complete(turns, access, model, effort, think)
        except AskError as e:
            # No answer, no charge: a failure is not held against the member or the day's total.
            self.member_limiter.undo(user)
            self._give_back_daily_request(e)
            raise

        # Remembered without the note, which is for the member, not the model.
        self.history.record_exchange(channel, rendered, answer, access.level(), time.monotonic())
        return with_cut_short_note(answer, cut_short)

    async def summarize(self, access: Access, settings: AiSettings, transcript: str, omitted: int) -> str:
        """A plain summary of a channel's conversation, written out in `transcript` (`omitted` older messages
        didn't fit), asked for by a member with `access`. Counts towards the limit for all members together,
        but not towards the member's own: `/tldr` has its own cooldown. Not kept in the channel's memory.
        Raises `AskError` if there is no summary."""
        if not access.is_allowed():
            raise NotAllowed()
        ensure_switches_allow(settings, access)
        model, effort = self.resolve(settings)
        think = self.resolve_think(settings)

        if self.daily_limiter.check(EVERYBODY, time.monotonic()) is not None:
            raise DailyLimit()
        try:
            summary, cut_short = await self._complete([Turn.user(tldr_request(transcript, omitted))], access, model, effort, think)
        except AskError as e:
            # No summary, no charge.
            self._give_back_daily_request(e)
            raise
        return with_cut_short_note(summary, cut_short)

    def _give_back_daily_request(self, error: AskError) -> None:
        self.daily_limiter.undo(EVERYBODY)
        if isinstance(error, Unavailable):
            self.failures.record(int(time.time()), error.detail)

    async def _complete(self, turns: Sequence[Turn], access: Access, model: str, effort: str | None, think: bool | None) -> tuple[str, bool]:
        """One request to `model` with `turns` as the conversation, the last of which is from a member with
        `access`. Returns the answer, and whether it ran out of tokens before it was finished."""
        system = system_prompt_for_request(self._system, access, datetime.now(CHICAGO))
        reply = await self._model_client.complete(model, system, turns, effort, think)

        if reply.tokens is not None:
            input_tokens, output_tokens = reply.tokens
            log.info("%s answered with %d input and %d output tokens", model, input_tokens, output_tokens)
            # Counted whether or not the answer turns out usable: the tokens were used either way. A local
            # model is counted under its own label, so that it shows as free.
            self.token_usage.record(local_label(model) if self.is_local else model, input_tokens, output_tokens, time.monotonic())

        if not reply.text:
            raise unavailable(f"the answer had no text (stop reason: {_debug_option(reply.stop_reason)})")
        if reply.cut_short:
            log.info("%s ran out of tokens before the answer was finished", model)
        return reply.text, reply.cut_short

    # --- What it remembers ---

    def remember_answer(self, message_id: int) -> None:
        """Remembers that the assistant answered with the message `message_id`, so that a reply to it is taken
        as talking to the assistant."""
        self.answers.remember(message_id)

    def is_answer(self, message_id: int) -> bool:
        """Whether the message is one the assistant answered with (among the last few hundred)."""
        return self.answers.contains(message_id)

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
        """How many questions have been answered in the last 24 hours, and how many may be."""
        return self.daily_limiter.count(EVERYBODY, time.monotonic()), self.daily_limiter.max

    def member_usage(self, user: int) -> MemberUsage:
        """How many questions `user` has asked within the window, and how many they may."""
        return MemberUsage(self.member_limiter.count(user, time.monotonic()), self.member_limiter.max, RATE_LIMIT_WINDOW)

    def reset_member(self, user: int) -> int:
        """Gives `user` their whole allowance of questions back. Returns how many they had used."""
        return self.member_limiter.clear(user, time.monotonic())
