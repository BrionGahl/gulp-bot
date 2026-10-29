"""The AI assistant's data: who may talk to it, what is said, what the model answers, and what it has
used."""

from __future__ import annotations

import enum
from dataclasses import dataclass

# --- Who may talk to it ---


class Access(enum.Enum):
    """What a member may do with the assistant."""

    # Not a raider: the assistant doesn't answer.
    DENIED = 0
    # A raider: may talk to the assistant.
    RAIDER = 1
    # An Officer: may talk to it too, and may ask it to change things.
    OFFICER = 2
    # A Developer: everything an Officer may, and the only one who may run the `/ai` commands that switch
    # the assistant off, change its model, reset limits and so on.
    DEVELOPER = 3

    def is_allowed(self) -> bool:
        return self is not Access.DENIED

    def is_officer(self) -> bool:
        """Whether the member has the standing of an Officer, which a Developer has too."""
        return self in (Access.OFFICER, Access.DEVELOPER)

    def level(self) -> int:
        """Higher is more trusted: what is said to a member is shown only to members of this level or
        higher (see `ConversationHistoryRepository`)."""
        return self.value


# --- What is said ---


class TurnRole(enum.Enum):
    """Who said a turn, named as the APIs name them."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class Turn:
    role: TurnRole
    content: str

    @classmethod
    def user(cls, content: str) -> Turn:
        return cls(TurnRole.USER, content)

    @classmethod
    def assistant(cls, content: str) -> Turn:
        return cls(TurnRole.ASSISTANT, content)


@dataclass(frozen=True)
class Question:
    """What a member asked the assistant."""

    # The member, as the model should name them (`speaker_label`).
    speaker: str
    # What they wrote.
    text: str
    # The message they replied to, if they did: its author as the model should name them (`"you"` for one
    # of the assistant's own answers), and its text.
    replying_to: tuple[str, str] | None = None


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
    """What the assistant has used, from the tokens the API reported."""

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


@dataclass(frozen=True)
class MemberUsage:
    """How many questions a member has asked within the window, how many they may, and how long the window
    is, in seconds."""

    asked: int
    allowed: int
    window_secs: float


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
