"""The Postgres connection pool, and Discord IDs as they are stored in it."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

# Discord IDs are unsigned 64-bit, Postgres BIGINT is signed. An ID above the signed maximum is stored as
# the negative number with the same bits (two's complement), and read back from it.
_U64 = 1 << 64
_I64_MAX = (1 << 63) - 1


def to_bigint(discord_id: int) -> int:
    """A Discord ID as it is stored in a BIGINT column."""
    return discord_id - _U64 if discord_id > _I64_MAX else discord_id


def from_bigint(value: int) -> int:
    """A Discord ID read back from a BIGINT column."""
    return value + _U64 if value < 0 else value


def to_bigint_or_none(discord_id: int | None) -> int | None:
    return None if discord_id is None else to_bigint(discord_id)


def from_bigint_or_none(value: int | None) -> int | None:
    return None if value is None else from_bigint(value)


def rows_affected(status: str) -> int:
    """How many rows a command touched, from the status asyncpg returns (`DELETE 1`, `INSERT 0 1`)."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def connect(database_url: str) -> asyncpg.Pool:
    """A pool of connections to the database at `database_url`."""
    import asyncpg

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    if pool is None:
        raise RuntimeError("Failed to connect to the database at `DATABASE_URL`.")
    return pool
