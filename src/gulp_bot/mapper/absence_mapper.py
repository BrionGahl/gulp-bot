"""Absences and personal officer channel notes into the text members and officers read."""

from __future__ import annotations

from datetime import date, timedelta

from ..core.schedule_parsing import SHORT_DAY_NAMES
from ..core.text import DISCORD_MAX_CHARS, limited_lines, single_line, truncate_chars
from ..schema.absence_schema import Absence, ChannelNote, NoteCategory
from ..schema.guild_settings_schema import RaidNight

# Room kept under Discord's limit for the overflow note.
DIGEST_MAX_CHARS = DISCORD_MAX_CHARS - 40

# The longest note shown after an absence in the digest.
MAX_NOTE_CHARS = 80


def day_text(day: date) -> str:
    """`Tue Nov 4`."""
    return f"{SHORT_DAY_NAMES[day.weekday()]} {day:%b} {day.day}"


def span_text(start: date, end: date) -> str:
    """`Tue Nov 4`, or `Tue Nov 4 – Thu Nov 6`."""
    return day_text(start) if start == end else f"{day_text(start)} – {day_text(end)}"


def _note(note: str | None) -> str:
    return "" if not note else f" ({truncate_chars(single_line(note), MAX_NOTE_CHARS)})"


def absence_text(absence: Absence) -> str:
    """`out Tue Nov 4 – Thu Nov 6 (wedding)`, as a member reads their own."""
    return f"{absence.kind.label} {span_text(absence.start, absence.end)}{_note(absence.note)}"


def confirmation(absence: Absence, nights: list[RaidNight]) -> str:
    """What the member is told when an absence is recorded, with the raid nights it covers."""
    covered = ", ".join(day_text(night.start.date()) for night in nights)
    return f"Noted: {absence_text(absence)}. Raid nights: {covered}."


def digest_text(tuesday: date, nights: list[RaidNight] | None, absences: list[Absence], notes: list[ChannelNote]) -> str:
    """The officer digest of the week starting `tuesday`: who is out or tentative on each of its raid
    nights (`nights`, `None` if none are registered), and what members raised in their personal officer
    channels."""
    monday = tuesday + timedelta(days=6)
    lines = [f"**Officer digest** — week of {day_text(tuesday)} – {day_text(monday)}\n", "\n**Absences**\n"]
    if nights is None:
        lines.append("Raid nights aren't set (`/settings raid_nights`), so absences can't be matched to them.\n")
        lines += [f"<@{absence.user_id}> {absence_text(absence)}\n" for absence in absences]
    elif not nights:
        lines.append("No raid nights this week.\n")
    for night in nights or []:
        day = night.start.date()
        on_night = [absence for absence in absences if absence.start <= day <= absence.end]
        who = " · ".join(f"<@{absence.user_id}> {absence.kind.label}{_note(absence.note)}" for absence in on_night)
        lines.append(f"{day_text(day)}: {who or 'nobody'}\n")

    lines.append("\n**From personal channels**\n")
    if not notes:
        lines.append("Nothing this week.\n")
    for category in NoteCategory:
        lines += [f"{category.label} — <@{note.user_id}> in <#{note.channel_id}>: {single_line(note.summary)}\n" for note in notes if note.category is category]
    return limited_lines(lines, DIGEST_MAX_CHARS).rstrip()


def member_list_text(absences: list[Absence]) -> str:
    """A member's upcoming absences, as `/absence list` shows them."""
    if not absences:
        return "You have no upcoming absences. Add one with `/absence add`."
    return "\n".join(f"**#{absence.id}** {absence_text(absence)}" for absence in absences)
