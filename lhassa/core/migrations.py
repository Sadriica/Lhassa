# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small, transactional SQLite schema migration runner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any


MigrationStep = Callable[[Any], Awaitable[None]]


class UnsupportedSchemaVersion(RuntimeError):
    """Raised when a database was written by a newer Lhassa version."""


class MigrationValidationError(RuntimeError):
    """Raised when a migration did not produce its promised schema."""


@dataclass(frozen=True)
class Migration:
    """One forward-only schema migration."""

    version: int
    name: str
    upgrade: MigrationStep
    validate: MigrationStep | None = None


async def get_schema_version(conn: Any) -> int:
    """Return SQLite's application-owned schema version stamp."""
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


def ensure_supported(version: int, target_version: int) -> None:
    """Refuse forward-incompatible databases instead of modifying them."""
    if version < 0:
        raise MigrationValidationError(
            f"database schema version {version} is invalid"
        )
    if version > target_version:
        raise UnsupportedSchemaVersion(
            f"database schema version {version} is newer than supported "
            f"version {target_version}; upgrade Lhassa or restore a compatible backup"
        )


def _validate_registry(migrations: Sequence[Migration]) -> None:
    versions = [migration.version for migration in migrations]
    expected = list(range(1, len(migrations) + 1))
    if versions != expected:
        raise ValueError(
            f"migration versions must be contiguous and ordered: "
            f"expected {expected}, got {versions}"
        )


async def validate_schema(
    conn: Any,
    migrations: Sequence[Migration],
    *,
    version: int | None = None,
) -> int:
    """Run every validator registered up to the database's current version."""
    _validate_registry(migrations)
    target_version = migrations[-1].version if migrations else 0
    current_version = (
        await get_schema_version(conn) if version is None else version
    )
    ensure_supported(current_version, target_version)
    for migration in migrations[:current_version]:
        if migration.validate is not None:
            await migration.validate(conn)
    return current_version


async def apply_migrations(conn: Any, migrations: Sequence[Migration]) -> int:
    """Apply pending migrations atomically and return the resulting version.

    Each migration gets its own immediate transaction. The version is read
    again after acquiring the write lock so concurrent initializers cannot
    apply the same migration from a stale observation.
    """
    _validate_registry(migrations)
    target_version = migrations[-1].version if migrations else 0

    while True:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            version = await get_schema_version(conn)
            ensure_supported(version, target_version)
            if version == target_version:
                await validate_schema(conn, migrations, version=version)
                await conn.commit()
                return version

            migration = migrations[version]
            await migration.upgrade(conn)
            if migration.validate is not None:
                await migration.validate(conn)
            await conn.execute(f"PRAGMA user_version = {migration.version}")
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise


# ── per-pack migration tracks ────────────────────────────────────────────────
# A capability pack owns its tables. Its migrations run on their own version
# track, one row per pack in ``pack_schema``, so installing or removing a pack
# never shifts the core ``PRAGMA user_version`` sequence.

_PACK_SCHEMA_DDL = (
    "CREATE TABLE IF NOT EXISTS pack_schema ("
    " pack TEXT PRIMARY KEY,"
    " version INTEGER NOT NULL"
    ")"
)


async def get_pack_version(conn: Any, pack: str) -> int:
    """Return the applied migration version for ``pack`` (0 when never run)."""
    await conn.execute(_PACK_SCHEMA_DDL)
    async with conn.execute(
        "SELECT version FROM pack_schema WHERE pack = ?", (pack,)
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def apply_pack_migrations(
    conn: Any, pack: str, migrations: Sequence[Migration]
) -> int:
    """Apply ``pack``'s pending migrations atomically; return its version.

    Same contract as :func:`apply_migrations` (contiguous versions, one
    immediate transaction per step, newer-than-supported is refused), with the
    version stamp kept in ``pack_schema`` instead of ``user_version``.
    """
    _validate_registry(migrations)
    target_version = migrations[-1].version if migrations else 0
    await conn.execute(_PACK_SCHEMA_DDL)
    await conn.commit()

    while True:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            version = await get_pack_version(conn, pack)
            ensure_supported(version, target_version)
            if version == target_version:
                for migration in migrations[:version]:
                    if migration.validate is not None:
                        await migration.validate(conn)
                await conn.commit()
                return version

            migration = migrations[version]
            await migration.upgrade(conn)
            if migration.validate is not None:
                await migration.validate(conn)
            await conn.execute(
                "INSERT INTO pack_schema (pack, version) VALUES (?, ?) "
                "ON CONFLICT(pack) DO UPDATE SET version = excluded.version",
                (pack, migration.version),
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
