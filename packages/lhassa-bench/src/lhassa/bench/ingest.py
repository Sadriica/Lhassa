# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ingest blackbox experiment results (``research/results/*.jsonl``) into ``bench_runs``.

The blackbox research (``research/``) ran its experiments as standalone scripts that
each wrote one JSONL file. Those rows never reached ``bench_runs``, so nothing that
reads the bench (the research note gatherer, the router, the future model benches)
could cite them. This module maps them in, without rewriting the scripts:

* ``fixture_id`` is ``blackbox:<phase>:<task>`` (task = ``task``, ``task_id``,
  ``probe`` or ``scale``, whichever the row carries).
* ``subject`` is ``blackbox/<phase>/<harness or arm>``.
* ``metric`` is ``tokens_in + tokens_out`` (cache reads excluded; lower is better).
* ``duration_ms`` is ``latency_ms``; ``cost_usd`` stays 0 (the scripts never priced runs).
* ``detail`` is a compact JSON object with the token split, the seed and every extra
  field the row had, plus ``audit``: ``pre-audit`` for the files the research's own
  audit later invalidated (``fase3_<date>``, ``gate_<date>``), ``post-audit`` otherwise.
  Pre-audit rows are kept because they document what was measured, but anything that
  decides from them must filter on that flag.
* ids are a hash of file name, line number and the line itself, so ingesting the same
  file twice inserts nothing new.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

_PRE_AUDIT = re.compile(r"^(fase3|gate)_\d{8}$")
_TASK_KEYS = ("task", "task_id", "probe", "scale")
_SUBJECT_KEYS = ("harness", "arm")
_KNOWN = {"passed", "tokens_in", "tokens_out", "tokens_cache", "latency_ms", "detail"}


def phase_of(path: Path) -> str:
    """The phase label of a results file: its stem without date or ``corrected`` marks."""
    stem = path.stem
    stem = re.sub(r"_(corrected|\d{8})", "", stem)
    return stem


def audit_flag(path: Path) -> str:
    return "pre-audit" if _PRE_AUDIT.match(path.stem) else "post-audit"


def _row(path: Path, lineno: int, raw: str, obj: dict, instance_id: str, created_at: str) -> dict:
    phase = phase_of(path)
    task = next((str(obj[k]) for k in _TASK_KEYS if obj.get(k) not in (None, "")), "all")
    who = next((str(obj[k]) for k in _SUBJECT_KEYS if obj.get(k) not in (None, "")), "unknown")
    tin = int(obj.get("tokens_in") or 0)
    tout = int(obj.get("tokens_out") or 0)
    detail = {
        "audit": audit_flag(path),
        "source": path.name,
        "tokens_in": tin,
        "tokens_out": tout,
        "tokens_cache": int(obj.get("tokens_cache") or 0),
    }
    for key, value in obj.items():
        if key not in _KNOWN and key not in _TASK_KEYS and key not in _SUBJECT_KEYS:
            detail[key] = value
    note = str(obj.get("detail") or "").strip()
    if note:
        detail["note"] = note[:80]
    digest = hashlib.sha256(f"{path.name}:{lineno}:{raw}".encode()).hexdigest()[:24]
    return {
        "id": f"bb_{digest}",
        "instance_id": instance_id,
        "fixture_id": f"blackbox:{phase}:{task}",
        "subject": f"blackbox/{phase}/{who}",
        "passed": bool(obj.get("passed")),
        "metric": float(tin + tout),
        "detail": json.dumps(detail, sort_keys=True, separators=(",", ":"), default=str),
        "cost_usd": 0.0,
        "duration_ms": int(obj["latency_ms"]) if obj.get("latency_ms") is not None else None,
        "created_at": created_at,
    }


def rows_from_jsonl(
    path: str | Path, instance_id: str, *, created_at: Optional[str] = None
) -> tuple[list[dict], int]:
    """Map one results file to ``bench_runs`` rows: ``(rows, skipped_lines)``.

    A line that is not a JSON object is skipped and counted, never raised.
    """
    p = Path(path)
    stamp = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    skipped = 0
    for lineno, raw in enumerate(p.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(obj, dict):
            skipped += 1
            continue
        rows.append(_row(p, lineno, raw, obj, instance_id, stamp))
    return rows, skipped


async def ingest_files(
    paths: Iterable[str | Path],
    instance_id: str,
    *,
    insert: Optional[Callable] = None,
    created_at: Optional[str] = None,
) -> dict:
    """Insert every row of ``paths`` into ``bench_runs``; return counts.

    ``{"files": n, "rows": n, "inserted": n, "duplicates": n, "skipped": n}``. Re-running
    over the same files inserts nothing (the ids are content-derived).
    """
    import sqlite3

    if insert is None:
        from lhassa.core.db import insert_bench_run as insert  # type: ignore[assignment]

    totals = {"files": 0, "rows": 0, "inserted": 0, "duplicates": 0, "skipped": 0}
    for path in paths:
        rows, skipped = rows_from_jsonl(path, instance_id, created_at=created_at)
        totals["files"] += 1
        totals["skipped"] += skipped
        for row in rows:
            totals["rows"] += 1
            try:
                await insert(row)
                totals["inserted"] += 1
            except sqlite3.IntegrityError:
                totals["duplicates"] += 1
    return totals
