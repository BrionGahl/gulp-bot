"""The weekly raid notes forum thread."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeekThread:
    """The raid notes forum thread for one week."""

    thread_id: int
    title: str
    # Whether this call created the thread, as opposed to finding the one already recorded.
    created: bool
