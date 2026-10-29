"""Why the AI assistant gave no answer."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class AskError(Exception):
    """Why a question got no answer. `str()` of it is what the member is told."""

    message = ""

    def __str__(self) -> str:
        return self.message


class NotAllowed(AskError):
    """The member isn't a raider. Callers check access first and say so themselves; this is the last line
    of defence."""

    message = "I only talk to raiders."


class RateLimited(AskError):
    """This member has asked too much; carries how many seconds until they may ask again."""

    def __init__(self, wait: float):
        super().__init__()
        self.wait = wait

    def __str__(self) -> str:
        return f"You're asking a lot, give me about {int(self.wait) + 1} seconds."


class DailyLimit(AskError):
    """The assistant has answered as many questions as it may in 24 hours, from everybody together."""

    message = "I've answered as much as I can for now. Try again later."


class Disabled(AskError):
    """A developer has switched the assistant off."""

    message = "The AI assistant is turned off right now."


class Maintenance(AskError):
    """A developer has put the assistant in maintenance mode, and this member isn't an Officer."""

    message = "I'm in maintenance mode right now, and only talk to Officers until it's over."


class Unavailable(AskError):
    """The model couldn't be reached or answered with an error. The details are in the log and, for the
    developers, in `/ai status`; the member who asked is not told them."""

    message = "I couldn't get an answer right now. Try again in a bit."

    def __init__(self, detail: str):
        super().__init__()
        self.detail = detail


def unavailable(detail: str) -> Unavailable:
    """An `Unavailable` for `detail`, written to the log."""
    log.error("Local model: %s", detail)
    return Unavailable(detail)


class ModelServerUnreachable(Exception):
    """The local model server couldn't be asked which models it has. The message says what went wrong."""


class ModelNameProblem(ValueError):
    """What a developer typed isn't a model name. The message says what is wrong."""
