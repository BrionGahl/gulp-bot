"""Raid attendance: who was in the raid voice channel at the start and the end of a raid."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

# How long before a raid-end snapshot a raid-start snapshot can have been taken and still be treated as
# the start of that same raid. Anything older is a start whose end never fired (the bot was down, or the
# schedules don't pair up), and comparing against it would be misleading.
MAX_RAID_HOURS = 12


class AttendanceMoment(enum.Enum):
    """Which point of the raid a snapshot was taken at. The value is how it is named in messages."""

    START = "start"
    END = "end"
    # Taken on demand with `/test_raid_attendance`.
    TEST = "manual check"


@dataclass(frozen=True)
class StartSnapshot:
    """A stored raid-start snapshot."""

    voice_channel_id: int
    taken_at: datetime
    present: list[int]

    def pairs_with(self, voice_channel_id: int, end: datetime) -> bool:
        """Whether this is plausibly the start of the raid ending at `end` in `voice_channel_id`."""
        elapsed = end.timestamp() - self.taken_at.timestamp()
        return self.voice_channel_id == voice_channel_id and 0 <= elapsed <= MAX_RAID_HOURS * 3600


@dataclass(frozen=True)
class AttendanceComparison:
    """How the people present at the end of a raid differ from those present at its start."""

    # How many were there at both moments.
    stayed: int
    left_early: list[int]
    joined_late: list[int]
