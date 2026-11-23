# SPDX-License-Identifier: AGPL-3.0-or-later
"""`ledger` - read the ledger from a terminal.

Deliberately small: report, tail, pending, json. Anything that needs a model to
answer does not belong in a tool whose whole point is costing nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import query
from .store import Store


def _fmt_cost(spend: query.Spend) -> str:
    tag = "" if spend.trustworthy else f"  ({spend.unpriced} unpriced)"
    return f"${spend.cost_usd:.4f}{tag}"


def _print_table(title: str, rows: list[query.Spend]) -> None:
    print(f"\n{title}")
    if not rows:
        print("  (nothing recorded)")
        return
    width = max(len(r.label) for r in rows)
    for r in rows:
        print(f"  {r.label:<{width}}  {r.events:>6} ev  {r.tokens:>10} tok  {_fmt_cost(r)}")


def _where(machine_id: str, instance_id: str) -> str:
    """`machine/instance`, `machine` or `/instance`; empty when neither is set."""
    if machine_id and instance_id:
        return f"{machine_id}/{instance_id}"
    return machine_id or (f"/{instance_id}" if instance_id else "")


def cmd_report(args: argparse.Namespace) -> int:
    store = Store(args.db)
    total = query.totals(store, args.days)
    print(f"agent-ledger  last {args.days}d  {total.events} events  "
          f"{total.tokens} tokens  {_fmt_cost(total)}")
    if not total.trustworthy:
        print(f"  note: {total.unpriced} events have no known price; the total is a floor, not a bill.")
    _print_table("by host", query.by_host(store, args.days))
    machines = query.by_machine(store, args.days, args.host)
    if machines:
        _print_table("by machine", machines)
    _print_table("by model", query.by_model(store, args.days, args.host))
    _print_table("by day", query.by_day(store, max(args.days, 7), args.host))
    if args.tools:
        _print_table("by tool", query.by_tool(store, args.days, args.host))
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    store = Store(args.db)
    for event in reversed(list(store.events(limit=args.limit, host=args.host))):
        stamp = time.strftime("%m-%d %H:%M", time.localtime(event.ts))
        what = event.model or event.tool or event.kind
        cost = f"${event.cost_usd:.5f}" if event.cost_usd is not None else "   -   "
        where = _where(event.machine_id, event.instance_id)
        print(f"{stamp}  {event.host:<12} {what:<28} {cost}  {event.outcome}"
              + (f"  [{where}]" if where else ""))
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    store = Store(args.db)
    rows = query.pending_decisions(store, args.days)
    if not rows:
        print("nothing is waiting on you.")
        return 0
    print(f"{len(rows)} actions waiting on a human:")
    for row in rows:
        age_h = (time.time() - row["ts"]) / 3600
        where = _where(row.get("machine_id", ""), row.get("instance_id", ""))
        print(f"  {row['id'][:12]}  {row['host']:<10} {row['tool'] or '(action)':<20} "
              f"{row['target'][:40]:<40} {age_h:.0f}h old" + (f"  [{where}]" if where else ""))
    return 1  # non-zero: a pending queue is a state a script should notice


def cmd_json(args: argparse.Namespace) -> int:
    store = Store(args.db)
    json.dump([e.as_dict() for e in store.events(limit=args.limit, host=args.host)],
              sys.stdout, indent=2, default=str)
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger", description="Read the agent ledger.")
    parser.add_argument("--db", default=None, help="ledger path (default ~/.agent-ledger/ledger.db)")
    parser.add_argument("--host", default=None, help="filter to one host")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("report", help="spend by host, model and day")
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--tools", action="store_true", help="also break down by tool")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("tail", help="the most recent events")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_tail)

    p = sub.add_parser(
        "pending",
        help="actions waiting on a human (outcome=pending, see pending_event)",
    )
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=cmd_pending)

    p = sub.add_parser("json", help="dump events as JSON")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_json)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
