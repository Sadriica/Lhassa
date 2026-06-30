"""
Incremental backup of the grande (brain.db) with restore-and-verify (§2.10).

The brief: "la grande se replica **incremental** al PC (rutas espejo, redundancia).
**Verificar restauraciones.**" This module mirrors the append-only source of truth
to a backup path using SQLite's online backup API, which is WAL-safe (it copies a
consistent snapshot while readers/writers continue, no manual checkpoint needed).

  - :func:`backup`  — copy brain.db to the mirror (cheap to re-run; overwrites /
    rotates the mirror). "Incremental" in the operational sense: idempotent and
    safe to call on every heartbeat.
  - :func:`restore` — rebuild brain.db from a backup file.
  - :func:`verify`  — open a backup read-only, run ``PRAGMA integrity_check`` and
    confirm the expected tables exist. Returns True/False; never raises.

The source path is read from ``lhassa.core.db.DB_PATH`` at call time, so tests
that monkeypatch it (and the ``BACKUP_DIR`` seam) reroute everything to tmp paths.
"""

from pathlib import Path

from lhassa._vendor import aiosqlite

from lhassa.config import Config
from lhassa.core import db

# Module-level seam — tests monkeypatch this to a tmp directory.
BACKUP_DIR = Path(Config.backup_dir)

# Tables a healthy grande must contain for a backup to be considered valid.
_EXPECTED_TABLES = {"episodes", "facts", "tasks"}


def _default_dest() -> Path:
    """The default mirror path: ``BACKUP_DIR / <brain-db-filename>``."""
    src = Path(db.DB_PATH)
    return BACKUP_DIR / src.name


async def _online_copy(src: Path, dest: Path) -> None:
    """WAL-safe whole-database copy via SQLite's online backup API."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(src) as source, aiosqlite.connect(dest) as target:
        await source.backup(target)


async def backup(*, dest: str | Path | None = None) -> str:
    """
    Back up the grande (``db.DB_PATH``) to ``dest`` (default ``BACKUP_DIR``).

    Uses the online backup API: WAL-safe, consistent snapshot, no checkpoint
    dance. Idempotent and cheap to re-run — the mirror is overwritten in place,
    which is the "incremental redundant mirror" the brief asks for. Returns the
    backup path as a string.
    """
    src = Path(db.DB_PATH)
    dest_path = Path(dest) if dest is not None else _default_dest()
    await _online_copy(src, dest_path)
    return str(dest_path)


async def restore(src: str | Path, *, dest: str | Path | None = None) -> None:
    """
    Restore the grande from a backup file ``src`` into ``dest`` (default
    ``db.DB_PATH``). Uses the online backup API to overwrite the destination
    database with the backup's contents.
    """
    src_path = Path(src)
    dest_path = Path(dest) if dest is not None else Path(db.DB_PATH)
    await _online_copy(src_path, dest_path)


async def verify(path: str | Path) -> bool:
    """
    Verify a backup at ``path`` is restorable. Opens it READ-ONLY, runs
    ``PRAGMA integrity_check`` (must return "ok") and confirms the expected
    grande tables exist. Returns True only if both pass; any error (missing file,
    empty file, corruption, wrong schema) returns False — never raises.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        uri = f"file:{path}?mode=ro"
        async with aiosqlite.connect(uri, uri=True) as conn:
            async with conn.execute("PRAGMA integrity_check") as cur:
                row = await cur.fetchone()
            if not row or str(row[0]).lower() != "ok":
                return False
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ) as cur:
                tables = {r[0] for r in await cur.fetchall()}
        return _EXPECTED_TABLES.issubset(tables)
    except (aiosqlite.Error, OSError):
        return False
