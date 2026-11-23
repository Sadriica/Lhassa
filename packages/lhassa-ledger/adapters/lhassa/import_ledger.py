# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backfill the ledger from Lhassa's own gateway history.

Lhassa has been recording every metered call since June 2026 in
``~/.lhassa/llm_gateway.db``. Importing it gives the ledger a real second
host on day one, and it is the cheapest possible proof that the cross-host
question ("what did all my agents spend") is answerable at all.

The import is idempotent: each row is keyed by its gateway row id, so
running it twice adds nothing.

    python -m adapters.lhassa.import_ledger [--db ~/.lhassa/llm_gateway.db]
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from agent_ledger.event import COST_FROM_HOST, KIND_LLM_CALL, AgentEvent
from agent_ledger.store import Store

HOST = "lhassa"
DEFAULT_GATEWAY_DB = Path.home() / ".lhassa" / "llm_gateway.db"


def _epoch(stamp: str) -> float:
    # The gateway writes ISO timestamps; anything unparseable is skipped
    # rather than silently placed at the epoch, which would corrupt every
    # time window in the reports.
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def import_gateway(gateway_db: Path, store: Store) -> tuple[int, int]:
    """Returns (imported, skipped)."""
    conn = sqlite3.connect(f"file:{gateway_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    imported = skipped = 0
    for row in conn.execute(
        "SELECT id, ts, provider, model, input_tokens, output_tokens, cost_usd, cache_hit "
        "FROM ledger ORDER BY id"
    ):
        ts = _epoch(row["ts"])
        if not ts:
            skipped += 1
            continue
        ok = store.record(
            AgentEvent(
                id=f"{HOST}:gw:{row['id']}",
                ts=ts,
                host=HOST,
                kind=KIND_LLM_CALL,
                agent="brain",
                provider=row["provider"] or "",
                model=row["model"] or "",
                input_tokens=row["input_tokens"] or 0,
                output_tokens=row["output_tokens"] or 0,
                cost_usd=float(row["cost_usd"] or 0.0),
                cost_source=COST_FROM_HOST,
                detail={"cache_hit": bool(row["cache_hit"])},
            )
        )
        imported += ok
        skipped += not ok
    conn.close()
    return imported, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_GATEWAY_DB)
    parser.add_argument("--ledger", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"no gateway database at {args.db}")
        return 1
    imported, skipped = import_gateway(args.db, Store(args.ledger))
    print(f"imported {imported} events from {args.db} ({skipped} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
