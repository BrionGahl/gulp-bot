"""The database migrations, applied as `sqlx` applied them.

The schema is the one the Rust bot created, and so is the migrations table: migrations are recorded in
`_sqlx_migrations` exactly as `sqlx` recorded them (version, description, SHA-384 checksum), so this bot
runs on a database the Rust bot set up, and the other way round.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

log = logging.getLogger(__name__)

# `migrations/` at the root of the repository (and of the image), next to `src/`: this file is
# `src/gulp_bot/core/migrations.py`.
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

_FILE_NAME = re.compile(r"^(\d+)_(.+)\.sql$")


class MigrationError(RuntimeError):
    """The database can't be migrated safely. The message says why."""


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    sql: str
    checksum: bytes


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """The migrations in `directory`, oldest first, read as `sqlx` reads them: the version is the number
    before the first `_`, the description the rest of the name with `_` as spaces, and the checksum the
    SHA-384 of the file."""
    migrations = []
    for path in directory.iterdir():
        match = _FILE_NAME.match(path.name)
        if match is None:
            continue
        # As bytes: reading as text would turn CRLF into LF, and change the checksum.
        sql = path.read_bytes().decode("utf-8")
        migrations.append(
            Migration(
                version=int(match.group(1)),
                description=match.group(2).replace("_", " "),
                sql=sql,
                checksum=hashlib.sha384(sql.encode("utf-8")).digest(),
            )
        )
    migrations.sort(key=lambda migration: migration.version)
    return migrations


def same_checksum(applied: bytes, migration: Migration) -> bool:
    """Whether `applied` is the checksum of `migration`, with either line ending: a checkout on Windows has
    CRLF where the repository has LF, and the image may have been built from either."""
    if applied == migration.checksum:
        return True
    lf = migration.sql.replace("\r\n", "\n")
    crlf = lf.replace("\n", "\r\n")
    return applied in (hashlib.sha384(lf.encode("utf-8")).digest(), hashlib.sha384(crlf.encode("utf-8")).digest())


def lock_id(database_name: str) -> int:
    """The advisory lock `sqlx` takes while migrating, so that it and this never migrate at once."""
    return 0x3D32AD9E * zlib.crc32(database_name.encode("utf-8"))


async def run_migrations(pool: asyncpg.Pool, migrations: list[Migration] | None = None) -> None:
    """Applies the migrations the database doesn't have yet. Refuses, as `sqlx` does, a database with a
    migration that failed half way, one this bot doesn't know (it was migrated by a newer version), or one
    whose file has changed since it was applied."""
    migrations = load_migrations() if migrations is None else migrations

    async with pool.acquire() as connection:
        database = await connection.fetchval("SELECT current_database()")
        lock = lock_id(database)
        await connection.execute("SELECT pg_advisory_lock($1)", lock)
        try:
            await _create_migrations_table(connection)
            applied = await _applied_checksums(connection)
            _refuse_unsafe_database(applied, migrations)
            for migration in migrations:
                if migration.version not in applied:
                    await _apply(connection, migration)
        finally:
            await connection.execute("SELECT pg_advisory_unlock($1)", lock)


async def _create_migrations_table(connection: asyncpg.Connection) -> None:
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS _sqlx_migrations (
            version BIGINT PRIMARY KEY,
            description TEXT NOT NULL,
            installed_on TIMESTAMPTZ NOT NULL DEFAULT now(),
            success BOOLEAN NOT NULL,
            checksum BYTEA NOT NULL,
            execution_time BIGINT NOT NULL
        )
        """
    )
    dirty = await connection.fetchval("SELECT version FROM _sqlx_migrations WHERE success = false ORDER BY version LIMIT 1")
    if dirty is not None:
        raise MigrationError(f"Migration {dirty} was partially applied; fix the database and delete its row in _sqlx_migrations.")


async def _applied_checksums(connection: asyncpg.Connection) -> dict[int, bytes]:
    rows = await connection.fetch("SELECT version, checksum FROM _sqlx_migrations ORDER BY version")
    return {row["version"]: bytes(row["checksum"]) for row in rows}


def _refuse_unsafe_database(applied: dict[int, bytes], migrations: list[Migration]) -> None:
    known = {migration.version: migration for migration in migrations}
    for version in applied:
        if version not in known:
            raise MigrationError(f"Migration {version} was applied to the database but is missing here: the database is newer than this bot.")
    for migration in migrations:
        if migration.version in applied and not same_checksum(applied[migration.version], migration):
            raise MigrationError(f"Migration {migration.version} was previously applied but has been modified.")


async def _apply(connection: asyncpg.Connection, migration: Migration) -> None:
    started = time.monotonic_ns()
    async with connection.transaction():
        await connection.execute(migration.sql)
        await connection.execute(
            "INSERT INTO _sqlx_migrations (version, description, success, checksum, execution_time) VALUES ($1, $2, TRUE, $3, -1)",
            migration.version,
            migration.description,
            migration.checksum,
        )
    await connection.execute(
        "UPDATE _sqlx_migrations SET execution_time = $1 WHERE version = $2",
        time.monotonic_ns() - started,
        migration.version,
    )
    log.info("Applied migration %d (%s)", migration.version, migration.description)
