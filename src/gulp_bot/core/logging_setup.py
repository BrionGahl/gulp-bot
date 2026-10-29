"""The bot's log: one JSON object per line on stdout."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the fields Google Cloud Logging reads (`severity`, `time`)."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "severity": record.levelname,
            "target": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: int) -> None:
    """Sends every log record, at `level` and above, to stdout as JSON."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
