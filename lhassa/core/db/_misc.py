# SPDX-License-Identifier: AGPL-3.0-or-later
"""Idea assessments, conformance runs, and self-written tools.

Split out of the former monolithic ``lhassa/core/db.py``; part of the
``lhassa.core.db`` facade package (see ``lhassa/core/db/__init__.py``).
"""

from __future__ import annotations

from typing import Any, Optional

from lhassa._vendor import aiosqlite

from ._conn import _pkg

_IDEA_JSON_FIELDS = ("criteria", "theory", "uncertainties")
_IDEA_UPDATE_FIELDS = {
    "evidence_path", "investment_level", "decision", "weighted_score",
    "criteria", "theory", "uncertainties", "assessment_path",
    "assessment_run_id", "research_run_id", "draft_path",
}


def _decode_idea_assessment(row: dict) -> dict:
    out = dict(row)
    for field in _IDEA_JSON_FIELDS:
        default = [] if field == "uncertainties" else {}
        out[field] = _pkg()._loads(out.get(field), default)
    return out


async def create_idea_assessment(
    instance_id: str,
    *,
    idea: str,
    evidence_digest: str,
    evidence_path: Optional[str] = None,
    payload: Optional[dict] = None,
) -> dict:
    """Create the funnel projection and its first immutable event, idempotently."""
    now = _pkg()._now()
    assessment_id = _pkg()._new_id("idea")
    event_id = _pkg()._new_id("ievt")
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                """SELECT * FROM idea_assessments
                   WHERE instance_id = ? AND evidence_digest = ?""",
                (instance_id, evidence_digest),
            ) as cur:
                existing = await cur.fetchone()
            if existing is not None:
                await conn.commit()
                return _decode_idea_assessment(dict(existing))
            await conn.execute(
                """INSERT INTO idea_assessments(
                       id, instance_id, idea, evidence_digest, evidence_path,
                       status, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, 'captured', ?, ?)""",
                (
                    assessment_id,
                    instance_id,
                    idea,
                    evidence_digest,
                    evidence_path,
                    now,
                    now,
                ),
            )
            await conn.execute(
                """INSERT INTO idea_assessment_events(
                       id, instance_id, assessment_id, stage, from_status,
                       to_status, payload, created_at)
                   VALUES(?, ?, ?, 'evidence_captured', NULL, 'captured', ?, ?)""",
                (
                    event_id,
                    instance_id,
                    assessment_id,
                    _pkg()._dumps(payload or {}),
                    now,
                ),
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
    return await get_idea_assessment(instance_id, assessment_id)  # type: ignore[return-value]


async def transition_idea_assessment(
    instance_id: str,
    assessment_id: str,
    *,
    stage: str,
    to_status: str,
    updates: Optional[dict] = None,
    payload: Optional[dict] = None,
    expected_status: Optional[str] = None,
) -> dict:
    """Atomically append a stage event and advance the queryable projection."""
    clean_updates = {
        key: value
        for key, value in (updates or {}).items()
        if key in _IDEA_UPDATE_FIELDS
    }
    now = _pkg()._now()
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                """SELECT * FROM idea_assessments
                   WHERE instance_id = ? AND id = ?""",
                (instance_id, assessment_id),
            ) as cur:
                current = await cur.fetchone()
            if current is None:
                raise KeyError(assessment_id)
            from_status = str(current["status"])
            if expected_status is not None and from_status != expected_status:
                raise ValueError(
                    f"idea assessment {assessment_id} is {from_status!r}, "
                    f"expected {expected_status!r}"
                )

            columns = ["status = ?", "updated_at = ?"]
            values: list[Any] = [to_status, now]
            for key, value in clean_updates.items():
                columns.append(f"{key} = ?")
                values.append(_pkg()._dumps(value) if key in _IDEA_JSON_FIELDS else value)
            values.extend([instance_id, assessment_id])
            await conn.execute(
                f"""UPDATE idea_assessments SET {", ".join(columns)}
                    WHERE instance_id = ? AND id = ?""",
                values,
            )
            event_payload = dict(payload or {})
            if clean_updates:
                event_payload.setdefault("updates", clean_updates)
            await conn.execute(
                """INSERT INTO idea_assessment_events(
                       id, instance_id, assessment_id, stage, from_status,
                       to_status, payload, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _pkg()._new_id("ievt"),
                    instance_id,
                    assessment_id,
                    stage,
                    from_status,
                    to_status,
                    _pkg()._dumps(event_payload),
                    now,
                ),
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
    return await get_idea_assessment(instance_id, assessment_id)  # type: ignore[return-value]


async def get_idea_assessment(
    instance_id: str, assessment_id: str
) -> Optional[dict]:
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM idea_assessments
               WHERE instance_id = ? AND id = ?""",
            (instance_id, assessment_id),
        ) as cur:
            row = await cur.fetchone()
    return _decode_idea_assessment(dict(row)) if row is not None else None


async def find_idea_assessment_by_draft(
    instance_id: str, draft_path: str
) -> Optional[dict]:
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM idea_assessments
               WHERE instance_id = ? AND draft_path = ?
               ORDER BY updated_at DESC LIMIT 1""",
            (instance_id, draft_path),
        ) as cur:
            row = await cur.fetchone()
    return _decode_idea_assessment(dict(row)) if row is not None else None


async def list_idea_assessments(
    instance_id: str,
    *,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    sql = "SELECT * FROM idea_assessments WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    return [_decode_idea_assessment(row) for row in rows]


async def list_idea_assessment_events(
    instance_id: str, assessment_id: str
) -> list[dict]:
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM idea_assessment_events
               WHERE instance_id = ? AND assessment_id = ?
               ORDER BY created_at, id""",
            (instance_id, assessment_id),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["payload"] = _pkg()._loads(row.get("payload"), {})
    return rows


# ── conformance: executable contracts + append-only evidence ─────────────────

async def start_conformance_run(
    instance_id: str, *, manifest_version: int, trigger: str
) -> str:
    run_id = _pkg()._new_id("conf")
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO conformance_runs(
                   id, instance_id, manifest_version, trigger, status, started_at)
               VALUES(?, ?, ?, ?, 'running', ?)""",
            (run_id, instance_id, manifest_version, trigger, _pkg()._now()),
        )
        await conn.commit()
    return run_id


async def record_conformance_check(
    instance_id: str,
    run_id: str,
    *,
    contract_id: str,
    version: int,
    severity: str,
    passed: bool,
    summary: str,
    evidence: Optional[dict] = None,
    duration_ms: int = 0,
) -> str:
    """Append one check/finding and update its current-state projection atomically."""
    check_id = _pkg()._new_id("cchk")
    now = _pkg()._now()
    evidence_json = _pkg()._dumps(evidence or {})
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await conn.execute(
                """INSERT INTO conformance_checks(
                       id, instance_id, run_id, contract_id, version, severity,
                       passed, summary, evidence, duration_ms, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    check_id, instance_id, run_id, contract_id, version,
                    severity, int(bool(passed)), summary, evidence_json,
                    int(duration_ms), now,
                ),
            )
            if not passed:
                await conn.execute(
                    """INSERT INTO conformance_findings(
                           id, instance_id, run_id, check_id, contract_id,
                           severity, summary, evidence, created_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _pkg()._new_id("finding"), instance_id, run_id, check_id,
                        contract_id, severity, summary, evidence_json, now,
                    ),
                )
            await conn.execute(
                """INSERT INTO conformance_state(
                       instance_id, contract_id, contract_version, last_run_id,
                       passed, severity, summary, evidence, checked_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(instance_id, contract_id) DO UPDATE SET
                       contract_version=excluded.contract_version,
                       last_run_id=excluded.last_run_id,
                       passed=excluded.passed,
                       severity=excluded.severity,
                       summary=excluded.summary,
                       evidence=excluded.evidence,
                       checked_at=excluded.checked_at""",
                (
                    instance_id, contract_id, version, run_id, int(bool(passed)),
                    severity, summary, evidence_json, now,
                ),
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
    return check_id


async def finish_conformance_run(
    instance_id: str,
    run_id: str,
    *,
    status: str,
    checks_total: int,
    checks_passed: int,
    findings_warn: int,
    findings_critical: int,
    attestation_expires_at: Optional[str],
) -> bool:
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        cur = await conn.execute(
            """UPDATE conformance_runs SET
                   status=?, checks_total=?, checks_passed=?, findings_warn=?,
                   findings_critical=?, finished_at=?,
                   attestation_expires_at=?
               WHERE instance_id=? AND id=? AND status='running'""",
            (
                status, checks_total, checks_passed, findings_warn,
                findings_critical, _pkg()._now(), attestation_expires_at,
                instance_id, run_id,
            ),
        )
        await conn.commit()
    return cur.rowcount == 1


async def latest_conformance_run(instance_id: str) -> Optional[dict]:
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM conformance_runs
               WHERE instance_id=? AND status!='running'
               ORDER BY started_at DESC, id DESC LIMIT 1""",
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row is not None else None


async def list_conformance_checks(
    instance_id: str, *, run_id: Optional[str] = None, limit: int = 200
) -> list[dict]:
    sql = "SELECT * FROM conformance_checks WHERE instance_id=?"
    args: list[Any] = [instance_id]
    if run_id:
        sql += " AND run_id=?"
        args.append(run_id)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 1000)))
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["evidence"] = _pkg()._loads(row.get("evidence"), {})
        row["passed"] = bool(row.get("passed"))
    return rows


async def list_conformance_findings(
    instance_id: str, *, limit: int = 200
) -> list[dict]:
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM conformance_findings WHERE instance_id=?
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (instance_id, max(1, min(int(limit), 1000))),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["evidence"] = _pkg()._loads(row.get("evidence"), {})
    return rows


async def get_conformance_state(instance_id: str) -> list[dict]:
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM conformance_state WHERE instance_id=?
               ORDER BY contract_id""",
            (instance_id,),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["evidence"] = _pkg()._loads(row.get("evidence"), {})
        row["passed"] = bool(row.get("passed"))
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
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
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
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        if existing is None:
            tool_id = _pkg()._new_id("tool")
            await db.execute(
                """INSERT INTO tools(
                       id, instance_id, nombre, dominio, repo_url, estado,
                       metrica_baseline, metrica_actual, usos, fallos, creado_en)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)""",
                (
                    tool_id, instance_id, nombre, dominio, repo_url, estado,
                    metrica_baseline, metrica_actual, _pkg()._now(),
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
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def bump_tool_usage(instance_id: str, nombre: str, *, ok: bool) -> None:
    """Record one production use of a tool: +1 ``usos`` and +1 ``fallos`` if not ``ok``.

    This is the signal gate 4 (Retiro) reads: a graduated tool whose production
    failure-rate crosses a threshold (or that goes unused) is demoted back to
    cuarentena. Scoped to ``instance_id``; a no-op if the tool does not exist.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
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


