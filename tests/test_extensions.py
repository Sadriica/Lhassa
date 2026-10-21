# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capability-pack extension points: manifests, surface registries, pack migrations."""

from __future__ import annotations

import sqlite3

import pytest

from lhassa import extensions
from lhassa.core import db
from lhassa.core.migrations import (
    Migration,
    UnsupportedSchemaVersion,
    apply_pack_migrations,
)
from lhassa._vendor import aiosqlite


def test_register_pack_is_idempotent():
    name = "test-pack-idem"
    try:
        first = extensions.register_pack(name, "one")
        second = extensions.register_pack(name, "two")
        assert first is second and second.summary == "one"
    finally:
        extensions.PACKS.unregister(name)


def test_surface_registration_first_wins():
    name = "test-surface"
    try:
        a = lambda: "a"  # noqa: E731
        b = lambda: "b"  # noqa: E731
        extensions.register_cli_app(name, a)
        extensions.register_cli_app(name, b)
        assert extensions.CLI_APPS.get(name)() == "a"
    finally:
        extensions.CLI_APPS.unregister(name)


async def _create_widgets(conn):
    await conn.execute("CREATE TABLE pack_widgets (id INTEGER PRIMARY KEY)")


async def _add_color(conn):
    await conn.execute("ALTER TABLE pack_widgets ADD COLUMN color TEXT")


MIGS = (
    Migration(1, "widgets", _create_widgets),
    Migration(2, "color", _add_color),
)


async def test_pack_migrations_apply_once_and_track_per_pack(tmp_path):
    path = tmp_path / "packs.db"
    async with aiosqlite.connect(path) as conn:
        assert await apply_pack_migrations(conn, "widgets", MIGS) == 2
        # Re-running is a no-op, not a second ALTER.
        assert await apply_pack_migrations(conn, "widgets", MIGS) == 2
        # A newer stamp than this build supports is refused, never "downgraded".
        with pytest.raises(UnsupportedSchemaVersion):
            await apply_pack_migrations(conn, "widgets", MIGS[:1])
    con = sqlite3.connect(path)
    assert con.execute("SELECT pack, version FROM pack_schema").fetchall() == [("widgets", 2)]
    assert [r[1] for r in con.execute("PRAGMA table_info(pack_widgets)")] == ["id", "color"]
    # The core user_version track is untouched by a pack.
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0


async def test_init_db_runs_registered_pack_migrations(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    extensions.register_pack_migrations("test-widgets", MIGS)
    try:
        await db.init_db()
        con = sqlite3.connect(tmp_path / "brain.db")
        assert con.execute(
            "SELECT version FROM pack_schema WHERE pack = 'test-widgets'"
        ).fetchone() == (2,)
    finally:
        extensions.PACK_MIGRATIONS.unregister("test-widgets")


def test_cli_command_registration_first_wins():
    name = "test-command"
    try:
        a = lambda: "a"  # noqa: E731
        b = lambda: "b"  # noqa: E731
        extensions.register_cli_command(name, a)
        extensions.register_cli_command(name, b)
        assert extensions.CLI_COMMANDS.get(name)() == "a"
    finally:
        extensions.CLI_COMMANDS.unregister(name)
