"""
Async SQLite store for the "grande" — the append-only source of truth.

This is the durable brain DB (`~/.lhassa/brain.db` by default), separate from
the conversational memory DB in ``lhassa.memory.store``. It is single-writer by
discipline; WAL lets readers run freely against the immutable log.

Schema (Apéndice A of the brief):
  episodes      — what happened (APPEND-ONLY; never UPDATE/DELETE)
  tasks         — typed, signed work items
  jobs          — long-running harness processes (PID bookkeeping)
  desired_state — per-machine reconciliation target (check-in, not push)
  tools         — self-written tools and their gate metrics (APPEND row, metrics update)
  approvals     — the human-in-the-loop queue

Invariants:
  - EVERY table carries an ``instance_id`` column.
  - EVERY query filters by ``instance_id`` (isolation from day one).
  - ``episodes`` and ``tools`` rows are append-only as source of truth; episodes
    are never mutated, tool *metric counters* are the only allowed in-place update.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from lhassa.config import Config

# Module-level so tests can monkeypatch it the way test_memory_store patches
# lhassa.memory.store.DB_PATH.
DB_PATH = Path(Config.brain_db_path)


# ── small helpers ───────────────────────────────────────────────────────────────

def _now() -> str:
    """Current time as an ISO 8601 UTC string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    """Generate a short, prefixed, collision-resistant id."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _dumps(value: Any) -> str:
    """Serialize a list/dict field for a column. ``None`` becomes an empty JSON."""
    if value is None:
        value = []
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: Optional[str], default: Any) -> Any:
    """Deserialize a JSON column, falling back to ``default`` on empty/garbage."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


# ── schema ──────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """
    Enable WAL and create every table. Idempotent — safe to call repeatedly.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")

        # LA GRANDE — append-only; never UPDATE/DELETE episodes.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id               TEXT PRIMARY KEY,
                instance_id      TEXT NOT NULL,
                creado_en        TEXT NOT NULL,
                despertar_por    TEXT,
                decision         TEXT,
                accion           TEXT,
                resultado        TEXT,
                dominio          TEXT,
                costo_usd        REAL,
                memoria_cargada  TEXT,
                raw              TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS episodes_by_instance
            ON episodes(instance_id, creado_en)
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id           TEXT PRIMARY KEY,
                instance_id  TEXT NOT NULL,
                accion       TEXT NOT NULL,
                objetivo     TEXT,
                params       TEXT,
                origen_trust INTEGER,
                origen_actor TEXT,
                estado       TEXT NOT NULL DEFAULT 'pendiente',
                firma        TEXT,
                creado_en    TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS tasks_pending
            ON tasks(instance_id, estado)
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id           TEXT PRIMARY KEY,
                instance_id  TEXT NOT NULL,
                pid          INTEGER,
                harness      TEXT,
                estado       TEXT NOT NULL DEFAULT 'running',
                costo_usd    REAL DEFAULT 0.0,
                creado_en    TEXT NOT NULL,
                terminado_en TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS desired_state (
                instance_id    TEXT NOT NULL,
                machine_id     TEXT NOT NULL,
                clave          TEXT NOT NULL,
                valor          TEXT,
                actualizado_en TEXT NOT NULL,
                PRIMARY KEY (instance_id, machine_id, clave)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tools (
                id               TEXT PRIMARY KEY,
                instance_id      TEXT NOT NULL,
                nombre           TEXT,
                dominio          TEXT,
                repo_url         TEXT,
                estado           TEXT,
                metrica_baseline REAL,
                metrica_actual   REAL,
                usos             INTEGER DEFAULT 0,
                fallos           INTEGER DEFAULT 0,
                creado_en        TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id          TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL,
                tipo        TEXT,
                payload     TEXT,
                estado      TEXT NOT NULL DEFAULT 'pendiente',
                creado_en   TEXT NOT NULL,
                resuelto_en TEXT
            )
        """)

        # facts — typed durable knowledge (dato | directiva). APPEND-ONLY:
        # a superseding fact is a NEW row; rows are never destructively rewritten.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id             TEXT PRIMARY KEY,
                instance_id    TEXT NOT NULL,
                tipo           TEXT NOT NULL,
                contenido      TEXT NOT NULL,
                pinned         INTEGER NOT NULL DEFAULT 0,
                tags           TEXT,
                procedencia    TEXT,
                confianza      REAL NOT NULL DEFAULT 1.0,
                creado_en      TEXT NOT NULL,
                actualizado_en TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS facts_by_instance
            ON facts(instance_id, tipo, pinned)
        """)

        await db.commit()


# ── episodes (append-only) ───────────────────────────────────────────────────────

async def append_episode(ep: Any) -> str:
    """
    Append an Episode to the grande. Serializes ``memoria_cargada`` into its
    column and stores a full JSON copy of every field in ``raw``. Returns the id.

    ``ep`` is duck-typed (any object exposing the Episode attributes) so this
    module never imports ``episode`` and stays dependency-light.
    """
    raw = json.dumps(
        {
            "id": ep.id,
            "instance_id": ep.instance_id,
            "despertar_por": ep.despertar_por,
            "memoria_cargada": list(ep.memoria_cargada or []),
            "opciones": list(getattr(ep, "opciones", []) or []),
            "decision": ep.decision,
            "accion": ep.accion,
            "resultado": ep.resultado,
            "dominio": getattr(ep, "dominio", None),
            "costo_usd": ep.costo_usd,
            "creado_en": ep.creado_en,
        },
        ensure_ascii=False,
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO episodes(
                   id, instance_id, creado_en, despertar_por, decision, accion,
                   resultado, dominio, costo_usd, memoria_cargada, raw)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ep.id,
                ep.instance_id,
                ep.creado_en,
                ep.despertar_por,
                ep.decision,
                ep.accion,
                ep.resultado,
                getattr(ep, "dominio", None),
                ep.costo_usd,
                _dumps(list(ep.memoria_cargada or [])),
                raw,
            ),
        )
        await db.commit()
    return ep.id


async def load_recent_episodes(instance_id: str, n: int = 20) -> list[dict]:
    """
    Return the ``n`` most recent episodes for ``instance_id``, newest first.
    ``memoria_cargada`` is deserialized back into a list.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM episodes
               WHERE instance_id = ?
               ORDER BY creado_en DESC, rowid DESC
               LIMIT ?""",
            (instance_id, n),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["memoria_cargada"] = _loads(row.get("memoria_cargada"), [])
    return rows


# ── facts (append-only; superseding = a new row) ─────────────────────────────────

async def insert_fact(row: dict) -> str:
    """
    Insert one fact row into the grande. ``row`` is a dict carrying every column
    (id, instance_id, tipo, contenido, pinned, tags, procedencia, confianza,
    creado_en, actualizado_en); ``tags`` may be a list (serialized here) or a
    pre-encoded JSON string. Append-only: this never overwrites an existing fact.
    Returns the fact id.
    """
    tags = row.get("tags")
    tags_json = tags if isinstance(tags, str) else _dumps(list(tags or []))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO facts(
                   id, instance_id, tipo, contenido, pinned, tags,
                   procedencia, confianza, creado_en, actualizado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["instance_id"],
                row["tipo"],
                row["contenido"],
                1 if row.get("pinned") else 0,
                tags_json,
                row.get("procedencia", ""),
                float(row.get("confianza", 1.0)),
                row["creado_en"],
                row["actualizado_en"],
            ),
        )
        await db.commit()
    return row["id"]


async def query_facts(
    instance_id: str,
    *,
    tipo: Optional[str] = None,
    pinned: Optional[bool] = None,
) -> list[dict]:
    """
    Return fact rows for ``instance_id`` (oldest first), optionally filtered by
    ``tipo`` and/or ``pinned``. ``tags`` is decoded back into a list. Always
    scoped to ``instance_id`` (isolation from day one).
    """
    sql = "SELECT * FROM facts WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if tipo is not None:
        sql += " AND tipo = ?"
        args.append(tipo)
    if pinned is not None:
        sql += " AND pinned = ?"
        args.append(1 if pinned else 0)
    sql += " ORDER BY creado_en, rowid"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["tags"] = _loads(row.get("tags"), [])
        row["pinned"] = bool(row["pinned"])
    return rows


async def query_facts_by_tags(instance_id: str, tags) -> list[dict]:
    """
    Return fact rows for ``instance_id`` whose tag set intersects ``tags``
    (oldest first). Tag matching is exact membership (NOT semantic). Scoped to
    ``instance_id``.
    """
    wanted = {t for t in (tags or [])}
    if not wanted:
        return []
    rows = await query_facts(instance_id)
    return [row for row in rows if wanted.intersection(row.get("tags") or [])]


# ── tasks ─────────────────────────────────────────────────────────────────────

async def add_task(
    instance_id: str,
    accion: str,
    objetivo: str = "",
    params: Optional[dict] = None,
    *,
    origen_trust: int = 1,
    origen_actor: Optional[str] = None,
    estado: str = "pendiente",
    firma: str = "",
    task_id: Optional[str] = None,
) -> str:
    """Insert a task. Returns its id (generated if not supplied)."""
    task_id = task_id or _new_id("t")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tasks(
                   id, instance_id, accion, objetivo, params,
                   origen_trust, origen_actor, estado, firma, creado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, instance_id, accion, objetivo, _dumps(params or {}),
                origen_trust, origen_actor, estado, firma, _now(),
            ),
        )
        await db.commit()
    return task_id


async def load_pending_tasks(instance_id: str) -> list[dict]:
    """Return pending tasks for ``instance_id`` (oldest first), params decoded."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM tasks
               WHERE instance_id = ? AND estado = 'pendiente'
               ORDER BY creado_en""",
            (instance_id,),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["params"] = _loads(row.get("params"), {})
    return rows


async def set_task_state(instance_id: str, task_id: str, estado: str) -> None:
    """Update a task's state, scoped to ``instance_id``."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET estado = ? WHERE instance_id = ? AND id = ?",
            (estado, instance_id, task_id),
        )
        await db.commit()


# ── jobs ──────────────────────────────────────────────────────────────────────

async def record_job(
    instance_id: str,
    *,
    pid: Optional[int] = None,
    harness: str = "",
    estado: str = "running",
    costo_usd: float = 0.0,
    job_id: Optional[str] = None,
) -> str:
    """Record a launched job (long-running harness process). Returns its id."""
    job_id = job_id or _new_id("j")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO jobs(
                   id, instance_id, pid, harness, estado, costo_usd, creado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (job_id, instance_id, pid, harness, estado, costo_usd, _now()),
        )
        await db.commit()
    return job_id


async def update_job(
    instance_id: str,
    job_id: str,
    *,
    estado: Optional[str] = None,
    costo_usd: Optional[float] = None,
    terminado_en: Optional[str] = None,
) -> None:
    """Update a job's mutable bookkeeping fields, scoped to ``instance_id``."""
    sets: list[str] = []
    args: list[Any] = []
    if estado is not None:
        sets.append("estado = ?")
        args.append(estado)
    if costo_usd is not None:
        sets.append("costo_usd = ?")
        args.append(costo_usd)
    if terminado_en is not None:
        sets.append("terminado_en = ?")
        args.append(terminado_en)
    if not sets:
        return
    args.extend([instance_id, job_id])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE instance_id = ? AND id = ?",
            args,
        )
        await db.commit()


# ── desired_state ─────────────────────────────────────────────────────────────

async def upsert_desired_state(
    instance_id: str, machine_id: str, clave: str, valor: str
) -> None:
    """Set the reconciliation target for one (machine, key), scoped to instance."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO desired_state(
                   instance_id, machine_id, clave, valor, actualizado_en)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(instance_id, machine_id, clave)
               DO UPDATE SET valor = excluded.valor,
                             actualizado_en = excluded.actualizado_en""",
            (instance_id, machine_id, clave, valor, _now()),
        )
        await db.commit()


async def get_desired_state(instance_id: str, machine_id: str) -> dict[str, str]:
    """Return the desired ``{clave: valor}`` map for one machine in one instance."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT clave, valor FROM desired_state
               WHERE instance_id = ? AND machine_id = ?
               ORDER BY clave""",
            (instance_id, machine_id),
        ) as cur:
            return {row["clave"]: row["valor"] for row in await cur.fetchall()}


# ── approvals ─────────────────────────────────────────────────────────────────

async def add_approval(
    instance_id: str,
    tipo: str,
    payload: Optional[dict] = None,
    *,
    estado: str = "pendiente",
    approval_id: Optional[str] = None,
) -> str:
    """Enqueue a human-in-the-loop approval. Returns its id."""
    approval_id = approval_id or _new_id("ap")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO approvals(
                   id, instance_id, tipo, payload, estado, creado_en)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (approval_id, instance_id, tipo, _dumps(payload or {}), estado, _now()),
        )
        await db.commit()
    return approval_id


async def load_pending_approvals(instance_id: str) -> list[dict]:
    """Return pending approvals for ``instance_id`` (oldest first), payload decoded."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM approvals
               WHERE instance_id = ? AND estado = 'pendiente'
               ORDER BY creado_en""",
            (instance_id,),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["payload"] = _loads(row.get("payload"), {})
    return rows


async def resolve_approval(instance_id: str, approval_id: str, estado: str) -> None:
    """Resolve an approval (e.g. 'aprobada' / 'rechazada'), scoped to instance."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE approvals
               SET estado = ?, resuelto_en = ?
               WHERE instance_id = ? AND id = ?""",
            (estado, _now(), instance_id, approval_id),
        )
        await db.commit()
