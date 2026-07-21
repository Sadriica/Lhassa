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


MIGRATIONS = (
    Migration(1, "baseline", _migration_001_baseline, _validate_migration_001),
    Migration(2, "durable-flows", _migration_002_durable_flows, _validate_migration_002),
    Migration(3, "knowledge-size", _migration_003_knowledge_size, _validate_migration_003),
)
SCHEMA_VERSION = MIGRATIONS[-1].version


async def init_db() -> None:
    """Enable WAL and migrate the grande to the supported schema version."""
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        # Refuse a newer DB before changing even its journal mode.
        ensure_supported(await get_schema_version(db), SCHEMA_VERSION)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await apply_migrations(db, MIGRATIONS)


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


async def count_episodes(instance_id: str) -> int:
    """Return the total number of episodes for ``instance_id``."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM episodes WHERE instance_id = ?",
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] if row else 0)


async def count_episodes_since(instance_id: str, since: str) -> int:
    """Return the number of episodes for ``instance_id`` at or after ``since``."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM episodes
               WHERE instance_id = ? AND creado_en >= ?""",
            (instance_id, since),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] if row else 0)


# ── recall (FTS5 full-text search over episodes — Fase 1) ───────────────────────
#
# $0, deterministic full-text search over the episode log — no model call. The
# query is SANITIZED into a safe FTS5 MATCH expression (each token individually
# double-quoted) so arbitrary user input (quotes, parens, AND/OR/NOT, dangling
# operators) can never raise an FTS5 syntax error. Automation-originated episodes
# (``despertar_por`` is ``monitor`` or starts with ``task:``) are DEMOTED (not
# hidden) in ranking so interactive history surfaces first.


def _fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Each whitespace-delimited token is individually double-quoted (FTS5 string
    syntax: a doubled `"` escapes a literal quote), then the tokens are joined
    with an implicit AND. This defuses any FTS5 operator/punctuation the user
    might type (``AND``, ``OR``, ``NOT``, ``(``, ``)``, a bare ``"``) — the
    result is always a valid MATCH argument, never a syntax error.
    """
    tokens = query.split()
    quoted = ['"' + tok.replace('"', '""') + '"' for tok in tokens if tok]
    return " ".join(quoted)


async def search_episodes(instance_id: str, query: str, n: int = 10) -> list[dict]:
    """BM25-ranked full-text search over ``instance_id``'s episode log.

    Returns up to ``n`` dicts: ``id``, ``despertar_por``, ``decision``, ``accion``,
    a ``resultado`` snippet (via FTS5 ``snippet()``), ``creado_en`` and ``rank``.
    Automation episodes (``despertar_por`` == ``monitor`` or starting with
    ``task:``) are ranked BELOW interactive ones for an equally-good match (a
    CASE-based penalty added to the raw bm25 rank), never hidden. An empty/blank
    ``query`` returns ``[]`` with no query executed. If the FTS5 index is
    missing (older SQLite build), returns ``[]`` (debug-logged) rather than
    raising — recall degrades to "no results", never a crash.
    """
    query = (query or "").strip()
    if not query:
        return []
    match_expr = _fts_query(query)
    if not match_expr:
        return []

    sql = """
        SELECT e.id, e.despertar_por, e.decision, e.accion, e.creado_en,
               snippet(episodes_fts, 2, '[', ']', '…', 8) AS snippet,
               bm25(episodes_fts) + (CASE
                   WHEN e.despertar_por = 'monitor' THEN 5.0
                   WHEN e.despertar_por LIKE 'task:%' THEN 5.0
                   ELSE 0.0
               END) AS rank
        FROM episodes_fts
        JOIN episodes e ON e.rowid = episodes_fts.rowid
        WHERE episodes_fts MATCH ? AND e.instance_id = ?
        ORDER BY rank
        LIMIT ?
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, (match_expr, instance_id, n)) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
    except Exception as exc:
        logger.debug(f"db: search_episodes unavailable ({exc}); returning no results")
        return []

    return [
        {
            "id": row["id"],
            "despertar_por": row["despertar_por"],
            "decision": row["decision"],
            "accion": row["accion"],
            "resultado": row["snippet"],
            "creado_en": row["creado_en"],
            "rank": row["rank"],
        }
        for row in rows
    ]


# ── runs / run_steps (durable execution analytics) ─────────────────────────────

async def record_run(row: dict) -> str:
    """Insert or replace one durable run record, scoped to ``instance_id``."""
    run_id = row.get("id") or _new_id("run")
    instance_id = row["instance_id"]
    raw = row.get("raw")
    if not isinstance(raw, str):
        raw = json.dumps(raw if raw is not None else row, ensure_ascii=False)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO runs(
                   id, instance_id, kind, entity_id, parent_run_id, status, trigger,
                   summary, started_at, finished_at, duration_ms, cost_usd,
                   input_tokens, output_tokens, raw)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                instance_id,
                row.get("kind") or "",
                row.get("entity_id"),
                row.get("parent_run_id"),
                row.get("status") or "completed",
                row.get("trigger"),
                row.get("summary"),
                row.get("started_at") or _now(),
                row.get("finished_at"),
                int(row.get("duration_ms") or 0),
                float(row.get("cost_usd") or 0.0),
                int(row.get("input_tokens") or 0),
                int(row.get("output_tokens") or 0),
                raw,
            ),
        )
        await db.commit()
    return run_id


async def record_run_step(row: dict) -> str:
    """Insert one durable run step row, scoped to ``instance_id``."""
    step_id = row.get("id") or _new_id("step")
    raw = row.get("raw")
    if not isinstance(raw, str):
        raw = json.dumps(raw if raw is not None else row, ensure_ascii=False)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO run_steps(
                   id, instance_id, run_id, idx, kind, entity_id, status,
                   started_at, finished_at, duration_ms, cost_usd,
                   input_summary, output_summary, error, raw)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                step_id,
                row["instance_id"],
                row["run_id"],
                int(row.get("idx") or 0),
                row.get("kind") or "",
                row.get("entity_id"),
                row.get("status") or "completed",
                row.get("started_at"),
                row.get("finished_at"),
                int(row.get("duration_ms") or 0),
                float(row.get("cost_usd") or 0.0),
                row.get("input_summary"),
                row.get("output_summary"),
                row.get("error"),
                raw,
            ),
        )
        await db.commit()
    return step_id


async def list_runs(
    instance_id: str,
    *,
    kind: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """Return durable runs, newest first, optionally filtered by kind/entity."""
    sql = "SELECT * FROM runs WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if kind is not None:
        sql += " AND kind = ?"
        args.append(kind)
    if entity_id is not None:
        sql += " AND entity_id = ?"
        args.append(entity_id)
    sql += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
    args.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["raw"] = _loads(row.get("raw"), {})
    return rows


async def list_run_steps(instance_id: str, run_id: str) -> list[dict]:
    """Return the steps for one run, ordered by execution index."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM run_steps
               WHERE instance_id = ? AND run_id = ?
               ORDER BY idx, rowid""",
            (instance_id, run_id),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["raw"] = _loads(row.get("raw"), {})
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
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tasks(
                   id, instance_id, accion, objetivo, params,
                   origen_trust, origen_actor, estado, firma, creado_en,
                   due_at, recurrence, updated_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, instance_id, accion, objetivo, _dumps(params or {}),
                origen_trust, origen_actor, estado, firma, now,
                due_at, recurrence, now,
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
    """Update a task's state, scoped to ``instance_id``.

    Also stamps ``updated_en`` — the deferred-retry sweep uses it to know how
    long a task has sat "diferida" before it is due for a retry.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET estado = ?, updated_en = ? WHERE instance_id = ? AND id = ?",
            (estado, _now(), instance_id, task_id),
        )
        await db.commit()


# ── deferred-task retry sweep (Phase-Resilience) ──────────────────────────────
#
# A task the judgment path could not run (no usable provider, or the daily
# budget spent) is marked "diferida" and, today, never revisited — the provider
# outage/budget window can pass and the task simply rots. This sweep re-queues
# a "diferida" task back to "pendiente" once it has sat for at least
# ``backoff_seconds``, tracking a retry counter in its params; once that counter
# reaches ``max_retries`` the task is marked "rechazada" (terminal — never
# retried again) instead of being requeued forever.


async def sweep_deferred_tasks(
    instance_id: str, *, backoff_seconds: float, max_retries: int
) -> dict:
    """Re-queue stale "diferida" tasks; reject the ones that exhausted retries.

    A "diferida" task whose ``updated_en`` is older than ``backoff_seconds`` is
    either requeued to "pendiente" (with ``params["_retries"]`` incremented) or,
    if it has already reached ``max_retries``, marked "rechazada" (terminal).
    Returns ``{"requeued": int, "rejected_ids": [task_id, ...]}``. Scoped to
    ``instance_id``. This function itself does not raise on a normal DB error
    path (the caller — ``step()`` — additionally wraps the call in a
    try/except so a sweep failure can never crash a heartbeat tick).
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=max(0.0, backoff_seconds))
    ).isoformat()

    requeued = 0
    rejected_ids: list[str] = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM tasks
               WHERE instance_id = ? AND estado = 'diferida'
                 AND (updated_en IS NULL OR updated_en <= ?)""",
            (instance_id, cutoff),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]

        now = _now()
        for row in rows:
            params = _loads(row.get("params"), {})
            if not isinstance(params, dict):
                params = {}
            retries = int(params.get("_retries") or 0)
            if retries >= max(0, max_retries):
                await db.execute(
                    "UPDATE tasks SET estado = 'rechazada', updated_en = ? "
                    "WHERE instance_id = ? AND id = ?",
                    (now, instance_id, row["id"]),
                )
                rejected_ids.append(row["id"])
            else:
                params["_retries"] = retries + 1
                await db.execute(
                    "UPDATE tasks SET estado = 'pendiente', params = ?, updated_en = ? "
                    "WHERE instance_id = ? AND id = ?",
                    (_dumps(params), now, instance_id, row["id"]),
                )
                requeued += 1
        await db.commit()

    return {"requeued": requeued, "rejected_ids": rejected_ids}


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


async def add_owner_prompt(
    instance_id: str,
    *,
    kind: str,
    slot: str = "",
    prompt: str = "",
    expires_at: str,
) -> str:
    """Add one pending owner-facing prompt, replacing older pending prompts of same kind."""
    now = _now()
    prompt_id = _new_id("op")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE owner_prompts
                  SET status = 'superseded'
                WHERE instance_id = ? AND kind = ? AND status = 'pending'""",
            (instance_id, kind),
        )
        await db.execute(
            """INSERT INTO owner_prompts(
                   id, instance_id, kind, slot, prompt, status,
                   created_at, expires_at)
               VALUES(?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (prompt_id, instance_id, kind, slot, prompt, now, expires_at),
        )
        await db.commit()
    return prompt_id


async def pending_owner_prompt(
    instance_id: str,
    *,
    kind: str,
    now: Optional[str] = None,
) -> Optional[dict]:
    """Return the newest unexpired pending owner prompt for ``kind``."""
    now = now or _now()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM owner_prompts
               WHERE instance_id = ?
                 AND kind = ?
                 AND status = 'pending'
                 AND expires_at >= ?
               ORDER BY created_at DESC, rowid DESC
               LIMIT 1""",
            (instance_id, kind, now),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def consume_owner_prompt(
    instance_id: str,
    prompt_id: str,
    *,
    answer: str,
) -> None:
    """Mark a pending owner prompt as answered."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE owner_prompts
                  SET status = 'answered', answered_at = ?, answer = ?
                WHERE instance_id = ? AND id = ? AND status = 'pending'""",
            (_now(), answer, instance_id, prompt_id),
        )
        await db.commit()


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


async def resolve_approval(instance_id: str, approval_id: str, estado: str) -> bool:
    """CAS a pending approval and return whether this caller resolved it."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """UPDATE approvals
               SET estado = ?, resuelto_en = ?
               WHERE instance_id = ? AND id = ? AND estado = 'pendiente'""",
            (estado, _now(), instance_id, approval_id),
        )
        await db.commit()
    return cur.rowcount == 1


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


# ── artifacts (append-only work products — "Lhassa is the sender of record") ────

async def insert_artifact(row: dict) -> str:
    """Insert one artifact row into the grande. Returns the artifact id.

    ``row`` carries every column (id, instance_id, kind, paths, metadata,
    origin_department, episode_id, creado_en); ``paths``/``metadata`` may be a
    pre-encoded JSON string or a raw list/dict (encoded here). Append-only: this
    never overwrites an existing artifact. Scoped to ``instance_id``.
    """
    paths = row.get("paths")
    paths_json = paths if isinstance(paths, str) else _dumps(list(paths or []))
    metadata = row.get("metadata")
    metadata_json = (
        metadata if isinstance(metadata, str) else json.dumps(metadata or {}, ensure_ascii=False)
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO artifacts(
                   id, instance_id, kind, paths, metadata,
                   origin_department, episode_id, creado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["instance_id"],
                row["kind"],
                paths_json,
                metadata_json,
                row.get("origin_department", ""),
                row.get("episode_id"),
                row["creado_en"],
            ),
        )
        await db.commit()
    return row["id"]


async def get_artifact(instance_id: str, artifact_id: str) -> Optional[dict]:
    """Return one artifact row (paths/metadata decoded), or ``None`` if absent.

    Scoped to ``instance_id`` (isolation from day one).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM artifacts WHERE instance_id = ? AND id = ?",
            (instance_id, artifact_id),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["paths"] = _loads(out.get("paths"), [])
    out["metadata"] = _loads(out.get("metadata"), {})
    return out


async def list_artifacts(instance_id: str, *, limit: int = 50) -> list[dict]:
    """Return artifacts for ``instance_id`` (newest first, cap ``limit``).

    ``paths``/``metadata`` decoded. Always scoped to ``instance_id``.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM artifacts
               WHERE instance_id = ?
               ORDER BY creado_en DESC, rowid DESC
               LIMIT ?""",
            (instance_id, limit),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["paths"] = _loads(row.get("paths"), [])
        row["metadata"] = _loads(row.get("metadata"), {})
    return rows


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


# ── answer_cache (the "$0 intelligence" cache over past chat answers) ───────────

async def insert_answer_cache(row: dict) -> str:
    """Insert one cached (question, answer) row. Returns its id.

    ``row`` needs ``instance_id``, ``question``, ``answer``, ``embedding`` (a list
    of floats) and optionally ``tags`` (a list). Append-only, like ``facts``: a
    later store for the same question is a NEW row, not an overwrite — the
    lookup's cosine scan picks the best (and freshest-on-tie) match regardless.
    """
    cache_id = row.get("id") or _new_id("ac")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO answer_cache(
                   id, instance_id, question, answer, embedding, tags, hits, creado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cache_id,
                row["instance_id"],
                row["question"],
                row["answer"],
                _dumps(list(row.get("embedding") or [])),
                _dumps(list(row.get("tags") or [])),
                int(row.get("hits") or 0),
                row.get("creado_en") or _now(),
            ),
        )
        await db.commit()
    return cache_id


async def list_answer_cache(instance_id: str, limit: int = 500) -> list[dict]:
    """Return the ``limit`` most recent cached rows for ``instance_id``.

    ``embedding``/``tags`` are decoded back into lists. Scoped to ``instance_id``.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM answer_cache
               WHERE instance_id = ?
               ORDER BY creado_en DESC, rowid DESC
               LIMIT ?""",
            (instance_id, limit),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["embedding"] = _loads(row.get("embedding"), [])
        row["tags"] = _loads(row.get("tags"), [])
    return rows


async def bump_answer_cache_hit(instance_id: str, cache_id: str) -> None:
    """Increment the ``hits`` counter for one cached row, scoped to ``instance_id``."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE answer_cache SET hits = hits + 1 WHERE instance_id = ? AND id = ?",
            (instance_id, cache_id),
        )
        await db.commit()


# ── knowledge (document perception — § memory.knowledge) ────────────────────

async def insert_knowledge_chunk(row: dict) -> str:
    """Insert one indexed chunk. Returns its id.

    ``row`` needs ``instance_id``, ``source_path``, ``chunk_index``, ``text``,
    ``embedding`` (a list of floats), ``mtime`` (a float, the source file's
    mtime at ingestion time) and ``size`` (the file's byte size; together with
    ``mtime`` it's how incremental re-ingest detects a change — ``-1`` = unknown).
    """
    chunk_id = row.get("id") or _new_id("kn")
    size = row.get("size")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO knowledge(
                   id, instance_id, source_path, chunk_index, text, embedding,
                   mtime, size, ingested_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk_id,
                row["instance_id"],
                row["source_path"],
                int(row.get("chunk_index") or 0),
                row["text"],
                _dumps(list(row.get("embedding") or [])),
                float(row.get("mtime") or 0.0),
                -1 if size is None else int(size),
                row.get("ingested_at") or _now(),
            ),
        )
        await db.commit()
    return chunk_id


async def list_knowledge_files(instance_id: str) -> dict[str, tuple[float, int]]:
    """Return ``{source_path: (mtime, size)}`` for every file currently indexed.

    One row per distinct ``source_path`` (its chunks all share the same
    ``mtime``/``size``, stamped at ingestion) — used by
    :func:`lhassa.memory.knowledge.ingest` to decide, per file, whether it's
    unchanged (skip), new (index) or touched (delete old chunks, re-index).
    ``size`` is ``-1`` for rows ingested before the size column existed.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT source_path, MAX(mtime) AS mtime, MAX(size) AS size
               FROM knowledge
               WHERE instance_id = ?
               GROUP BY source_path""",
            (instance_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {
        row["source_path"]: (float(row["mtime"]), int(row["size"]))
        for row in rows
    }


async def delete_knowledge_file(instance_id: str, source_path: str) -> None:
    """Delete every chunk indexed for ``source_path`` (a re-index or a removal)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM knowledge WHERE instance_id = ? AND source_path = ?",
            (instance_id, source_path),
        )
        await db.commit()


async def list_knowledge_chunks(instance_id: str, limit: int = 5000) -> list[dict]:
    """Return up to ``limit`` most recently ingested chunks for ``instance_id``.

    ``embedding`` is decoded back into a list of floats. Bounded so a search
    over a large index stays a cheap, in-memory cosine scan (mirrors
    ``list_answer_cache``'s bounded-recent-set pattern).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM knowledge
               WHERE instance_id = ?
               ORDER BY ingested_at DESC, rowid DESC
               LIMIT ?""",
            (instance_id, limit),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["embedding"] = _loads(row.get("embedding"), [])
    return rows


async def clear_knowledge(instance_id: str) -> int:
    """Wipe the whole knowledge index for ``instance_id``. Returns rows deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM knowledge WHERE instance_id = ?", (instance_id,)
        )
        await db.commit()
        return cur.rowcount if cur.rowcount is not None else 0
