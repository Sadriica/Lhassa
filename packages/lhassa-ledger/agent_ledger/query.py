# SPDX-License-Identifier: AGPL-3.0-or-later
"""The questions the ledger exists to answer.

Every aggregate here reports what it does not know alongside what it does.
A daily total that silently drops the calls with an unknown price is a lie
by omission, so `unpriced` rides along with every sum.

`unpriced` counts only metered calls whose price could not be established.
An event that never had a cost (a tool call, a note) is not a blind spot,
and counting it as one would make every report look untrustworthy the
moment an agent picked up a hammer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .store import Store


@dataclass(frozen=True)
class Spend:
    """A slice of spend, with the size of its own blind spot attached."""

    label: str
    events: int
    tokens: int
    cost_usd: float
    unpriced: int          # events whose cost could not be established

    @property
    def trustworthy(self) -> bool:
        return self.unpriced == 0


def _window(days: int) -> float:
    return time.time() - days * 86400


_SPEND_SELECT = """
    SELECT {label} AS label,
           COUNT(*) AS events,
           SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) AS tokens,
           COALESCE(SUM(cost_usd), 0.0) AS cost,
           SUM(CASE WHEN cost_usd IS NULL AND kind = 'llm_call' THEN 1 ELSE 0 END) AS unpriced
    FROM events
    WHERE ts >= ? {extra}
    GROUP BY label
    ORDER BY cost DESC, events DESC
"""


def _spend_by(
    store: Store,
    column: str,
    days: int,
    host: Optional[str],
    kind: Optional[str] = None,
) -> list[Spend]:
    clauses, params = [], [_window(days)]
    if host:
        clauses.append("AND host = ?")
        params.append(host)
    if kind:
        # A breakdown by model has nothing to say about tool calls, and a
        # breakdown by tool has nothing to say about model calls. Without
        # this both tables grow a meaningless "(none)" row that is larger
        # than every real one.
        clauses.append("AND kind = ?")
        params.append(kind)
    rows = store.query(
        _SPEND_SELECT.format(label=column, extra=" ".join(clauses)), params
    )
    return [
        Spend(
            label=str(r["label"] or "(none)"),
            events=r["events"],
            tokens=r["tokens"] or 0,
            cost_usd=round(r["cost"] or 0.0, 6),
            unpriced=r["unpriced"] or 0,
        )
        for r in rows
    ]


def by_host(store: Store, days: int = 1) -> list[Spend]:
    return _spend_by(store, "host", days, None)


def by_model(store: Store, days: int = 1, host: Optional[str] = None) -> list[Spend]:
    return _spend_by(store, "model", days, host, kind="llm_call")


def by_day(store: Store, days: int = 7, host: Optional[str] = None) -> list[Spend]:
    # Days read as a timeline, not a leaderboard: newest first, always.
    rows = _spend_by(store, "date(ts, 'unixepoch', 'localtime')", days, host)
    return sorted(rows, key=lambda s: s.label, reverse=True)


def by_tool(store: Store, days: int = 1, host: Optional[str] = None) -> list[Spend]:
    return _spend_by(store, "tool", days, host, kind="tool_call")


def pending_decisions(store: Store, days: int = 30) -> list[dict]:
    """Actions still waiting on a human.

    This is the number that must never grow quietly: an approval queue nobody
    empties is an autonomy story nobody is actually supervising.
    """
    cols = "id, ts, host, agent, tool, target, machine_id, instance_id"
    base = f"SELECT {cols} FROM events WHERE outcome = 'pending' AND ts >= ?"
    try:
        # A pending event is answered by a later event whose detail names it
        # (`resolution_event`); history is never rewritten, so the answer is
        # read as an exclusion, not an update.
        rows = store.query(
            base + " AND id NOT IN (SELECT json_extract(detail, '$.resolves') FROM events "
            "WHERE json_extract(detail, '$.resolves') IS NOT NULL) ORDER BY ts ASC",
            (_window(days),),
        )
    except Exception:  # a SQLite built without JSON1: list every pending row
        rows = store.query(base + " ORDER BY ts ASC", (_window(days),))
    return [dict(r) for r in rows]


def by_machine(store: Store, days: int = 1, host: Optional[str] = None) -> list[Spend]:
    """Spend per machine. Events that never named one are left out, so the
    table is empty until a host starts reporting `machine_id`."""
    rows = _spend_by(store, "machine_id", days, host)
    return [r for r in rows if r.label != "(none)"]


def totals(store: Store, days: int = 1) -> Spend:
    rows = store.query(
        "SELECT COUNT(*) AS events, "
        "SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) AS tokens, "
        "COALESCE(SUM(cost_usd), 0.0) AS cost, "
        "SUM(CASE WHEN cost_usd IS NULL AND kind = 'llm_call' THEN 1 ELSE 0 END) AS unpriced "
        "FROM events WHERE ts >= ?",
        (_window(days),),
    )
    r = rows[0]
    return Spend(
        label=f"last {days}d",
        events=r["events"] or 0,
        tokens=r["tokens"] or 0,
        cost_usd=round(r["cost"] or 0.0, 6),
        unpriced=r["unpriced"] or 0,
    )
