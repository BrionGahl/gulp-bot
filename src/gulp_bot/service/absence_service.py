"""Absences members report for raid nights, what else they raise in their personal officer channels, and
the officer digest that lists both in each week's raid notes thread.

The digest is posted when the raid notes schedule makes the week's thread (`raid_notes_job`), and edited
whenever an absence or a note of that week is added or taken back, so it stays current until raid night.
Absences that ended before the week, and the notes of earlier weeks, are deleted at the same time.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import discord

from ..core.discord_helpers import NO_MENTIONS
from ..core.schedule_parsing import NEW_YORK, now_in_new_york
from ..mapper.absence_mapper import digest_text
from ..repository.absence_repository import AbsenceRepository
from ..schema.absence_schema import Absence, NewAbsence, NewChannelNote
from ..schema.guild_settings_schema import RaidNight
from .guild_settings_service import GuildSettingsService
from .raid_nights_service import nights_between, nights_in_week, week_start
from .raid_notes_service import RaidNotesService

log = logging.getLogger(__name__)

# How far ahead an absence may be reported.
MAX_DAYS_AHEAD = 8 * 7


def span_problem(start: date, end: date, today: date) -> str | None:
    """Why an absence from `start` to `end` can't be recorded on `today`, or `None` if it can."""
    if end < start:
        return "The absence ends before it starts."
    if end < today:
        return "That's in the past."
    if start > today + timedelta(days=MAX_DAYS_AHEAD):
        return f"Absences can be reported up to {MAX_DAYS_AHEAD // 7} weeks ahead."
    return None


def week_begins(tuesday: date) -> datetime:
    """Midnight Eastern at the start of the week beginning `tuesday`."""
    return datetime.combine(tuesday, datetime.min.time(), NEW_YORK)


class AbsenceService:
    def __init__(self, repository: AbsenceRepository, guild_settings: GuildSettingsService, raid_notes: RaidNotesService):
        self._repository = repository
        self._guild_settings = guild_settings
        self._raid_notes = raid_notes

    def nights_covered(self, guild_id: int, start: date, end: date) -> list[RaidNight] | None:
        """The guild's raid nights from `start` to `end`, or `None` if it hasn't registered any."""
        nights = self._guild_settings.get(guild_id).raid_nights
        return None if nights is None else nights_between(nights, start, end)

    # --- Absences ---

    async def add(self, absence: NewAbsence) -> Absence:
        """Records `absence`, and updates the digest of every week it falls in."""
        stored = await self._repository.insert(absence)
        log.info("Recorded absence #%s of %s in guild %s: %s to %s", stored.id, stored.user_id, stored.guild_id, stored.start, stored.end)
        await self._refresh_weeks(stored.guild_id, stored.start, stored.end)
        return stored

    async def find(self, guild_id: int, absence_id: int) -> Absence | None:
        return await self._repository.find(guild_id, absence_id)

    async def remove(self, guild_id: int, absence_id: int) -> Absence | None:
        """Deletes the absence and updates the digests it was in. Returns it, or `None` if there was none."""
        removed = await self._repository.delete(guild_id, absence_id)
        if removed is not None:
            log.info("Removed absence #%s of %s in guild %s", removed.id, removed.user_id, guild_id)
            await self._refresh_weeks(guild_id, removed.start, removed.end)
        return removed

    async def upcoming_for_member(self, guild_id: int, user_id: int) -> list[Absence]:
        return await self._repository.upcoming_for_member(guild_id, user_id, now_in_new_york().date())

    async def week(self, guild_id: int, tuesday: date) -> list[Absence]:
        """The absences that cover any day of the week starting `tuesday`."""
        return await self._repository.overlapping(guild_id, tuesday, tuesday + timedelta(days=6))

    # --- Personal officer channel notes ---

    async def add_notes(self, guild_id: int, notes: list[NewChannelNote]) -> None:
        """Records what a member raised, and updates this week's digest."""
        for note in notes:
            await self._repository.insert_note(note)
        if notes:
            await self.refresh_digest(guild_id, now_in_new_york().date())

    async def forget_message(self, guild_id: int, message_id: int) -> bool:
        """Deletes everything read from the message `message_id` (it was deleted, or edited and is read
        again), and updates the digests it was in. Returns whether there was anything."""
        absences = await self._repository.delete_for_message(message_id)
        notes = await self._repository.delete_notes_for_message(message_id)
        for absence in absences:
            await self._refresh_weeks(guild_id, absence.start, absence.end)
        if notes:
            await self.refresh_digest(guild_id, now_in_new_york().date())
        return bool(absences or notes)

    # --- The weekly digest ---

    async def digest(self, guild_id: int, tuesday: date) -> str:
        """The officer digest of the week starting `tuesday`, as it is posted."""
        nights = self._guild_settings.get(guild_id).raid_nights
        absences = await self.week(guild_id, tuesday)
        begins = week_begins(tuesday)
        notes = await self._repository.notes_between(guild_id, begins, week_begins(tuesday + timedelta(days=7)))
        return digest_text(tuesday, None if nights is None else nights_in_week(nights, tuesday), absences, notes)

    async def post_digest(self, guild_id: int, thread_id: int, day: date) -> None:
        """Posts the digest of the week containing `day` in its thread, or brings the one already posted up to
        date: the raid notes schedule may make the week's post more than once."""
        tuesday = week_start(day)
        text = await self.digest(guild_id, tuesday)
        thread = self._raid_notes.thread_messageable(guild_id, thread_id)
        existing = await self._raid_notes.find_digest(guild_id, tuesday)
        if existing is not None and await self._edit(thread, existing, text):
            return
        message = await thread.send(text, allowed_mentions=NO_MENTIONS)
        await self._raid_notes.save_digest(guild_id, tuesday, message.id)
        log.info("Posted the officer digest of the week of %s for guild %s", tuesday, guild_id)

    async def refresh_digest(self, guild_id: int, day: date) -> None:
        """Brings the digest of the week containing `day` up to date, if the week has a thread: edits it, or
        posts it if it isn't there yet (its post failed, or the thread was made early, for attendance). A
        week without a thread gets an up-to-date digest when it is made."""
        thread_id = await self._raid_notes.find_week_thread(guild_id, day)
        if thread_id is not None:
            await self.post_digest(guild_id, thread_id, day)

    async def clean_up(self, guild_id: int, day: date) -> None:
        """Deletes the absences that ended before the week containing `day`, and the notes of earlier weeks:
        their digests have been posted, and nothing reads them again."""
        tuesday = week_start(day)
        absences = await self._repository.delete_ended_before(guild_id, tuesday)
        notes = await self._repository.delete_notes_before(guild_id, week_begins(tuesday))
        if absences or notes:
            log.info("Cleaned up %d old absence(s) and %d old note(s) for guild %s", absences, notes, guild_id)

    async def _refresh_weeks(self, guild_id: int, start: date, end: date) -> None:
        tuesday = week_start(start)
        while tuesday <= end:
            await self.refresh_digest(guild_id, tuesday)
            tuesday += timedelta(days=7)

    async def _edit(self, thread: discord.PartialMessageable, message_id: int, text: str) -> bool:
        """Edits the digest message. Returns whether it was there to edit."""
        try:
            await thread.get_partial_message(message_id).edit(content=text, allowed_mentions=NO_MENTIONS)
            return True
        except discord.NotFound:
            return False
        except discord.HTTPException as e:
            log.warning("Couldn't update the officer digest %s: %s", message_id, e)
            return True
