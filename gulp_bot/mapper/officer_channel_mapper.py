"""Personal officer channels into what `/settings officer_channel_list` shows."""

from __future__ import annotations

from ..core.text import limited_lines
from ..schema.officer_channel_schema import RegisteredOfficerChannel

# Cap on the list's embed description, under Discord's 4096-character limit.
MAX_LIST_CHARS = 4000


def officer_channel_list_description(registered: list[RegisteredOfficerChannel]) -> str:
    lines = [
        f"<@{entry.user_id}> — <#{entry.channel_id}>\n" if entry.channel_id is not None else f"<@{entry.user_id}> — channel not recorded\n"
        for entry in registered
    ]
    return limited_lines(lines, MAX_LIST_CHARS)
