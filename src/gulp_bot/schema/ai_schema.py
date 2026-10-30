"""The AI features' data: who may use them, what is sent to the model, what it answers, and what it has
used."""

from __future__ import annotations

import enum
from dataclasses import dataclass

# --- Who may use them ---


class Access(enum.Enum):
    """What a member may do with the AI features."""

    # Not a raider: may not use them.
    DENIED = 0
    # A raider: may use them.
    RAIDER = 1
    # An Officer: may use them too, also while they are in maintenance mode.
    OFFICER = 2
    # A Developer: everything an Officer may, and the only one who may run the `/ai` commands that switch
    # them off, change the model and so on.
    DEVELOPER = 3

    def is_allowed(self) -> bool:
        return self is not Access.DENIED

    def is_officer(self) -> bool:
        """Whether the member has the standing of an Officer, which a Developer has too."""
        return self in (Access.OFFICER, Access.DEVELOPER)


# --- What is sent ---


class TurnRole(enum.Enum):
    """Who said a turn, named as Ollama names them."""

    USER = "user"


@dataclass(frozen=True)
class Turn:
    role: TurnRole
    content: str

    @classmethod
    def user(cls, content: str) -> Turn:
        return cls(TurnRole.USER, content)


@dataclass(frozen=True)
class ModelReply:
    """What the model answered."""

    text: str
    # It ran out of tokens before the answer was finished.
    cut_short: bool
    # Why it stopped, as the server put it; for the log when something is wrong.
    stop_reason: str | None
    # `(input, output)` tokens, if the server said.
    tokens: tuple[int, int] | None


# --- Whether the model can be reached ---


@dataclass(frozen=True)
class CheckOk:
    """The model server answers. `model_listed` says whether the model that was asked about is installed
    on it."""

    model_listed: bool


@dataclass(frozen=True)
class Unreachable:
    """The model server couldn't be reached, or didn't answer as expected; carries what went wrong."""

    problem: str


ModelCheck = CheckOk | Unreachable


# --- What it has used ---


@dataclass
class Tokens:
    """What a number of requests used."""

    requests: int = 0
    input: int = 0
    output: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.requests += 1
        self.input += input_tokens
        self.output += output_tokens


@dataclass(frozen=True)
class UsageReport:
    """What the model has used, from the tokens the server reported."""

    # By model, within the last 24 hours.
    last_24h: list[tuple[str, Tokens]]
    # By model, since the bot started.
    since_start: list[tuple[str, Tokens]]


@dataclass(frozen=True)
class ApiFailure:
    """A request that got no answer."""

    # When it happened, in Unix seconds.
    at_unix: int
    # What went wrong, on one line.
    detail: str


# --- /tldr ---


@dataclass(frozen=True)
class TranscriptLine:
    """One message, as it goes into a `/tldr` transcript."""

    # When it was sent, in Unix seconds.
    at: int
    # When it was sent, as shown to the model (e.g. `21:04`).
    time: str
    # Who sent it, as the model should name them.
    author: str
    text: str


@dataclass(frozen=True)
class TldrReady:
    """Go ahead: the channel is now held until the summary is finished or cancelled."""


@dataclass(frozen=True)
class TldrBusy:
    """A summary of this channel is being made right now."""


@dataclass(frozen=True)
class TldrWait:
    """The last summary of this channel is still fresh: it is at `link`, was posted at `posted_unix`, and
    the next may be made in `remaining` seconds."""

    link: str
    posted_unix: int
    remaining: float


TldrCooldown = TldrReady | TldrBusy | TldrWait
