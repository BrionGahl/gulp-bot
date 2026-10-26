"""Raid attendance snapshots into the embeds posted to the raid notes thread."""

from __future__ import annotations

from datetime import UTC, datetime

from ..core.embed import MAX_DESCRIPTION_CHARS, MAX_FIELD_CHARS, Embed, add_field, new_embed
from ..core.text import utf8_length
from ..schema.raid_attendance_schema import AttendanceComparison, AttendanceMoment, StartSnapshot


def snapshot_embed(moment: AttendanceMoment, at: datetime, voice_channel_id: int, present: list[int]) -> Embed:
    """The embed for one snapshot: who was in the voice channel at that moment."""
    return new_embed(
        title=f"Raid attendance — {moment.value}",
        description=snapshot_description(at, voice_channel_id, present),
        timestamp=at.astimezone(UTC).isoformat(),
    )


def snapshot_description(at: datetime, voice_channel_id: int, present: list[int]) -> str:
    description = f"<#{voice_channel_id}> · <t:{int(at.timestamp())}:f>\n\n"
    if not present:
        return description + "Nobody was in the channel."
    description += f"**{len(present)} present**\n"
    return description + mention_list(present, MAX_DESCRIPTION_CHARS - utf8_length(description))


def summary_embed(start: StartSnapshot | None, end_at: datetime, comparison: AttendanceComparison | None) -> Embed:
    """The embed posted after a raid-end snapshot, comparing it with the raid's start snapshot, or saying
    there was none to compare with."""
    embed = new_embed(title="Raid attendance — summary", timestamp=end_at.astimezone(UTC).isoformat())
    if start is None or comparison is None:
        embed["description"] = "No start snapshot was recorded for this raid, so there's nothing to compare the end against."
        return embed

    def listed(users: list[int]) -> str:
        return mention_list(users, MAX_FIELD_CHARS) if users else "Nobody"

    embed["description"] = f"<t:{int(start.taken_at.timestamp())}:t> to <t:{int(end_at.timestamp())}:t>"
    add_field(embed, "There at both the start and the end", str(comparison.stayed))
    add_field(embed, f"Left before the end ({len(comparison.left_early)})", listed(comparison.left_early))
    add_field(embed, f"Joined after the start ({len(comparison.joined_late)})", listed(comparison.joined_late))
    return embed


def mention_list(users: list[int], max_chars: int) -> str:
    """`users` as comma-separated mentions, in at most `max_chars`. If they don't all fit, the list is cut
    short and ends with a line saying how many were left off."""
    text = ""
    for listed, user_id in enumerate(users):
        mention = f"<@{user_id}>"
        separator = "" if listed == 0 else ", "
        overflow_note = f"\n…and {len(users) - listed} more."
        # Always keep room for the note, so the count of who was left off is never itself cut.
        if utf8_length(text) + len(separator) + len(mention) + utf8_length(overflow_note) > max_chars:
            text += overflow_note
            break
        text += separator + mention
    return text
