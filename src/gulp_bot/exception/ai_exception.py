"""Why an AI feature gave no answer."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class AiError(Exception):
    """Why a request got no answer. `str()` of it is what the member is told."""

    message = ""

    def __str__(self) -> str:
        return self.message

    @property
    def reason(self) -> str:
        """Why there was no answer, for the log: more than the member is told, where there is more."""
        return str(self)


class NotAllowed(AiError):
    """The member isn't a raider. Callers check access first and say so themselves; this is the last line
    of defence."""

    message = "This is only available to raiders."


class DailyLimit(AiError):
    """The model has answered as many requests as it may in 24 hours, from everybody together."""

    message = "I've done as much as I can for now. Try again later."


class Disabled(AiError):
    """A developer has switched the AI features off."""

    message = "The AI features are turned off right now."


class Maintenance(AiError):
    """A developer has put the AI features in maintenance mode, and this member isn't an Officer."""

    message = "The AI features are in maintenance mode right now, and only available to Officers until it's over."


class Unavailable(AiError):
    """The model couldn't be reached or answered with an error. The details are in the log and, for the
    developers, in `/ai status`; the member is not told them."""

    message = "I couldn't get an answer right now. Try again in a bit."

    def __init__(self, detail: str):
        super().__init__()
        self.detail = detail

    @property
    def reason(self) -> str:
        return self.detail


def unavailable(detail: str) -> Unavailable:
    """An `Unavailable` for `detail`, written to the log."""
    log.error("Local model: %s", detail)
    return Unavailable(detail)


class ModelServerUnreachable(Exception):
    """The local model server couldn't be asked which models it has. The message says what went wrong."""


class ModelNameProblem(ValueError):
    """What a developer typed isn't a model name. The message says what is wrong."""
