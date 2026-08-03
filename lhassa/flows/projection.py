# SPDX-License-Identifier: AGPL-3.0-or-later
"""Best-effort analytics projection for operational flow records."""

from loguru import logger

import lhassa.config as lhassa_config
from lhassa.core import db


def trigger_from_inputs(inputs: dict | None) -> str:
    if isinstance(inputs, dict) and inputs.get("trigger"):
        return str(inputs.get("trigger"))
    return "manual"


def _short(value, limit: int = 240) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = str(value.get("summary") or value.get("output") or value)
    else:
        text = str(value)
    return " ".join(text.split())[:limit]


async def project_flow_run(
    record: dict,
    *,
    trigger: str = "manual",
    instance_id: str | None = None,
) -> None:
    """Replace the read-optimized run/step projection from one run record."""
    if not isinstance(record, dict):
        return
    try:
        await db.init_db()
        instance_id = instance_id or lhassa_config.Config.instance_id
        run_id = record.get("id") or record.get("run_id")
        if not run_id:
            return
        await db.record_run(
            {
                "id": run_id,
                "instance_id": instance_id,
                "kind": "flow",
                "entity_id": record.get("flow_id"),
                "status": record.get("status") or "completed",
                "trigger": trigger,
                "summary": _short(record.get("output")),
                "started_at": record.get("started_at"),
                "finished_at": record.get("finished_at"),
                "duration_ms": record.get("duration_ms") or 0,
                "cost_usd": record.get("cost_usd") or 0.0,
                "raw": record,
            }
        )
        for idx, step in enumerate(record.get("steps") or []):
            if not isinstance(step, dict):
                continue
            kind = step.get("kind") or ""
            entity_id = step.get("brain") if kind == "task" else step.get("node")
            result = step.get("result")
            await db.record_run_step(
                {
                    "id": f"{run_id}:step:{idx}",
                    "instance_id": instance_id,
                    "run_id": run_id,
                    "idx": idx,
                    "kind": kind,
                    "entity_id": entity_id,
                    "status": step.get("status")
                    or ("completed" if step.get("ok", True) else "failed"),
                    "started_at": step.get("started_at"),
                    "finished_at": step.get("finished_at"),
                    "duration_ms": step.get("duration_ms") or 0,
                    "cost_usd": step.get("cost_usd")
                    or (result.get("cost_usd") if isinstance(result, dict) else 0.0),
                    "input_summary": _short(step.get("inputs") or step.get("goal")),
                    "output_summary": _short(
                        result or step.get("output") or step.get("payload")
                    ),
                    "error": step.get("error"),
                    "raw": step,
                }
            )
    except Exception as exc:
        logger.debug("flows: analytics projection skipped: {}", exc)


__all__ = ["project_flow_run", "trigger_from_inputs"]
