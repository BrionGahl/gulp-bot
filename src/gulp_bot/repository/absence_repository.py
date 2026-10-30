"""The `raid_absences` and `officer_channel_notes` tables. Every lookup by ID is scoped to a guild."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from ..core.database import rows_affected, to_bigint, to_bigint_or_none
from ..mapper.absence_mapper import absence_from_row, note_from_row
from ..schema.absence_schema import Absence, ChannelNote, NewAbsence, NewChannelNote

if TYPE_CHECKING:
    import asyncpg

ABSENCE_COLUMNS = "id, guild_id, user_id, start_date, end_date, kind, note, created_by, source_message_id"
NOTE_COLUMNS = "id, user_id, channel_id, source_message_id, category, summary, created_at"


class AbsenceRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # --- Absences ---

    async def insert(self, absence: NewAbsence) -> Absence:
        row = await self._pool.fetchrow(
            "INSERT INTO raid_absences (guild_id, user_id, start_date, end_date, kind, note, created_by, source_message_id) "
            f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING {ABSENCE_COLUMNS}",
            to_bigint(absence.guild_id),
            to_bigint(absence.user_id),
            absence.start,
            absence.end,
            absence.kind.value,
            absence.note,
            to_bigint(absence.created_by),
            to_bigint_or_none(absence.source_message_id),
        )
        return absence_from_row(row)

    async def find(self, guild_id: int, absence_id: int) -> Absence | None:
        row = await self._pool.fetchrow(f"SELECT {ABSENCE_COLUMNS} FROM raid_absences WHERE id = $1 AND guild_id = $2", absence_id, to_bigint(guild_id))
        return None if row is None else absence_from_row(row)

    async def delete(self, guild_id: int, absence_id: int) -> Absence | None:
        """Deletes the absence and returns it, or `None` if `guild_id` has none with that ID."""
        row = await self._pool.fetchrow(
            f"DELETE FROM raid_absences WHERE id = $1 AND guild_id = $2 RETURNING {ABSENCE_COLUMNS}", absence_id, to_bigint(guild_id)
        )
        return None if row is None else absence_from_row(row)

    async def delete_for_message(self, message_id: int) -> list[Absence]:
        """Deletes the absences read from the message `message_id`, and returns them."""
        rows = await self._pool.fetch(f"DELETE FROM raid_absences WHERE source_message_id = $1 RETURNING {ABSENCE_COLUMNS}", to_bigint(message_id))
        return [absence_from_row(row) for row in rows]

    async def overlapping(self, guild_id: int, first: date, last: date) -> list[Absence]:
        """`guild_id`'s absences that cover any day from `first` to `last`, both included, earliest first."""
        rows = await self._pool.fetch(
            f"SELECT {ABSENCE_COLUMNS} FROM raid_absences WHERE guild_id = $1 AND start_date <= $3 AND end_date >= $2 ORDER BY start_date, id",
            to_bigint(guild_id),
            first,
            last,
        )
        return [absence_from_row(row) for row in rows]

    async def upcoming(self, guild_id: int, today: date) -> list[Absence]:
        """Every absence of `guild_id` that hasn't ended before `today`, earliest first."""
        rows = await self._pool.fetch(
            f"SELECT {ABSENCE_COLUMNS} FROM raid_absences WHERE guild_id = $1 AND end_date >= $2 ORDER BY start_date, user_id, id",
            to_bigint(guild_id),
            today,
        )
        return [absence_from_row(row) for row in rows]

    async def upcoming_for_member(self, guild_id: int, user_id: int, today: date) -> list[Absence]:
        """The member's absences that haven't ended before `today`, earliest first."""
        rows = await self._pool.fetch(
            f"SELECT {ABSENCE_COLUMNS} FROM raid_absences WHERE guild_id = $1 AND user_id = $2 AND end_date >= $3 ORDER BY start_date, id",
            to_bigint(guild_id),
            to_bigint(user_id),
            today,
        )
        return [absence_from_row(row) for row in rows]

    async def delete_ended_before(self, guild_id: int, day: date) -> int:
        """Deletes `guild_id`'s absences that ended before `day`. Returns how many there were."""
        return rows_affected(await self._pool.execute("DELETE FROM raid_absences WHERE guild_id = $1 AND end_date < $2", to_bigint(guild_id), day))

    # --- Personal officer channel notes ---

    async def insert_note(self, note: NewChannelNote) -> None:
        await self._pool.execute(
            "INSERT INTO officer_channel_notes (guild_id, user_id, channel_id, source_message_id, category, summary) VALUES ($1, $2, $3, $4, $5, $6)",
            to_bigint(note.guild_id),
            to_bigint(note.user_id),
            to_bigint(note.channel_id),
            to_bigint(note.source_message_id),
            note.category.value,
            note.summary,
        )

    async def delete_notes_for_message(self, message_id: int) -> int:
        """Deletes the notes read from the message `message_id`. Returns how many there were."""
        return rows_affected(await self._pool.execute("DELETE FROM officer_channel_notes WHERE source_message_id = $1", to_bigint(message_id)))

    async def notes_between(self, guild_id: int, since: datetime, until: datetime) -> list[ChannelNote]:
        """`guild_id`'s notes written from `since` up to (not including) `until`, oldest first."""
        rows = await self._pool.fetch(
            f"SELECT {NOTE_COLUMNS} FROM officer_channel_notes WHERE guild_id = $1 AND created_at >= $2 AND created_at < $3 ORDER BY created_at, id",
            to_bigint(guild_id),
            since,
            until,
        )
        return [note_from_row(row) for row in rows]

    async def delete_notes_before(self, guild_id: int, moment: datetime) -> int:
        """Deletes `guild_id`'s notes written before `moment`. Returns how many there were."""
        return rows_affected(await self._pool.execute("DELETE FROM officer_channel_notes WHERE guild_id = $1 AND created_at < $2", to_bigint(guild_id), moment))
