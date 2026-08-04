# SPDX-License-Identifier: AGPL-3.0-or-later
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

This module owns the connection primitives shared by every other submodule of
the ``lhassa.core.db`` facade package: ``DB_PATH``, the small serialization
helpers, the migration registry and ``init_db``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from lhassa._vendor import aiosqlite

from lhassa.config import Config
from lhassa.core.migrations import (
    Migration,
    MigrationValidationError,
    apply_migrations,
    ensure_supported,
    get_schema_version,
)


def _pkg():
    """Return the live ``lhassa.core.db`` package module.

    Every other submodule routes its reads of ``DB_PATH`` and calls to
    ``_now``/``_dumps``/``_loads``/``_new_id`` through this lookup (instead of
    importing them directly) so that ``monkeypatch.setattr(db, "DB_PATH", ...)``
    — or patching any of those helpers — on the *package* (``from lhassa.core
    import db``) is honored no matter which submodule actually runs the code.
    Mirrors the pattern used by ``lhassa/notify/commands/_ai.py``.
    """
    from lhassa.core import db as _db_pkg

    return _db_pkg

DB_PATH = Path(Config.brain_db_path)

# Validated-schema cache: a DB path is migrated + schema-validated ONCE per
# process. Re-validation is the expensive part of init_db (every migration
# validator runs a PRAGMA table_info — ~23+ checks across the whole registry)
# and is a pure no-op when the DB is already at a version we brought to the
# current schema. Keyed by resolved path AND the schema version we validated,
# so a replaced/recreated file (fresh user_version) still migrates.
_MIGRATED_VERSIONS: dict[str, int] = {}


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


async def _migration_001_baseline(db) -> None:
    """Reconcile an empty or unversioned legacy database to the baseline."""
    await db.execute("SAVEPOINT migration_001_baseline")
    try:
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

        # episodes_fts — Fase 1 "Recall": a full-text index over the episode log
        # (§ decisions log Fase 1). An FTS5 EXTERNAL-CONTENT table over `episodes`
        # (no row duplication — content lives only in `episodes`), kept in sync by
        # triggers. Defensive: some SQLite builds omit FTS5, so this — and ONLY
        # this — is wrapped so init_db() never crashes on an old/stripped build.
        await db.execute("SAVEPOINT migration_001_fts")
        try:
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                    decision, accion, resultado, despertar_por,
                    content='episodes', content_rowid='rowid'
                )
            """)
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS episodes_fts_ai AFTER INSERT ON episodes BEGIN
                    INSERT INTO episodes_fts(rowid, decision, accion, resultado, despertar_por)
                    VALUES (new.rowid, new.decision, new.accion, new.resultado, new.despertar_por);
                END
            """)
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS episodes_fts_ad AFTER DELETE ON episodes BEGIN
                    INSERT INTO episodes_fts(episodes_fts, rowid, decision, accion, resultado, despertar_por)
                    VALUES ('delete', old.rowid, old.decision, old.accion, old.resultado, old.despertar_por);
                END
            """)
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS episodes_fts_au AFTER UPDATE ON episodes BEGIN
                    INSERT INTO episodes_fts(episodes_fts, rowid, decision, accion, resultado, despertar_por)
                    VALUES ('delete', old.rowid, old.decision, old.accion, old.resultado, old.despertar_por);
                    INSERT INTO episodes_fts(rowid, decision, accion, resultado, despertar_por)
                    VALUES (new.rowid, new.decision, new.accion, new.resultado, new.despertar_por);
                END
            """)

            # Backfill: episodes existed before the index (or from a version
            # without triggers) — if the index is empty but episodes aren't,
            # rebuild it from the content table in one shot. NOTE: `SELECT
            # count(*) FROM episodes_fts` is NOT a reliable emptiness check for
            # an external-content table — SQLite answers a plain count(*) off
            # the content table itself, not the index. The `_docsize` shadow
            # table (one row per row actually INDEXED) is the true signal.
            async with db.execute("SELECT COUNT(*) FROM episodes_fts_docsize") as cur:
                fts_count = (await cur.fetchone())[0]
            if fts_count == 0:
                async with db.execute("SELECT COUNT(*) FROM episodes") as cur:
                    ep_count = (await cur.fetchone())[0]
                if ep_count > 0:
                    await db.execute(
                        "INSERT INTO episodes_fts(episodes_fts) VALUES ('rebuild')"
                    )
        except Exception as exc:
            await db.execute("ROLLBACK TO SAVEPOINT migration_001_fts")
            await db.execute("RELEASE SAVEPOINT migration_001_fts")
            logger.warning(f"db: episodes_fts (FTS5) unavailable, recall search disabled: {exc}")
        else:
            await db.execute("RELEASE SAVEPOINT migration_001_fts")

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

        await db.execute("""
            CREATE TABLE IF NOT EXISTS owner_prompts (
                id           TEXT PRIMARY KEY,
                instance_id  TEXT NOT NULL,
                kind         TEXT NOT NULL,
                slot         TEXT,
                prompt       TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                answered_at  TEXT,
                answer       TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS owner_prompts_pending
            ON owner_prompts(instance_id, kind, status, expires_at)
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

        # tasks: updated_en — last time estado changed (drives the deferred-retry
        # backoff sweep: a task sits "diferida" for at least the backoff window
        # before it is retried). Defaults to creado_en for pre-existing rows via
        # the UPDATE below so the very first sweep after upgrade doesn't treat
        # every old deferred task as instantly due.
        if not await _has_column(db, "tasks", "updated_en"):
            await db.execute("ALTER TABLE tasks ADD COLUMN updated_en TEXT")
            await db.execute(
                "UPDATE tasks SET updated_en = creado_en WHERE updated_en IS NULL"
            )

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

        # runs / run_steps — durable execution history for analytics. Episodes stay
        # the narrative audit log; these rows answer operational questions like
        # "how many times did this flow/brain run, how long did it take, did it fail".
        await db.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id             TEXT PRIMARY KEY,
                instance_id    TEXT NOT NULL,
                kind           TEXT NOT NULL,
                entity_id      TEXT,
                parent_run_id  TEXT,
                status         TEXT NOT NULL,
                trigger        TEXT,
                summary        TEXT,
                started_at     TEXT NOT NULL,
                finished_at    TEXT,
                duration_ms    INTEGER DEFAULT 0,
                cost_usd       REAL DEFAULT 0.0,
                input_tokens   INTEGER DEFAULT 0,
                output_tokens  INTEGER DEFAULT 0,
                raw            TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS runs_by_instance_kind_entity
            ON runs(instance_id, kind, entity_id, started_at)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS runs_by_instance_started
            ON runs(instance_id, started_at)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS run_steps (
                id             TEXT PRIMARY KEY,
                instance_id    TEXT NOT NULL,
                run_id         TEXT NOT NULL,
                idx            INTEGER NOT NULL,
                kind           TEXT NOT NULL,
                entity_id      TEXT,
                status         TEXT NOT NULL,
                started_at     TEXT,
                finished_at    TEXT,
                duration_ms    INTEGER DEFAULT 0,
                cost_usd       REAL DEFAULT 0.0,
                input_summary  TEXT,
                output_summary TEXT,
                error          TEXT,
                raw            TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS run_steps_by_instance_run
            ON run_steps(instance_id, run_id, idx)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS run_steps_by_instance_kind_entity
            ON run_steps(instance_id, kind, entity_id)
        """)

        # artifacts — durable, append-only record of a delivered WORK PRODUCT (a CV
        # .tex + .pdf, a report, …). "Lhassa is the sender of record" (§org, the
        # hierarchy is the philosophy): whatever a leaf produces is registered here
        # BEFORE it is handed up the chain, so the audit trail always has something
        # concrete to point at (``episode_id`` links back to the hop that delivered
        # it). Append-only: a re-delivery is a NEW row, never an update.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id                 TEXT PRIMARY KEY,
                instance_id        TEXT NOT NULL,
                kind               TEXT NOT NULL,
                paths              TEXT,
                metadata           TEXT,
                origin_department  TEXT,
                episode_id         TEXT,
                creado_en          TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS artifacts_by_instance
            ON artifacts(instance_id, creado_en)
        """)

        # answer_cache — the "$0 intelligence" answer cache (§ notify.intent /
        # memory.answer_cache): one row per PAST (question, answer) turn the chat
        # actually thought through, so a semantically-similar future question can be
        # served again for FREE instead of spending another model call. Scoped to
        # ``instance_id``; ``embedding`` is the question's vector (JSON-encoded floats,
        # same shape as ``fact_embeddings.vector``) so a lookup is a cosine scan, no
        # extra index needed at this scale. ``hits`` counts how many times a cached
        # row has served a lookup above threshold — an observability/tuning signal,
        # not a cache-invalidation mechanism (TTL, applied at read time in
        # memory/answer_cache.py, is what ages a row out).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS answer_cache (
                id          TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL,
                question    TEXT NOT NULL,
                answer      TEXT NOT NULL,
                embedding   TEXT NOT NULL,
                tags        TEXT,
                hits        INTEGER NOT NULL DEFAULT 0,
                creado_en   TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS answer_cache_by_instance
            ON answer_cache(instance_id, creado_en)
        """)

        # knowledge — document perception (§ memory.knowledge): chunks of the
        # owner's OWN documents from opt-in ``config.knowledge_roots``, embedded
        # offline so the brain's thinking/recall/graph can be grounded in real
        # material at $0. One row per chunk; ``source_path`` + ``mtime`` make
        # ingestion incremental (a file whose mtime is unchanged is skipped; a
        # changed file has its old chunks deleted and re-indexed). Scoped to
        # ``instance_id`` like everything else in the grande. This table is
        # local-only (``~/.lhassa/brain.db``), never the repo — see
        # doc/boundaries.md.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id           TEXT PRIMARY KEY,
                instance_id  TEXT NOT NULL,
                source_path  TEXT NOT NULL,
                chunk_index  INTEGER NOT NULL,
                text         TEXT NOT NULL,
                embedding    TEXT NOT NULL,
                mtime        REAL NOT NULL,
                ingested_at  TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS knowledge_by_instance
            ON knowledge(instance_id, source_path)
        """)

    except BaseException:
        await db.execute("ROLLBACK TO SAVEPOINT migration_001_baseline")
        await db.execute("RELEASE SAVEPOINT migration_001_baseline")
        raise
    else:
        await db.execute("RELEASE SAVEPOINT migration_001_baseline")


_BASELINE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "episodes": {
        "id", "instance_id", "creado_en", "despertar_por", "decision", "accion",
        "resultado", "dominio", "costo_usd", "memoria_cargada", "raw",
    },
    "tasks": {
        "id", "instance_id", "accion", "objetivo", "params", "origen_trust",
        "origen_actor", "estado", "firma", "creado_en", "due_at", "recurrence",
        "updated_en",
    },
    "jobs": {
        "id", "instance_id", "pid", "harness", "estado", "costo_usd", "creado_en",
        "terminado_en", "acked",
    },
    "desired_state": {
        "instance_id", "machine_id", "clave", "valor", "actualizado_en",
    },
    "tools": {
        "id", "instance_id", "nombre", "dominio", "repo_url", "estado",
        "metrica_baseline", "metrica_actual", "usos", "fallos", "creado_en",
    },
    "approvals": {
        "id", "instance_id", "tipo", "payload", "estado", "creado_en", "resuelto_en",
    },
    "autonomy": {"instance_id", "accion", "actor_id", "streak", "actualizado_en"},
    "facts": {
        "id", "instance_id", "tipo", "contenido", "pinned", "tags",
        "procedencia", "confianza", "creado_en", "actualizado_en",
    },
    "postulaciones": {
        "id", "instance_id", "empresa", "rol", "estado", "creado_en",
        "fuente", "link", "notas", "actualizado_en",
    },
    "fact_embeddings": {
        "fact_id", "instance_id", "dim", "vector", "model", "creado_en",
    },
    "org_messages": {
        "id", "instance_id", "kind", "from_instance", "to_instance", "status",
        "payload", "corr_id", "creado_en",
    },
    "org_commons": {"instance_id", "clave", "valor", "curator", "actualizado_en"},
    "owner_prompts": {
        "id", "instance_id", "kind", "slot", "prompt", "status", "created_at",
        "expires_at", "answered_at", "answer",
    },
    "watch_state": {"instance_id", "key", "value", "updated_en"},
    "runs": {
        "id", "instance_id", "kind", "entity_id", "parent_run_id", "status",
        "trigger", "summary", "started_at", "finished_at", "duration_ms", "cost_usd",
        "input_tokens", "output_tokens", "raw",
    },
    "run_steps": {
        "id", "instance_id", "run_id", "idx", "kind", "entity_id", "status",
        "started_at", "finished_at", "duration_ms", "cost_usd", "input_summary",
        "output_summary", "error", "raw",
    },
    "artifacts": {
        "id", "instance_id", "kind", "paths", "metadata", "origin_department",
        "episode_id", "creado_en",
    },
    "answer_cache": {
        "id", "instance_id", "question", "answer", "embedding", "tags", "hits",
        "creado_en",
    },
    "knowledge": {
        "id", "instance_id", "source_path", "chunk_index", "text", "embedding",
        "mtime", "ingested_at",
    },
}


async def _validate_migration_001(db) -> None:
    problems: list[str] = []
    for table, required in _BASELINE_REQUIRED_COLUMNS.items():
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            actual = {row[1] for row in await cur.fetchall()}
        missing = sorted(required - actual)
        if missing:
            problems.append(f"{table}: missing {', '.join(missing)}")
    if problems:
        raise MigrationValidationError("invalid baseline schema: " + "; ".join(problems))


async def _migration_002_durable_flows(db) -> None:
    """Add the operational kernel for resumable flow execution.

    ``runs`` and ``run_steps`` remain analytics projections.  These tables own
    live execution state, immutable checkpoints, attempts, and the event stream.
    """
    await db.execute("""
        CREATE TABLE flow_run_state (
            run_id              TEXT PRIMARY KEY,
            instance_id         TEXT NOT NULL,
            flow_id             TEXT NOT NULL,
            flow_digest         TEXT NOT NULL DEFAULT '',
            flow_snapshot       TEXT NOT NULL,
            status              TEXT NOT NULL CHECK(status IN (
                                    'queued', 'running', 'waiting_approval',
                                    'waiting_retry', 'completed', 'failed', 'cancelled'
                                )),
            inputs              TEXT NOT NULL DEFAULT '{}',
            frontier            TEXT NOT NULL DEFAULT '[]',
            visited             TEXT NOT NULL DEFAULT '[]',
            output              TEXT,
            checkpoint_seq      INTEGER NOT NULL DEFAULT 0 CHECK(checkpoint_seq >= 0),
            state_version       INTEGER NOT NULL DEFAULT 0 CHECK(state_version >= 0),
            requested_by        TEXT NOT NULL DEFAULT '',
            owner_department    TEXT NOT NULL DEFAULT '',
            org_chain_snapshot  TEXT NOT NULL DEFAULT '[]',
            topology_digest     TEXT NOT NULL DEFAULT '',
            idempotency_key     TEXT,
            lease_owner         TEXT,
            lease_token         TEXT,
            lease_expires_at    TEXT,
            heartbeat_at        TEXT,
            next_wakeup_at      TEXT,
            error               TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX flow_run_idempotency
        ON flow_run_state(instance_id, flow_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
    """)
    await db.execute("""
        CREATE INDEX flow_run_dispatch
        ON flow_run_state(instance_id, status, next_wakeup_at, created_at)
    """)
    await db.execute("""
        CREATE INDEX flow_run_expired_leases
        ON flow_run_state(instance_id, lease_expires_at)
        WHERE lease_token IS NOT NULL
    """)

    await db.execute("""
        CREATE TABLE flow_step_attempts (
            id              TEXT PRIMARY KEY,
            instance_id     TEXT NOT NULL,
            run_id          TEXT NOT NULL,
            node_id         TEXT NOT NULL,
            attempt         INTEGER NOT NULL CHECK(attempt >= 1),
            assigned_brain  TEXT NOT NULL DEFAULT '',
            org_corr_id     TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL CHECK(status IN (
                                'running', 'succeeded', 'failed', 'abandoned',
                                'timed_out', 'cancelled'
                            )),
            input           TEXT,
            output          TEXT,
            error           TEXT,
            effect_key      TEXT NOT NULL DEFAULT '',
            lease_token     TEXT,
            heartbeat_at    TEXT,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            FOREIGN KEY(run_id) REFERENCES flow_run_state(run_id) ON DELETE CASCADE,
            UNIQUE(run_id, node_id, attempt)
        )
    """)
    await db.execute("""
        CREATE INDEX flow_step_attempts_by_run
        ON flow_step_attempts(instance_id, run_id, node_id, attempt)
    """)

    await db.execute("""
        CREATE TABLE flow_checkpoints (
            run_id          TEXT NOT NULL,
            seq             INTEGER NOT NULL CHECK(seq >= 1),
            instance_id     TEXT NOT NULL,
            frontier        TEXT NOT NULL,
            visited         TEXT NOT NULL,
            state           TEXT NOT NULL,
            source_event_id INTEGER,
            schema_version  INTEGER NOT NULL DEFAULT 1 CHECK(schema_version >= 1),
            created_at      TEXT NOT NULL,
            PRIMARY KEY(run_id, seq),
            FOREIGN KEY(run_id) REFERENCES flow_run_state(run_id) ON DELETE CASCADE
        )
    """)
    await db.execute("""
        CREATE INDEX flow_checkpoints_by_instance_run
        ON flow_checkpoints(instance_id, run_id, seq)
    """)

    await db.execute("""
        CREATE TABLE flow_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id     TEXT NOT NULL,
            run_id          TEXT NOT NULL,
            step_attempt_id TEXT,
            kind            TEXT NOT NULL CHECK(kind IN (
                                'RUN_CREATED', 'ORG_BOUND', 'RUN_CLAIMED',
                                'RUN_STARTED', 'CHECKPOINT_SAVED', 'STEP_SCHEDULED',
                                'STEP_CLAIMED', 'STEP_STARTED', 'STEP_HEARTBEAT',
                                'STEP_COMPLETED', 'STEP_FAILED', 'EDGE_SELECTED',
                                'RETRY_SCHEDULED', 'APPROVAL_REQUESTED',
                                'APPROVAL_RESOLVED', 'RUN_PAUSED', 'RUN_RESUMED',
                                'LEASE_EXPIRED', 'RUN_COMPLETED', 'RUN_FAILED',
                                'RUN_CANCELLED'
                            )),
            payload         TEXT NOT NULL DEFAULT '{}',
            schema_version  INTEGER NOT NULL DEFAULT 1 CHECK(schema_version >= 1),
            actor_id        TEXT NOT NULL DEFAULT '',
            actor_role      TEXT NOT NULL DEFAULT '',
            dedupe_key      TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES flow_run_state(run_id) ON DELETE CASCADE,
            FOREIGN KEY(step_attempt_id) REFERENCES flow_step_attempts(id)
        )
    """)
    await db.execute("""
        CREATE INDEX flow_events_by_instance_run
        ON flow_events(instance_id, run_id, id)
    """)
    await db.execute("""
        CREATE UNIQUE INDEX flow_events_dedupe
        ON flow_events(instance_id, dedupe_key)
        WHERE dedupe_key IS NOT NULL
    """)


_DURABLE_FLOW_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "flow_run_state": {
        "run_id", "instance_id", "flow_id", "flow_snapshot", "status",
        "checkpoint_seq", "state_version", "idempotency_key", "lease_owner",
        "lease_token", "lease_expires_at", "created_at", "updated_at",
    },
    "flow_step_attempts": {
        "id", "instance_id", "run_id", "node_id", "attempt", "status",
        "effect_key", "lease_token", "started_at", "finished_at",
    },
    "flow_checkpoints": {
        "run_id", "seq", "instance_id", "frontier", "visited", "state",
        "schema_version", "created_at",
    },
    "flow_events": {
        "id", "instance_id", "run_id", "kind", "payload", "schema_version",
        "actor_id", "actor_role", "dedupe_key", "created_at",
    },
}


async def _validate_migration_002(db) -> None:
    problems: list[str] = []
    for table, required in _DURABLE_FLOW_REQUIRED_COLUMNS.items():
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            actual = {row[1] for row in await cur.fetchall()}
        missing = sorted(required - actual)
        if missing:
            problems.append(f"{table}: missing {', '.join(missing)}")
    if problems:
        raise MigrationValidationError(
            "invalid durable flow schema: " + "; ".join(problems)
        )


async def _migration_003_knowledge_size(db) -> None:
    """Record each indexed file's byte size alongside its ``mtime``.

    Incremental re-ingest compares both: an mtime match alone can miss a real
    rewrite (coarse filesystem clocks, same-second saves, mtime-preserving
    tools). ``-1`` marks pre-migration rows whose size was never captured —
    the ingester treats it as "unknown, trust the mtime".
    """
    await db.execute(
        "ALTER TABLE knowledge ADD COLUMN size INTEGER NOT NULL DEFAULT -1"
    )


async def _validate_migration_003(db) -> None:
    async with db.execute("PRAGMA table_info(knowledge)") as cur:
        actual = {row[1] for row in await cur.fetchall()}
    if "size" not in actual:
        raise MigrationValidationError("invalid knowledge schema: missing size")


async def _migration_004_projects(db) -> None:
    """The PROJECT entity — the first-class workspace Lhassa works ON.

    A project is a named unit of the owner's work (a repo, a research effort,
    an ops surface), typed by ``kind`` so project-type policy (standing review
    brains, default flows) can key off it. Scoping of existing tables happens
    WITHOUT new columns: ``runs.entity_id`` carries ``project:<id>`` and
    ``artifacts.metadata`` carries a ``project_id`` key — the namespaced-string
    convention, promoted to schema only if it ever proves insufficient.
    """
    await db.execute("""
        CREATE TABLE projects (
            id               TEXT PRIMARY KEY,
            instance_id      TEXT NOT NULL,
            name             TEXT NOT NULL,
            kind             TEXT NOT NULL DEFAULT 'generic',
            path             TEXT,
            status           TEXT NOT NULL DEFAULT 'active',
            metadata         TEXT,
            owner_department TEXT,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX projects_by_instance
        ON projects(instance_id, status, kind)
    """)


async def _validate_migration_004(db) -> None:
    async with db.execute("PRAGMA table_info(projects)") as cur:
        actual = {row[1] for row in await cur.fetchall()}
    required = {
        "id", "instance_id", "name", "kind", "path", "status",
        "metadata", "owner_department", "created_at", "updated_at",
    }
    missing = required - actual
    if missing:
        raise MigrationValidationError(f"invalid projects schema: missing {sorted(missing)}")


async def _migration_005_project_brains(db) -> None:
    """Standing-brain attachments: which org template is bound to which project.

    The durable record behind "a code project always has its reviewer brains":
    the standing-brains sense fires only for projects with no row here, and the
    ``attach_project_template`` action writes one after instantiating — the row
    IS the idempotency (mirrors ``watch_state`` dedup, but queryable both ways).
    """
    await db.execute("""
        CREATE TABLE project_brains (
            project_id      TEXT NOT NULL,
            instance_id     TEXT NOT NULL,
            template_id     TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'attached',
            instantiated_at TEXT NOT NULL,
            PRIMARY KEY (project_id, template_id)
        )
    """)
    await db.execute("""
        CREATE INDEX project_brains_by_instance
        ON project_brains(instance_id, project_id)
    """)


async def _validate_migration_005(db) -> None:
    async with db.execute("PRAGMA table_info(project_brains)") as cur:
        actual = {row[1] for row in await cur.fetchall()}
    required = {"project_id", "instance_id", "template_id", "status", "instantiated_at"}
    missing = required - actual
    if missing:
        raise MigrationValidationError(f"invalid project_brains schema: missing {sorted(missing)}")


async def _migration_006_knowledge_graph(db) -> None:
    """The persisted knowledge graph (LightRAG-lite): entities + typed edges.

    Fed $0 from the existing deterministic extraction (memory/graph.py tags +
    regex terms); validation/vocabulary discipline lives in the domain module
    (mirrors org/relationships.py), these tables stay dumb storage. ``weight``
    accumulates co-occurrence counts; ``source`` names the feeder
    (``fact:<id>`` / ``episode:<id>``) for auditability.
    """
    await db.execute("""
        CREATE TABLE kg_entities (
            id          TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'term',
            first_seen  TEXT NOT NULL,
            last_seen   TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX kg_entities_by_name
        ON kg_entities(instance_id, name)
    """)
    await db.execute("""
        CREATE TABLE kg_edges (
            instance_id TEXT NOT NULL,
            from_id     TEXT NOT NULL,
            to_id       TEXT NOT NULL,
            kind        TEXT NOT NULL,
            weight      REAL NOT NULL DEFAULT 1.0,
            source      TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (instance_id, from_id, to_id, kind)
        )
    """)
    await db.execute("""
        CREATE INDEX kg_edges_by_from
        ON kg_edges(instance_id, from_id)
    """)


async def _validate_migration_006(db) -> None:
    for table, required in (
        ("kg_entities", {"id", "instance_id", "name", "kind", "first_seen", "last_seen"}),
        ("kg_edges", {"instance_id", "from_id", "to_id", "kind", "weight", "source", "created_at", "updated_at"}),
    ):
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            actual = {row[1] for row in await cur.fetchall()}
        missing = required - actual
        if missing:
            raise MigrationValidationError(f"invalid {table} schema: missing {sorted(missing)}")


async def _migration_007_loops(db) -> None:
    """Loop engineering's record: a bounded, verifier-terminated iteration.

    A loop is the unit of WORK (a prompt is just one turn of it): a verifiable
    goal, a turn/deadline cap enforced BEFORE any work, an independent verifier
    spec (the doer never judges), and the last verdict whose detail seeds the
    next turn. The loop row is thin state; TICKING reuses the tasks table (one
    task per active loop) and the audit trail is episodes — one per turn, like
    every other observable act. Statuses walk the loop-engineering stages:
    discover|plan|execute|verify|iterate|done|failed|capped.
    """
    await db.execute("""
        CREATE TABLE loops (
            id            TEXT PRIMARY KEY,
            instance_id   TEXT NOT NULL,
            goal          TEXT NOT NULL,
            kind          TEXT NOT NULL DEFAULT 'closed',
            origin        TEXT,
            status        TEXT NOT NULL DEFAULT 'plan',
            turn          INTEGER NOT NULL DEFAULT 0,
            max_turns     INTEGER NOT NULL DEFAULT 3,
            deadline_at   TEXT,
            verifier_spec TEXT,
            last_verdict  TEXT,
            task_id       TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX loops_by_instance
        ON loops(instance_id, status)
    """)


async def _validate_migration_007(db) -> None:
    async with db.execute("PRAGMA table_info(loops)") as cur:
        actual = {row[1] for row in await cur.fetchall()}
    required = {
        "id", "instance_id", "goal", "kind", "origin", "status", "turn",
        "max_turns", "deadline_at", "verifier_spec", "last_verdict",
        "task_id", "created_at", "updated_at",
    }
    missing = required - actual
    if missing:
        raise MigrationValidationError(f"invalid loops schema: missing {sorted(missing)}")


async def _migration_008_bench_runs(db) -> None:
    """LhassaBench results: fixture x subject scored runs, deterministically.

    Eval-driven development's substrate: every bench run records which fixture
    ran against which subject (a provider, a route, a loop config), whether the
    EXISTING objective verifier passed it, and what it cost — data the router
    and the consciousness can later weigh. Append-only.
    """
    await db.execute("""
        CREATE TABLE bench_runs (
            id          TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL,
            fixture_id  TEXT NOT NULL,
            subject     TEXT NOT NULL,
            passed      INTEGER NOT NULL,
            metric      REAL,
            detail      TEXT,
            cost_usd    REAL NOT NULL DEFAULT 0.0,
            duration_ms INTEGER,
            created_at  TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX bench_runs_by_fixture
        ON bench_runs(instance_id, fixture_id, subject, created_at)
    """)


async def _validate_migration_008(db) -> None:
    async with db.execute("PRAGMA table_info(bench_runs)") as cur:
        actual = {row[1] for row in await cur.fetchall()}
    required = {"id", "instance_id", "fixture_id", "subject", "passed",
                "metric", "detail", "cost_usd", "duration_ms", "created_at"}
    missing = required - actual
    if missing:
        raise MigrationValidationError(f"invalid bench_runs schema: missing {sorted(missing)}")


async def _migration_009_idea_assessments(db) -> None:
    """Durable research funnel: queryable state plus an immutable event stream.

    ``idea_assessments`` is the current projection used by surfaces. Every
    transition is first recorded in ``idea_assessment_events``; the latter is
    append-only and is the audit source of truth.
    """
    await db.execute("""
        CREATE TABLE idea_assessments (
            id                TEXT PRIMARY KEY,
            instance_id       TEXT NOT NULL,
            idea              TEXT NOT NULL,
            evidence_digest   TEXT NOT NULL,
            evidence_path     TEXT,
            status            TEXT NOT NULL,
            investment_level  TEXT,
            decision          TEXT,
            weighted_score    REAL,
            criteria          TEXT,
            theory            TEXT,
            uncertainties     TEXT,
            assessment_path   TEXT,
            assessment_run_id TEXT,
            research_run_id   TEXT,
            draft_path        TEXT,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX idea_assessments_by_evidence
        ON idea_assessments(instance_id, evidence_digest)
    """)
    await db.execute("""
        CREATE INDEX idea_assessments_by_state
        ON idea_assessments(instance_id, status, investment_level, updated_at)
    """)
    await db.execute("""
        CREATE TABLE idea_assessment_events (
            id             TEXT PRIMARY KEY,
            instance_id    TEXT NOT NULL,
            assessment_id  TEXT NOT NULL,
            stage          TEXT NOT NULL,
            from_status    TEXT,
            to_status      TEXT NOT NULL,
            payload        TEXT,
            created_at     TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX idea_assessment_events_by_assessment
        ON idea_assessment_events(instance_id, assessment_id, created_at, id)
    """)


async def _validate_migration_009(db) -> None:
    expected = {
        "idea_assessments": {
            "id", "instance_id", "idea", "evidence_digest", "evidence_path",
            "status", "investment_level", "decision", "weighted_score",
            "criteria", "theory", "uncertainties", "assessment_path",
            "assessment_run_id", "research_run_id", "draft_path", "created_at",
            "updated_at",
        },
        "idea_assessment_events": {
            "id", "instance_id", "assessment_id", "stage", "from_status",
            "to_status", "payload", "created_at",
        },
    }
    for table, required in expected.items():
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            actual = {row[1] for row in await cur.fetchall()}
        missing = required - actual
        if missing:
            raise MigrationValidationError(
                f"invalid {table} schema: missing {sorted(missing)}"
            )


async def _migration_010_conformance(db) -> None:
    """Executable philosophy: integrity runs, checks, findings and projection."""
    await db.execute("""
        CREATE TABLE conformance_runs (
            id                     TEXT PRIMARY KEY,
            instance_id            TEXT NOT NULL,
            manifest_version       INTEGER NOT NULL,
            trigger                TEXT NOT NULL,
            status                 TEXT NOT NULL,
            checks_total           INTEGER NOT NULL DEFAULT 0,
            checks_passed          INTEGER NOT NULL DEFAULT 0,
            findings_warn          INTEGER NOT NULL DEFAULT 0,
            findings_critical      INTEGER NOT NULL DEFAULT 0,
            started_at             TEXT NOT NULL,
            finished_at            TEXT,
            attestation_expires_at TEXT
        )
    """)
    await db.execute("""
        CREATE INDEX conformance_runs_by_instance
        ON conformance_runs(instance_id, started_at)
    """)
    await db.execute("""
        CREATE TABLE conformance_checks (
            id           TEXT PRIMARY KEY,
            instance_id  TEXT NOT NULL,
            run_id       TEXT NOT NULL,
            contract_id  TEXT NOT NULL,
            version      INTEGER NOT NULL,
            severity     TEXT NOT NULL,
            passed       INTEGER NOT NULL,
            summary      TEXT NOT NULL,
            evidence     TEXT,
            duration_ms  INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX conformance_checks_by_run
        ON conformance_checks(instance_id, run_id, contract_id)
    """)
    await db.execute("""
        CREATE TABLE conformance_findings (
            id           TEXT PRIMARY KEY,
            instance_id  TEXT NOT NULL,
            run_id       TEXT NOT NULL,
            check_id     TEXT NOT NULL,
            contract_id  TEXT NOT NULL,
            severity     TEXT NOT NULL,
            summary      TEXT NOT NULL,
            evidence     TEXT,
            created_at   TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX conformance_findings_by_instance
        ON conformance_findings(instance_id, created_at)
    """)
    await db.execute("""
        CREATE TABLE conformance_state (
            instance_id       TEXT NOT NULL,
            contract_id       TEXT NOT NULL,
            contract_version  INTEGER NOT NULL,
            last_run_id       TEXT NOT NULL,
            passed            INTEGER NOT NULL,
            severity          TEXT NOT NULL,
            summary           TEXT NOT NULL,
            evidence          TEXT,
            checked_at        TEXT NOT NULL,
            PRIMARY KEY(instance_id, contract_id)
        )
    """)


async def _validate_migration_010(db) -> None:
    expected = {
        "conformance_runs": {
            "id", "instance_id", "manifest_version", "trigger", "status",
            "checks_total", "checks_passed", "findings_warn",
            "findings_critical", "started_at", "finished_at",
            "attestation_expires_at",
        },
        "conformance_checks": {
            "id", "instance_id", "run_id", "contract_id", "version",
            "severity", "passed", "summary", "evidence", "duration_ms",
            "created_at",
        },
        "conformance_findings": {
            "id", "instance_id", "run_id", "check_id", "contract_id",
            "severity", "summary", "evidence", "created_at",
        },
        "conformance_state": {
            "instance_id", "contract_id", "contract_version", "last_run_id",
            "passed", "severity", "summary", "evidence", "checked_at",
        },
    }
    for table, required in expected.items():
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            actual = {row[1] for row in await cur.fetchall()}
        missing = required - actual
        if missing:
            raise MigrationValidationError(
                f"invalid {table} schema: missing {sorted(missing)}"
            )


async def _migration_011_multiuser(db) -> None:
    """Multi-user Phase A: ``actor_id`` as a first-class column + shared resources.

    Additive and backward-compatible (Stream 6 Phase A): existing rows never
    lose data, they just get a WHO. ``actor_id`` is added to the three tables
    where "who did/authored this" is genuinely load-bearing — approvals (who
    proposed it; it already rides in ``payload["actor_id"]`` via
    :class:`lhassa.security.trust.Origin`, this promotes it to a queryable
    column), facts (who authored a durable memory row) and knowledge (who
    ingested a chunk) — defaulting existing rows to ``Config.root_admin_id``
    (the owner, "lhassa"), never blank.

    ``shared_resources`` / ``resource_subscriptions`` are the new tables behind
    cross-instance sharing (:mod:`lhassa.org.sharing`): a CLOSED, small set of
    shareable kinds (skill/flow/template/knowledge) — deliberately NOT facts or
    episodes, which stay private by construction (there is no column here to
    mark a fact/episode "shared", so leaking memory across instances is a
    schema-level impossibility, not just a runtime check).
    """
    for table in ("approvals", "facts", "knowledge"):
        await db.execute(f"ALTER TABLE {table} ADD COLUMN actor_id TEXT")
    # Backfill: an approval's actor_id was already carried in its JSON payload
    # (Origin.actor_id) — recover it where present, else the owner.
    async with db.execute("SELECT id, payload FROM approvals") as cur:
        approval_rows = await cur.fetchall()
    for row in approval_rows:
        approval_id, payload_raw = row[0], row[1]
        actor = Config.root_admin_id
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
            actor = str(payload.get("actor_id") or "").strip() or actor
        except (ValueError, TypeError):
            pass
        await db.execute(
            "UPDATE approvals SET actor_id = ? WHERE id = ?", (actor, approval_id)
        )
    await db.execute(
        "UPDATE facts SET actor_id = ? WHERE actor_id IS NULL",
        (Config.root_admin_id,),
    )
    await db.execute(
        "UPDATE knowledge SET actor_id = ? WHERE actor_id IS NULL",
        (Config.root_admin_id,),
    )

    # shared_resources — what THIS instance has published for other instances to
    # discover. The CLOSED shareable-kind vocabulary is enforced in Python
    # (lhassa.org.sharing.SHAREABLE_TYPES), never in schema — but no column here
    # ever names "fact" or "episode" as a resource_type, so the private plane is
    # structurally absent from this table, not merely filtered out at read time.
    await db.execute("""
        CREATE TABLE shared_resources (
            id            TEXT PRIMARY KEY,
            instance_id   TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_name TEXT NOT NULL,
            actor_id      TEXT NOT NULL,
            published_at  TEXT NOT NULL,
            revoked_at    TEXT
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX shared_resources_by_offer
        ON shared_resources(instance_id, resource_type, resource_name)
    """)

    # resource_subscriptions — what THIS instance has subscribed to from another
    # instance's offerings. Always minted AFTER a permits() check (deny-by-default
    # — see lhassa.org.sharing.subscribe): the row itself is not a grant, it is a
    # record that a permitted subscription happened.
    await db.execute("""
        CREATE TABLE resource_subscriptions (
            id                  TEXT PRIMARY KEY,
            instance_id         TEXT NOT NULL,
            source_instance_id  TEXT NOT NULL,
            resource_type       TEXT NOT NULL,
            resource_name       TEXT NOT NULL,
            actor_id            TEXT NOT NULL,
            subscribed_at       TEXT NOT NULL,
            unsubscribed_at     TEXT
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX resource_subscriptions_by_source
        ON resource_subscriptions(instance_id, source_instance_id, resource_type, resource_name)
    """)


async def _validate_migration_011(db) -> None:
    for table in ("approvals", "facts", "knowledge"):
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "actor_id" not in cols:
            raise MigrationValidationError(f"invalid {table} schema: missing actor_id")
    expected = {
        "shared_resources": {
            "id", "instance_id", "resource_type", "resource_name", "actor_id",
            "published_at", "revoked_at",
        },
        "resource_subscriptions": {
            "id", "instance_id", "source_instance_id", "resource_type",
            "resource_name", "actor_id", "subscribed_at", "unsubscribed_at",
        },
    }
    for table, required in expected.items():
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            actual = {row[1] for row in await cur.fetchall()}
        missing = required - actual
        if missing:
            raise MigrationValidationError(
                f"invalid {table} schema: missing {sorted(missing)}"
            )


async def _migration_012_answer_cache_embedder(db) -> None:
    """``answer_cache.embedder`` — the embedder stamp that produced ``embedding``.

    Mirrors the ``fact_embeddings.model`` stamp (migration 001's Phase 5 add):
    without it, ``memory.answer_cache.lookup`` has no way to tell whether a
    stored vector came from the same embedder as the current query vector, so
    a cosine comparison across embedders (garbage) looks identical to a real
    match. Existing rows all predate the stamp and were produced by the
    offline default (``HashingEmbedder.name``, hardcoded here rather than
    imported — ``core/`` is inner to ``memory/`` and must not import it), so
    they backfill to that literal name; anything embedded after this
    migration carries the embedder that actually produced it.
    """
    if not await _has_column(db, "answer_cache", "embedder"):
        await db.execute("ALTER TABLE answer_cache ADD COLUMN embedder TEXT")
        await db.execute(
            "UPDATE answer_cache SET embedder = ? WHERE embedder IS NULL",
            ("hashing-v1",),
        )


async def _validate_migration_012(db) -> None:
    if not await _has_column(db, "answer_cache", "embedder"):
        raise MigrationValidationError(
            "invalid answer_cache schema: missing embedder"
        )


MIGRATIONS = (
    Migration(1, "baseline", _migration_001_baseline, _validate_migration_001),
    Migration(2, "durable-flows", _migration_002_durable_flows, _validate_migration_002),
    Migration(3, "knowledge-size", _migration_003_knowledge_size, _validate_migration_003),
    Migration(4, "projects", _migration_004_projects, _validate_migration_004),
    Migration(5, "project-brains", _migration_005_project_brains, _validate_migration_005),
    Migration(6, "knowledge-graph", _migration_006_knowledge_graph, _validate_migration_006),
    Migration(7, "loops", _migration_007_loops, _validate_migration_007),
    Migration(8, "bench-runs", _migration_008_bench_runs, _validate_migration_008),
    Migration(
        9,
        "idea-assessments",
        _migration_009_idea_assessments,
        _validate_migration_009,
    ),
    Migration(10, "conformance", _migration_010_conformance, _validate_migration_010),
    Migration(11, "multiuser", _migration_011_multiuser, _validate_migration_011),
    Migration(
        12,
        "answer-cache-embedder",
        _migration_012_answer_cache_embedder,
        _validate_migration_012,
    ),
)
SCHEMA_VERSION = MIGRATIONS[-1].version


async def init_db() -> None:
    """Enable WAL and migrate the grande to the supported schema version."""
    path = Path(_pkg().DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    async with aiosqlite.connect(path) as db:
        # Refuse a newer DB before changing even its journal mode.
        version = await get_schema_version(db)
        ensure_supported(version, SCHEMA_VERSION)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        # Already brought this DB file to the current schema in THIS process —
        # the migration/validation pass (the expensive part) is a no-op.
        if _MIGRATED_VERSIONS.get(key) == version:
            return
        migrated_version = await apply_migrations(db, MIGRATIONS)
        _MIGRATED_VERSIONS[key] = migrated_version


# ── episodes (append-only) ───────────────────────────────────────────────────────

