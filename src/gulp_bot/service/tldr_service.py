"""`/tldr`: which messages of a channel are summarized, how they are written out for the model, and the
cooldown between two summaries of a channel. No Discord in here; channels are their numeric IDs, message
times Unix seconds, and other times `time.monotonic()` seconds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..core.text import single_line, truncate_chars
from ..schema.ai_schema import TldrBusy, TldrCooldown, TldrReady, TldrWait, TranscriptLine
from .ai_prompt_service import CHICAGO

# How far back a summary reaches: the last 4 hours.
WINDOW_SECS = 4 * 60 * 60

# A pause this long between two messages ends the conversation being summarized: what was said before it is
# another conversation.
GAP_SECS = 60 * 60

# How long after a summary of a channel the next one may be made, in seconds.
TLDR_COOLDOWN = 60.0 * 60

# The most characters of one message that go into the transcript.
MAX_LINE_CHARS = 400

# The most characters of conversation sent to the model. Leaves room for the prompt in a local model's
# context, and keeps a summary's cost down; the oldest messages are left out first.
MAX_TRANSCRIPT_CHARS = 12_000


def conversation_to_summarize(newest_first: list[TranscriptLine], now: int) -> list[TranscriptLine]:
    """The conversation to summarize out of `newest_first` (a channel's messages, the newest first): the
    messages of the last `WINDOW_SECS` before `now`, back to the first pause of `GAP_SECS` or more between
    two of them. Returned the oldest first, as a conversation is read."""
    kept: list[TranscriptLine] = []
    for line in newest_first:
        if now - line.at > WINDOW_SECS:
            break
        if kept and kept[-1].at - line.at >= GAP_SECS:
            break
        kept.append(line)
    kept.reverse()
    return kept


def transcript(lines: list[TranscriptLine], max_chars: int = MAX_TRANSCRIPT_CHARS) -> tuple[str, int]:
    """The conversation as text for the model, one `[time] author: text` line per message, each on one line
    and cut to length. If it all doesn't fit in `max_chars`, the oldest lines are left out; returns how many
    were."""
    rendered = [f"[{line.time}] {single_line(line.author)}: {single_line(truncate_chars(line.text, MAX_LINE_CHARS))}" for line in lines]

    # The newest lines are kept first, then as many older ones as fit.
    kept = 0
    used = 0
    for line in reversed(rendered):
        cost = len(line) + 1
        if used + cost > max_chars:
            break
        used += cost
        kept += 1

    omitted = len(rendered) - kept
    return "\n".join(rendered[omitted:]), omitted


def describe_span(seconds: int) -> str:
    """`seconds` as a reader would say it: `45m`, `2h`, `3h 20m`. Under a minute counts as a minute."""
    minutes = max((max(seconds, 0) + 59) // 60, 1)
    hours, minutes = divmod(minutes, 60)
    if hours == 0:
        return f"{minutes}m"
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h {minutes}m"


def central_time(at: int) -> str:
    """`at` (Unix seconds) as a time of day in the guild's Central Time, e.g. `21:04`."""
    return datetime.fromtimestamp(at, UTC).astimezone(CHICAGO).strftime("%H:%M")


@dataclass
class _Entry:
    started: float
    # Where the summary is and when it was posted; `None` while it's being made.
    posted: tuple[str, int] | None


class TldrCooldowns:
    """The cooldown of each channel. In memory only: a restart lets every channel be summarized again."""

    def __init__(self, period: float):
        self._period = period
        self._channels: dict[int, _Entry] = {}

    def start(self, channel: int, now: float) -> TldrCooldown:
        """Whether a summary of `channel` may be made now; if so, holds the channel for it."""
        self._channels = {key: entry for key, entry in self._channels.items() if now - entry.started < self._period}

        entry = self._channels.get(channel)
        if entry is None:
            self._channels[channel] = _Entry(now, None)
            return TldrReady()
        if entry.posted is None:
            return TldrBusy()
        link, posted_unix = entry.posted
        return TldrWait(link, posted_unix, max(0.0, self._period - max(0.0, now - entry.started)))

    def finish(self, channel: int, link: str, posted_unix: int, now: float) -> None:
        """The summary of `channel` is posted at `link`: the cooldown runs from now."""
        self._channels[channel] = _Entry(now, (link, posted_unix))

    def cancel(self, channel: int) -> None:
        """No summary of `channel` was posted after all: it may be tried again at once."""
        entry = self._channels.get(channel)
        if entry is not None and entry.posted is None:
            del self._channels[channel]
