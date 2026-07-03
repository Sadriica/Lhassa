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

from lhassa._vendor import aiosqlite

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

async def _has_column(conn, table: str, column: str) -> bool:
    """Return True if ``column`` exists in ``table`` (PRAGMA table_info check)."""
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        return any(row[1] == column for row in await cur.fetchall())


async def init_db() -> None:
    """
    Enable WAL and create every table. Idempotent — safe to call repeatedly.

    Migrations for existing production DBs are handled with idempotent
    ALTER TABLE statements guarded by PRAGMA table_info checks.
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

        # autonomy — the friction knob (§2.6, principle #9). One row per
        # (instance_id, accion, actor_id) holding the count of CONSECUTIVE
        # human-origin approvals of that action by that actor. The knob only
        # LOWERS friction for already-human-approved actions; it never changes an
        # Origin's trust level (no contagion-promotion). A denial resets the streak.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS autonomy (
                instance_id    TEXT NOT NULL,
                accion         TEXT NOT NULL,
                actor_id       TEXT NOT NULL,
                streak         INTEGER NOT NULL DEFAULT 0,
                actualizado_en TEXT NOT NULL,
                PRIMARY KEY (instance_id, accion, actor_id)
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

        # postulaciones — the tracking_postulaciones typed task (§2.5): tracked
        # job/internship applications (CVs sent). A small mutable workbook scoped
        # to instance_id; ``estado`` advances as an application progresses. An
        # external source (email/sheet via an MCP tool) MAY top it up, but the
        # core stays deterministic and offline.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS postulaciones (
                id             TEXT PRIMARY KEY,
                instance_id    TEXT NOT NULL,
                empresa        TEXT,
                rol            TEXT,
                estado         TEXT NOT NULL DEFAULT 'pendiente',
                fuente         TEXT,
                link           TEXT,
                notas          TEXT,
                creado_en      TEXT NOT NULL,
                actualizado_en TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS postulaciones_by_instance
            ON postulaciones(instance_id, estado)
        """)

        # fact_embeddings — memory layer 4 (semantic retrieval, §3-Fase 6). One
        # row per (instance_id, fact_id) holding the fact's embedding as a
        # JSON-encoded list of floats plus the embedder ``model`` tag. ADDITIVE:
        # the semantic layer is OFF by default, so this table stays empty unless
        # an index step (``memory.load.index_facts``) is run. Scoped to instance.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS fact_embeddings (
                fact_id     TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                dim         INTEGER NOT NULL,
                vector      TEXT NOT NULL,
                model       TEXT,
                creado_en   TEXT NOT NULL,
                PRIMARY KEY (instance_id, fact_id)
            )
        """)

        # org_messages — the typed task queue between brains (§2.coordinación,
        # Fase 7). Inter-brain communication is TYPED messages, never free prose:
        # a closed set of kinds (TASK_ASSIGN / TASK_RESULT / STATUS_REQUEST /
        # STATUS_REPORT) correlated by ``corr_id`` so a result maps back to its
        # assignment. Scoped to ``instance_id`` (the org/tenant): the two brains
        # (general + department) share the tenant and are addressed by their
        # logical ``from_instance`` / ``to_instance`` identity strings.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS org_messages (
                id            TEXT PRIMARY KEY,
                instance_id   TEXT NOT NULL,
                kind          TEXT NOT NULL,
                from_instance TEXT,
                to_instance   TEXT,
                payload       TEXT,
                status        TEXT NOT NULL DEFAULT 'pending',
                corr_id       TEXT,
                creado_en     TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS org_messages_inbox
            ON org_messages(instance_id, to_instance, status)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS org_messages_by_corr
            ON org_messages(instance_id, corr_id)
        """)

        # org_commons — the ONE cross-cutting read-only source of truth (Fase 7):
        # policies, org chart, shared data. Readable by ALL brains; written ONLY
        # by the curator (the general — enforced in lhassa.org.commons / rbac).
        # A single shared row per (instance_id, clave) — no per-department copies
        # that could diverge. ``curator`` records who last wrote it (audit).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS org_commons (
                instance_id    TEXT NOT NULL,
                clave          TEXT NOT NULL,
                valor          TEXT,
                curator        TEXT,
                actualizado_en TEXT NOT NULL,
                PRIMARY KEY (instance_id, clave)
            )
        """)

        # ── Phase 4 migrations: add columns to existing tables ───────────────
        # tasks: due_at (ISO timestamp when the task becomes ready)
        if not await _has_column(db, "tasks", "due_at"):
            await db.execute("ALTER TABLE tasks ADD COLUMN due_at TEXT")
        # tasks: recurrence (seconds as a string; None = one-shot)
        if not await _has_column(db, "tasks", "recurrence"):
            await db.execute("ALTER TABLE tasks ADD COLUMN recurrence TEXT")
        # jobs: acked (1 once the watchlist has fired job_review for this job)
        if not await _has_column(db, "jobs", "acked"):
            await db.execute("ALTER TABLE jobs ADD COLUMN acked INTEGER DEFAULT 0")

        # ── Phase 5 migration: fact_embeddings.model (embedder stamp) ────────
        # The column is in the CREATE TABLE above (new DBs have it), but a DB
        # created before the stamp existed lacks it — add it idempotently so
        # semantic queries can filter rows by the embedder that produced them.
        if not await _has_column(db, "fact_embeddings", "model"):
            await db.execute("ALTER TABLE fact_embeddings ADD COLUMN model TEXT")

        # watch_state — generic key/value store for watchlist dedup hashes.
        # Each sense writes its last-seen state here so a persistent condition
        # fires ONCE per transition rather than on every 2-minute beat.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS watch_state (
                instance_id TEXT NOT NULL,
                key         TEXT NOT NULL,
                value       TEXT,
                updated_en  TEXT NOT NULL,
                PRIMARY KEY (instance_id, key)
            )
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


async def upsert_fact_embedding(
    instance_id: str,
    fact_id: str,
    vector: list,
    *,
    model: str = "",
) -> None:
    """Store (or replace) one fact's embedding, scoped to ``instance_id``.

    ``vector`` is a list of floats, persisted as JSON. One row per
    (instance_id, fact_id); re-indexing a fact overwrites its vector.
    """
    vec = [float(x) for x in (vector or [])]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO fact_embeddings(
                   fact_id, instance_id, dim, vector, model, creado_en)
               VALUES(?, ?, ?, ?, ?, ?)
               ON CONFLICT(instance_id, fact_id)
               DO UPDATE SET dim = excluded.dim,
                             vector = excluded.vector,
                             model = excluded.model,
                             creado_en = excluded.creado_en""",
            (fact_id, instance_id, len(vec), _dumps(vec), model, _now()),
        )
        await db.commit()


async def get_fact_embeddings(instance_id: str) -> list[dict]:
    """Return stored fact embeddings for ``instance_id``.

    Each dict has ``fact_id``, the decoded ``vector`` (list of floats) and the
    ``model`` tag. Always scoped to ``instance_id`` (isolation from day one).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT fact_id, vector, model FROM fact_embeddings WHERE instance_id = ?",
            (instance_id,),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "fact_id": row["fact_id"],
                "vector": _loads(row.get("vector"), []),
                "model": row.get("model") or "",
            }
        )
    return out


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
    due_at: Optional[str] = None,
    recurrence: Optional[str] = None,
) -> str:
    """Insert a task. Returns its id (generated if not supplied).

    ``due_at`` is an ISO 8601 UTC timestamp; a task with a future ``due_at``
    is NOT returned by :func:`load_pending_tasks` until its time arrives.
    ``recurrence`` is an interval in SECONDS (stored as a string); the brain
    mints the next occurrence when it marks this task "hecha".
    """
    task_id = task_id or _new_id("t")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tasks(
                   id, instance_id, accion, objetivo, params,
                   origen_trust, origen_actor, estado, firma, creado_en,
                   due_at, recurrence)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, instance_id, accion, objetivo, _dumps(params or {}),
                origen_trust, origen_actor, estado, firma, _now(),
                due_at, recurrence,
            ),
        )
        await db.commit()
    return task_id


async def load_pending_tasks(instance_id: str) -> list[dict]:
    """Return pending tasks for ``instance_id`` (oldest first), params decoded.

    Due-aware: a task with a ``due_at`` timestamp in the future is NOT returned
    until its scheduled time arrives. One-shot tasks (``due_at IS NULL``) are
    always returned when pendiente.
    """
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM tasks
               WHERE instance_id = ? AND estado = 'pendiente'
                 AND (due_at IS NULL OR due_at <= ?)
               ORDER BY creado_en""",
            (instance_id, now),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["params"] = _loads(row.get("params"), {})
    return rows


async def list_tasks(
    instance_id: str,
    *,
    estado: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """Return tasks for ``instance_id`` (newest first, cap ``limit``).

    Optionally filtered by ``estado``. Params decoded. Always scoped to
    ``instance_id``.
    """
    sql = "SELECT * FROM tasks WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if estado is not None:
        sql += " AND estado = ?"
        args.append(estado)
    sql += " ORDER BY creado_en DESC, rowid DESC LIMIT ?"
    args.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
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


async def get_approval(instance_id: str, approval_id: str) -> Optional[dict]:
    """Return a single approval row (payload decoded), or ``None`` if absent.

    Scoped to ``instance_id`` (isolation from day one). Used when resolving an
    approval so the autonomy knob can be fed the action/actor it was parked for.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM approvals WHERE instance_id = ? AND id = ?",
            (instance_id, approval_id),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["payload"] = _loads(out.get("payload"), {})
    return out


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


# ── autonomy (the friction knob — consecutive human approvals) ──────────────────

async def get_autonomy_streak(instance_id: str, accion: str, actor_id: str) -> int:
    """Return the consecutive-human-approval streak for one (accion, actor_id).

    Scoped to ``instance_id``. Missing row → ``0`` (no autonomy earned yet).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT streak FROM autonomy
               WHERE instance_id = ? AND accion = ? AND actor_id = ?""",
            (instance_id, accion, actor_id),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def bump_autonomy_streak(instance_id: str, accion: str, actor_id: str) -> int:
    """Increment (or create) the streak for one (accion, actor_id). Returns the new value.

    Upsert: a HUMANO-origin approval advances the count by one. This only ever
    *records* how often a human said yes; it never touches trust levels.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO autonomy(instance_id, accion, actor_id, streak, actualizado_en)
               VALUES(?, ?, ?, 1, ?)
               ON CONFLICT(instance_id, accion, actor_id)
               DO UPDATE SET streak = streak + 1, actualizado_en = excluded.actualizado_en""",
            (instance_id, accion, actor_id, _now()),
        )
        await db.commit()
    return await get_autonomy_streak(instance_id, accion, actor_id)


async def reset_autonomy_streak(instance_id: str, accion: str, actor_id: str) -> None:
    """Reset the streak for one (accion, actor_id) to zero (a denial resets it)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO autonomy(instance_id, accion, actor_id, streak, actualizado_en)
               VALUES(?, ?, ?, 0, ?)
               ON CONFLICT(instance_id, accion, actor_id)
               DO UPDATE SET streak = 0, actualizado_en = excluded.actualizado_en""",
            (instance_id, accion, actor_id, _now()),
        )
        await db.commit()


# ── postulaciones (tracked applications — tracking_postulaciones task) ───────────

async def add_postulacion(
    instance_id: str,
    empresa: str,
    rol: str = "",
    *,
    estado: str = "pendiente",
    fuente: str = "",
    link: str = "",
    notas: str = "",
    postulacion_id: Optional[str] = None,
) -> str:
    """Insert one tracked application. Returns its id (generated if not supplied).

    Scoped to ``instance_id`` (isolation from day one).
    """
    postulacion_id = postulacion_id or _new_id("post")
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO postulaciones(
                   id, instance_id, empresa, rol, estado, fuente, link, notas,
                   creado_en, actualizado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                postulacion_id, instance_id, empresa, rol, estado, fuente, link,
                notas, now, now,
            ),
        )
        await db.commit()
    return postulacion_id


async def update_postulacion_estado(
    instance_id: str,
    postulacion_id: str,
    estado: str,
    *,
    notas: Optional[str] = None,
) -> bool:
    """Advance one application's ``estado`` (and optionally append/replace notas).

    Returns True if a row was updated, False if no such id under ``instance_id``.
    Scoped to ``instance_id``.
    """
    sets = ["estado = ?", "actualizado_en = ?"]
    args: list[Any] = [estado, _now()]
    if notas is not None:
        sets.append("notas = ?")
        args.append(notas)
    args.extend([instance_id, postulacion_id])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE postulaciones SET {', '.join(sets)} "
            "WHERE instance_id = ? AND id = ?",
            args,
        )
        await db.commit()
        return cur.rowcount > 0


async def list_postulaciones(
    instance_id: str,
    *,
    estado: Optional[str] = None,
) -> list[dict]:
    """Return tracked applications for ``instance_id`` (newest first).

    Optionally filtered by ``estado``. Always scoped to ``instance_id``.
    """
    sql = "SELECT * FROM postulaciones WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if estado is not None:
        sql += " AND estado = ?"
        args.append(estado)
    sql += " ORDER BY creado_en DESC, rowid DESC"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── tools (self-written tools + their gate metrics — §2.8) ──────────────────────
#
# A ``tools`` row is APPEND-as-source-of-truth: one row per (instance_id, nombre);
# the only in-place mutations allowed are the gate ``estado`` and the metric/usage
# counters (metrica_baseline/actual, usos, fallos). The lifecycle of ``estado`` is
# the four-gate pipeline of self-improvement 2b:
#   cuarentena → (gates) → graduada | rechazada → (gate 4 Retiro) → cuarentena
# Every helper is scoped to ``instance_id`` (isolation from day one).


async def get_tool(instance_id: str, nombre: str) -> Optional[dict]:
    """Return the tool row for one (instance_id, nombre), or ``None`` if absent."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM tools
               WHERE instance_id = ? AND nombre = ?
               ORDER BY creado_en DESC, rowid DESC
               LIMIT 1""",
            (instance_id, nombre),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row is not None else None


async def upsert_tool(
    instance_id: str,
    nombre: str,
    *,
    dominio: Optional[str] = None,
    repo_url: Optional[str] = None,
    estado: Optional[str] = None,
    metrica_baseline: Optional[float] = None,
    metrica_actual: Optional[float] = None,
) -> str:
    """Create or update the tool row for one (instance_id, nombre). Returns its id.

    Only the fields explicitly passed (non-``None``) are written on an update, so a
    caller can advance ``estado`` without clobbering the recorded metrics. The
    usage counters (usos/fallos) are owned by :func:`bump_tool_usage` and are never
    touched here.
    """
    existing = await get_tool(instance_id, nombre)
    async with aiosqlite.connect(DB_PATH) as db:
        if existing is None:
            tool_id = _new_id("tool")
            await db.execute(
                """INSERT INTO tools(
                       id, instance_id, nombre, dominio, repo_url, estado,
                       metrica_baseline, metrica_actual, usos, fallos, creado_en)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)""",
                (
                    tool_id, instance_id, nombre, dominio, repo_url, estado,
                    metrica_baseline, metrica_actual, _now(),
                ),
            )
            await db.commit()
            return tool_id
        sets: list[str] = []
        args: list[Any] = []
        for col, val in (
            ("dominio", dominio),
            ("repo_url", repo_url),
            ("estado", estado),
            ("metrica_baseline", metrica_baseline),
            ("metrica_actual", metrica_actual),
        ):
            if val is not None:
                sets.append(f"{col} = ?")
                args.append(val)
        if sets:
            args.extend([instance_id, nombre])
            await db.execute(
                f"UPDATE tools SET {', '.join(sets)} "
                "WHERE instance_id = ? AND nombre = ?",
                args,
            )
            await db.commit()
        return existing["id"]


async def list_tools(
    instance_id: str,
    *,
    estado: Optional[str] = None,
) -> list[dict]:
    """Return tool rows for ``instance_id`` (newest first), optionally by ``estado``."""
    sql = "SELECT * FROM tools WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if estado is not None:
        sql += " AND estado = ?"
        args.append(estado)
    sql += " ORDER BY creado_en DESC, rowid DESC"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def bump_tool_usage(instance_id: str, nombre: str, *, ok: bool) -> None:
    """Record one production use of a tool: +1 ``usos`` and +1 ``fallos`` if not ``ok``.

    This is the signal gate 4 (Retiro) reads: a graduated tool whose production
    failure-rate crosses a threshold (or that goes unused) is demoted back to
    cuarentena. Scoped to ``instance_id``; a no-op if the tool does not exist.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE tools
               SET usos = usos + 1,
                   fallos = fallos + ?
               WHERE instance_id = ? AND nombre = ?""",
            (0 if ok else 1, instance_id, nombre),
        )
        await db.commit()


# ── org_messages (the typed task queue between brains — Fase 7) ──────────────────
#
# Inter-brain messages are TYPED, never free prose: the ``kind`` is one of a closed
# set (validated by lhassa.org.messaging before it reaches here). A ``corr_id``
# threads a TASK_RESULT back to its TASK_ASSIGN. Every helper is scoped to
# ``instance_id`` (the org/tenant); the brains are addressed by their logical
# ``from_instance`` / ``to_instance`` identity strings.


async def enqueue_org_message(
    instance_id: str,
    *,
    kind: str,
    from_instance: str,
    to_instance: str,
    payload: Optional[dict] = None,
    corr_id: str = "",
    status: str = "pending",
    msg_id: Optional[str] = None,
    creado_en: Optional[str] = None,
) -> str:
    """Persist one typed inter-brain message. Returns its id (generated if absent).

    ``payload`` is JSON-encoded. Scoped to ``instance_id`` (the org/tenant).
    """
    msg_id = msg_id or _new_id("msg")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO org_messages(
                   id, instance_id, kind, from_instance, to_instance,
                   payload, status, corr_id, creado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id, instance_id, kind, from_instance, to_instance,
                _dumps(payload or {}), status, corr_id, creado_en or _now(),
            ),
        )
        await db.commit()
    return msg_id


def _decode_org_message(row: dict) -> dict:
    """Decode the JSON ``payload`` column of an org_messages row in place."""
    row["payload"] = _loads(row.get("payload"), {})
    return row


async def get_org_inbox(
    instance_id: str,
    to_instance: str,
    *,
    status: Optional[str] = None,
) -> list[dict]:
    """Return messages addressed to ``to_instance`` (oldest first), payload decoded.

    Optionally filtered by ``status``. Always scoped to ``instance_id``.
    """
    sql = "SELECT * FROM org_messages WHERE instance_id = ? AND to_instance = ?"
    args: list[Any] = [instance_id, to_instance]
    if status is not None:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY creado_en, rowid"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    return [_decode_org_message(r) for r in rows]


async def get_org_messages_by_corr(
    instance_id: str,
    corr_id: str,
    *,
    kind: Optional[str] = None,
) -> list[dict]:
    """Return messages sharing a ``corr_id`` (oldest first), payload decoded.

    Optionally filtered by ``kind`` (e.g. only TASK_RESULT). Scoped to instance.
    """
    sql = "SELECT * FROM org_messages WHERE instance_id = ? AND corr_id = ?"
    args: list[Any] = [instance_id, corr_id]
    if kind is not None:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY creado_en, rowid"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    return [_decode_org_message(r) for r in rows]


async def get_org_message(instance_id: str, msg_id: str) -> Optional[dict]:
    """Return a single message row (payload decoded), or ``None`` if absent."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM org_messages WHERE instance_id = ? AND id = ?",
            (instance_id, msg_id),
        ) as cur:
            row = await cur.fetchone()
    return _decode_org_message(dict(row)) if row is not None else None


async def set_org_message_status(instance_id: str, msg_id: str, status: str) -> None:
    """Advance a message's ``status`` (pending → delivered → done). Instance-scoped."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE org_messages SET status = ? WHERE instance_id = ? AND id = ?",
            (status, instance_id, msg_id),
        )
        await db.commit()


# ── org_commons (the read-only cross-cutting source of truth — Fase 7) ───────────
#
# ONE shared row per (instance_id, clave). Reads are open to all brains; the WRITE
# gate (curator-only) lives in lhassa.org.commons + lhassa.org.rbac, not here — db
# stays a dumb store. Values are JSON-encoded to preserve dict/list/str/number.


async def set_commons(
    instance_id: str,
    clave: str,
    valor: Any,
    *,
    curator: str,
) -> None:
    """Upsert one commons entry (single shared row per key). Records ``curator``.

    JSON-encodes ``valor`` to preserve its type. Scoped to ``instance_id``.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO org_commons(instance_id, clave, valor, curator, actualizado_en)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(instance_id, clave)
               DO UPDATE SET valor = excluded.valor,
                             curator = excluded.curator,
                             actualizado_en = excluded.actualizado_en""",
            (instance_id, clave, json.dumps(valor, ensure_ascii=False), curator, _now()),
        )
        await db.commit()


async def get_commons(instance_id: str, clave: str) -> Optional[dict]:
    """Return one commons row (value JSON-decoded) or ``None`` if absent.

    The returned dict carries ``clave``, ``valor`` (decoded), ``curator`` and
    ``actualizado_en``. Scoped to ``instance_id``.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM org_commons WHERE instance_id = ? AND clave = ?",
            (instance_id, clave),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["valor"] = _loads(out.get("valor"), None)
    return out


async def get_all_commons(instance_id: str) -> dict[str, Any]:
    """Return the whole commons as ``{clave: valor}`` (values decoded), instance-scoped."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT clave, valor FROM org_commons WHERE instance_id = ? ORDER BY clave",
            (instance_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {row["clave"]: _loads(row["valor"], None) for row in rows}


# ── watch_state (watchlist dedup key/value store — Phase 4) ──────────────────────
#
# A generic, lightweight key/value store for watchlist transition dedup. Each
# sense writes its last-seen state here so a persistent condition fires ONCE per
# transition rather than every 2-minute heartbeat beat. Keys are per-sense
# identifiers (e.g. ``repo:<path>``, ``health:level``); values are opaque strings.


async def get_watch_state(instance_id: str, key: str) -> Optional[str]:
    """Return the stored value for ``key``, or ``None`` if not yet set.

    Scoped to ``instance_id`` so different brain instances don't cross-talk.
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT value FROM watch_state WHERE instance_id = ? AND key = ?",
            (instance_id, key),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row is not None else None


async def set_watch_state(instance_id: str, key: str, value: str) -> None:
    """Upsert the state for ``key`` to ``value``, scoped to ``instance_id``."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO watch_state(instance_id, key, value, updated_en)
               VALUES(?, ?, ?, ?)
               ON CONFLICT(instance_id, key)
               DO UPDATE SET value = excluded.value,
                             updated_en = excluded.updated_en""",
            (instance_id, key, value, _now()),
        )
        await conn.commit()


# ── jobs: ack surface (Phase 4 watchlist job_review sense) ───────────────────────
#
# ``acked`` prevents the ``_from_finished_jobs`` watchlist sense from re-firing for
# the same finished job on every subsequent tick. The sense marks ``acked=1`` as
# it fires, so each job_review Trigger is emitted exactly once.


async def load_unacked_finished_jobs(instance_id: str) -> list[dict]:
    """Return finished (done/error) jobs that have not yet been acked.

    A job is "finished" when its ``estado`` is either ``done`` or starts with
    ``error`` (runner.py uses ``"error: harness unavailable"`` and plain
    ``"error"``). Sorted oldest-first. Scoped to ``instance_id``.
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM jobs
               WHERE instance_id = ?
                 AND acked = 0
                 AND (estado = 'done' OR estado LIKE 'error%')
               ORDER BY creado_en""",
            (instance_id,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def ack_job(instance_id: str, job_id: str) -> None:
    """Mark a job as acked (job_review Trigger already fired). Scoped to instance."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE jobs SET acked = 1 WHERE instance_id = ? AND id = ?",
            (instance_id, job_id),
        )
        await conn.commit()
