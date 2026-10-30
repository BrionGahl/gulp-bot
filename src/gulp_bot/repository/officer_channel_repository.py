"""The `personal_officer_channels` table: which members have a personal officer channel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.database import from_bigint, from_bigint_or_none, rows_affected, to_bigint
from ..schema.absence_schema import OfficerChannelOwner
from ..schema.officer_channel_schema import RegisteredOfficerChannel

if TYPE_CHECKING:
    import asyncpg


class OfficerChannelRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def claim(self, guild_id: int, user_id: int) -> bool:
        """Inserts the row for this member if they don't have one. Returns whether it was inserted, i.e.
        whether the caller now owns creating their channel. The insert is atomic, so of any number of
        callers racing for the same member exactly one gets `True`."""
        status = await self._pool.execute(
            "INSERT INTO personal_officer_channels (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            to_bigint(guild_id),
            to_bigint(user_id),
        )
        return rows_affected(status) == 1

    async def save_channel(self, guild_id: int, user_id: int, channel_id: int) -> None:
        """Records `channel_id` as this member's channel, replacing whatever was recorded before."""
        await self._pool.execute(
            "INSERT INTO personal_officer_channels (guild_id, user_id, channel_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET channel_id = EXCLUDED.channel_id",
            to_bigint(guild_id),
            to_bigint(user_id),
            to_bigint(channel_id),
        )

    async def delete(self, guild_id: int, user_id: int) -> bool:
        """Returns whether the member had a row."""
        status = await self._pool.execute("DELETE FROM personal_officer_channels WHERE guild_id = $1 AND user_id = $2", to_bigint(guild_id), to_bigint(user_id))
        return rows_affected(status) == 1

    async def delete_channel(self, guild_id: int, channel_id: int) -> bool:
        """Forgets whichever member's row records `channel_id` as their channel. Returns whether there was
        one."""
        status = await self._pool.execute(
            "DELETE FROM personal_officer_channels WHERE guild_id = $1 AND channel_id = $2", to_bigint(guild_id), to_bigint(channel_id)
        )
        return rows_affected(status) > 0

    async def owners(self) -> dict[int, OfficerChannelOwner]:
        """Every recorded channel, by its ID, with whose it is."""
        rows = await self._pool.fetch("SELECT guild_id, user_id, channel_id FROM personal_officer_channels WHERE channel_id IS NOT NULL")
        return {from_bigint(row["channel_id"]): OfficerChannelOwner(from_bigint(row["guild_id"]), from_bigint(row["user_id"])) for row in rows}

    async def list_for_guild(self, guild_id: int) -> list[RegisteredOfficerChannel]:
        """Every member of `guild_id` with a row, oldest first."""
        rows = await self._pool.fetch(
            "SELECT user_id, channel_id FROM personal_officer_channels WHERE guild_id = $1 ORDER BY created_at, user_id",
            to_bigint(guild_id),
        )
        return [RegisteredOfficerChannel(from_bigint(row["user_id"]), from_bigint_or_none(row["channel_id"])) for row in rows]
