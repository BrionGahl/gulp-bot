"""The loop every scheduled job runs on.

Schedules differ per guild and can change at any time, so rather than sleeping until one fixed fire time
the loop re-reads what is scheduled on every wake-up, runs everything with a fire time since the previous
wake-up, and sleeps until the earliest upcoming fire time, or `MAX_SLEEP` if that comes first. Fire times
are computed in `America/New_York` (see `core/schedule_parsing.py`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..core.cron import Schedule
from ..core.schedule_parsing import now_in_new_york

log = logging.getLogger(__name__)

# The longest the loop sleeps before re-reading what is scheduled, in seconds, which bounds how long a
# change (a new announcement, a schedule changed through `/settings`) takes to be picked up.
MAX_SLEEP = 60.0


@dataclass(frozen=True)
class ScheduledWork:
    """Something to do whenever `schedule` fires. `run` is given the fire time; a failure is written to
    the log after `failure_message`."""

    schedule: Schedule
    run: Callable[[datetime], Awaitable[None]]
    failure_message: str


async def run_schedule_loop(name: str, scheduled_work: Callable[[], Awaitable[list[ScheduledWork]]]) -> None:
    """Runs forever, doing each piece of `scheduled_work()` whenever its schedule fires. If what is scheduled
    can't be read, nothing is run until it can. Then each piece that came due in the meantime runs once,
    given the earliest fire time it missed, however many it missed: a database outage over two raid nights
    posts one late attendance snapshot, not two."""
    last_checked = now_in_new_york()

    while True:
        now = now_in_new_york()
        next_wake = now + timedelta(seconds=MAX_SLEEP)

        try:
            work = await scheduled_work()
        except Exception as e:
            # `last_checked` is left alone, so what came due in the meantime is done (once) when it's back.
            log.exception("Failed to load %s: %s", name, e)
        else:
            for item in work:
                fire_time = item.schedule.after(last_checked)
                if fire_time is not None and fire_time.timestamp() <= now.timestamp():
                    try:
                        await item.run(fire_time)
                    except Exception as e:
                        log.exception("%s: %s", item.failure_message, e)

                upcoming = item.schedule.after(now)
                if upcoming is not None:
                    next_wake = min(next_wake, upcoming, key=datetime.timestamp)
            last_checked = now

        # Measured from the current time, not `now`: running the work above may have taken a while.
        await asyncio.sleep(max(0.0, next_wake.timestamp() - now_in_new_york().timestamp()))
