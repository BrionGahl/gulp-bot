"""Work that runs on its own, alongside whatever started it."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger(__name__)


class BackgroundTasks:
    """The tasks started with `spawn`, held until they are done (the event loop keeps only a weak
    reference to a task, so one nobody holds can vanish half way)."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Runs `coroutine` on its own. A failure is written to the log."""
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("A background task failed: %s", task.exception(), exc_info=task.exception())

    def cancel_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()
