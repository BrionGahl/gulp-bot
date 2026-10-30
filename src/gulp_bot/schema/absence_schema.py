"""Absences members report for raid nights, and the other things they raise in their personal officer
channels, as the officer digest shows them."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime


class AbsenceKind(enum.Enum):
    """Whether a member will miss a raid night, or might: late, or not sure yet."""

    ABSENT = "absent"
    TENTATIVE = "tentative"

    @property
    def label(self) -> str:
        return {AbsenceKind.ABSENT: "out", AbsenceKind.TENTATIVE: "tentative"}[self]


@dataclass(frozen=True)
class NewAbsence:
    guild_id: int
    user_id: int
    # Both included; equal for a single day.
    start: date
    end: date
    kind: AbsenceKind
    note: str | None
    # Who reported it: the member, or the officer who added it for them.
    created_by: int
    # The personal officer channel message it was read from; `None` for one added with `/absence add`.
    source_message_id: int | None = None


@dataclass(frozen=True)
class Absence:
    id: int
    guild_id: int
    user_id: int
    start: date
    end: date
    kind: AbsenceKind
    note: str | None
    created_by: int
    source_message_id: int | None


class NoteCategory(enum.Enum):
    """What a member raised in their personal officer channel, other than an absence."""

    ROLE_CHANGE = "role_change"
    LOOT_ROSTER = "loot_roster"
    FEEDBACK = "feedback"

    @property
    def label(self) -> str:
        return {NoteCategory.ROLE_CHANGE: "Role or spec", NoteCategory.LOOT_ROSTER: "Loot or roster", NoteCategory.FEEDBACK: "Feedback"}[self]


@dataclass(frozen=True)
class NewChannelNote:
    guild_id: int
    user_id: int
    channel_id: int
    source_message_id: int
    category: NoteCategory
    # One line, written by the model.
    summary: str


@dataclass(frozen=True)
class ChannelNote:
    id: int
    user_id: int
    channel_id: int
    source_message_id: int
    category: NoteCategory
    summary: str
    created_at: datetime


@dataclass(frozen=True)
class DetectedAbsence:
    """An absence the model read out of a message, checked against the calendar."""

    start: date
    end: date
    kind: AbsenceKind
    note: str | None


@dataclass(frozen=True)
class Detection:
    """What the model read out of a message in a personal officer channel."""

    # An absence covering at least one raid night, ready to record.
    absence: DetectedAbsence | None
    # The model saw an absence but couldn't tell which day: the member is asked to use `/absence add`.
    unclear_absence: bool
    notes: list[tuple[NoteCategory, str]]


@dataclass(frozen=True)
class OfficerChannelOwner:
    """Whose personal officer channel a channel is."""

    guild_id: int
    user_id: int
