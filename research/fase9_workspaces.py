# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 9; the `workspaces`/`domain` bucket: Lhassa's real use case.

Fase 0.2 measured re-derivability per task type: `workspaces` and
`domain/venta` sit at ~47%; i.e. ~53% of the transcript is NOT re-derivable
from disk, the worst case for a relay and the realistic Lhassa workload. All
prior phases benched synthetic single-purpose coding tasks (hello/fib/merge/
service) where the non-derivable fraction was a single line of hidden spec.

This probe makes the non-derivable content RICH and DOMAIN-LIKE: a business
ruleset of ~15 rules that phase-1 must absorb, partially apply, and reason
about; the kind of content that dominates real transcripts. The grader tests
five behaviors scattered across the ruleset, so the relay must carry the
actual decisions (not just one), and pointer-only has no way to know them.

Arms (same instrument pattern as Fase 8, reusing the live recorder):
  (a) re-send ; full phase-1 transcript (the ruleset is in it)
  (c) poor    ; pointer-only recorder destilado (files list, no rules)
  (e) recorder; LIVE event recorder destilado (carries phase-1's stated rules)

The Fase-0.2-bucket question: on a task whose non-derivable fraction is ~half
and is domain-text (not one hidden line), does the carrying relay still
complete (e 3/3), and does pointer-only collapse (c < 3/3)? This is the
"will it work on Lhassa's real tasks" probe.

Anti-leak: ruleset lives ONLY in phase-1's setup prompt and never touches disk;
grader is a hidden python snippet; task.prompt never mentions the rules.
shell=True discipline; abort-safe JSONL. Degrades, never raises.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fase2_bench import Task, _run_opencode, _make_worktree, _verify
from fase2_bench import _usage_from_opencode, recorder_destilado
from fase8_recorder import run_opencode_recorded, Recorder, RunEvent


# ---- the workspace/domain ruleset --------------------------------------------

RULES = [
    "R1  Orders with priority 'urgent' ship the same day; all others ship on the next business day.",
    "R2  A customer is 'gold' if they have >=5 orders in the last 60 days OR a lifetime spend >= $10k.",
    "R3  Gold customers skip the standard credit check; silver (>=2 orders / 30d) get a soft check only.",
    "R4  Free shipping applies to orders over $75, or any order for a gold customer.",
    "R5  Orders with a single item under $10 must be reviewed by a human before shipping.",
    "R6  The shipping method is 'express' when total weight > 2kg OR the customer chose it at checkout;",
    "R7  ...and 'economy' otherwise. Express never ships to PO boxes.",
    "R8  Discounts: 5% off for silver, 10% off for gold, 15% off for 'urgent' + gold (stacked, applied in that order).",
    "R9  A refund is full if the item is defective or unused within 30 days; otherwise 50%.",
    "R10 Loyalty points: 1 point per $1 on the discounted total, rounded down, credited on ship date.",
    "R11 A cancelled order keeps its discount only if cancelled within 4 hours of placement.",
    "R12 Backorders: an order is backordered when any line's requested quantity exceeds stock;",
    "R13 ...and a backordered order ships in full only when ALL lines are available (no partial).",
    "R14 Gifting: a 'gift' order wraps items individually and never includes the price on the slip.",
    "R15 The business day calendar is Mon-Fri; a Friday 'urgent' order ships Monday, not Saturday.",
]

RULES_PROMPT = "\n".join(RULES)


def workspace_task() -> Task:
    """The Fase-0.2-`workspaces`-style task: absorb a 15-rule domain ruleset.

    phase-1 receives the ruleset via its setup prompt (the non-derivable ~53%),
    states a summary of the five rules the grader cares about, and writes a
    PARTIAL engine (a stub that imports but does not implement). The grader
    tests five behaviors drawn from across R1-R15, so carrying a single rule is
    not enough.
    """
    verify = (
        "python3 - <<'PY'\n"
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from engine import (ship_day, is_gold, shipping, discount, points)\n"
        "ok = True\n"
        "# R1/R15: urgent same-day, Friday urgent ships Monday\n"
        "ok &= ship_day('urgent', 'Friday') == 'Monday'\n"
        "ok &= ship_day('standard', 'Friday') == 'Monday'\n"
        "ok &= ship_day('urgent', 'Tuesday') == 'Tuesday'\n"
        "# R2/R3: gold needs >=5 orders / 60d OR >10k spend; gold skips full check\n"
        "ok &= is_gold(orders_60d=5, spend=100) is True\n"
        "ok &= is_gold(orders_60d=4, spend=10000) is True\n"
        "ok &= is_gold(orders_60d=4, spend=9999) is False\n"
        "# R4: free shipping over $75\n"
        "ok &= shipping(price=76, gold=False) == 'free'\n"
        "ok &= shipping(price=74, gold=False) == 'paid'\n"
        "# R8: discounts stacked urgent+gold = 15% then 10% on remainder\n"
        "ok &= abs(discount(urgent=True, gold=True, price=100) - 100*0.9*0.85) < 0.01\n"
        "# R10: points on discounted total, rounded down\n"
        "ok &= points(discounted=99.99) == 99\n"
        "sys.exit(0 if ok else 1)\n"
        "PY"
    )
    return Task(
        id="workspaces",
        prompt=("This repo has engine.py (a stub). The domain RULESET that "
                "governs ship_day/is_gold/shipping/discount/points was given to "
                "the previous agent as a relay contract; it is NOT written here "
                "and is NOT derivable from the files. Implement the five "
                "functions in engine.py to satisfy that contract exactly. Honor "
                "the decisions carried by the prior transcript / handoff below. "
                "Deliver engine.py only."),
        verify_cmd=verify,
        files={"engine.py": (
            "def ship_day(priority, day):\n    raise NotImplementedError\n\n"
            "def is_gold(orders_60d, spend):\n    raise NotImplementedError\n\n"
            "def shipping(price, gold):\n    raise NotImplementedError\n\n"
            "def discount(urgent, gold, price):\n    raise NotImplementedError\n\n"
            "def points(discounted):\n    raise NotImplementedError\n")},
    )


# ---- phase-1 + arms ----------------------------------------------------------

@dataclass
class Row:
    arm: str
    task_id: str
    seed: int
    passed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache: int = 0
    latency_ms: int = 0
    n_events: int = 0
    detail: str = ""


def emit_row(r: Row, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(r.__dict__) + "\n")


async def _phase1(task: Task) -> tuple[Path, str, str, int]:
    """phase-1 receives the ruleset, states the relevant rules in its reply
    (the non-derivable content that must cross the switch), and leaves a stub."""
    cwd = _make_worktree(task)
    setup = (
        "Read engine.py (a stub). The following DOMAIN RULESET governs the "
        "five functions and must be honored exactly:\n"
        + RULES_PROMPT
        + "\nState a concise summary of the rules for ship_day, is_gold, "
        "shipping, discount and points IN YOUR REPLY so a later agent can read "
        "them (do NOT write the rules into any file; they stay off disk). "
        "Implement engine.py PARTIALLY: a stub that imports cleanly and "
        "returns placeholder values, but do NOT complete it. Print SETUP DONE."
    )
    ok, out, evlog, ms = run_opencode_recorded(setup, cwd=cwd)
    subprocess.run(["git", "add", "-A"], cwd=str(cwd), check=False)
    subprocess.run(["git", "commit", "-qm", "phase1 stub"], cwd=str(cwd), check=False)
    n = len(json.loads(evlog)) if evlog else 0
    return cwd, (out or ""), evlog, n


async def arm_a(task: Task, transcript: str, seed: int, results: Path) -> Row:
    cwd = _make_worktree(task)
    prompt = task.prompt + "\n\n===== PRIOR SETUP + TRANSCRIPT (full) ===\n" + transcript[:40000]
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    r = Row("a", task.id, seed, passed, u.input_tokens, u.output_tokens,
            u.cache_read_tokens, ms, 0, det if ok else f"opencode failed: {out[:120]}")
    emit_row(r, results)
    return r


async def arm_c(task: Task, seed: int, results: Path) -> Row:
    cwd = _make_worktree(task)
    prompt = task.prompt + "\n\n===== recorder destilado (pointer-only) ===\n" \
        + recorder_destilado(task)
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    r = Row("c", task.id, seed, passed, u.input_tokens, u.output_tokens,
            u.cache_read_tokens, ms, 0, det if ok else f"opencode failed: {out[:120]}")
    emit_row(r, results)
    return r


async def arm_e(task: Task, evlog: str, phase_cwd: Path,
                seed: int, results: Path) -> Row:
    """recorder: LIVE recorder destilado, delivered with the phase-1 state
    (the relay real: destilado + the state it describes)."""
    tmp = Path(tempfile.mkdtemp(prefix="fase9_rec_"))
    subprocess.run(["git", "clone", "-q", str(phase_cwd), str(tmp / "repo")],
                   check=False, timeout=300)
    cwd = tmp / "repo"
    rec = Recorder()
    if evlog:
        for e in json.loads(evlog):
            rec.events.append(RunEvent(e["kind"], e["payload"], e["file"]))
    dest = rec.destilado(task, phase_cwd)
    prompt = task.prompt + "\n\n===== live-recorder destilado ===\n" + dest
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    r = Row("e", task.id, seed, passed, u.input_tokens, u.output_tokens,
            u.cache_read_tokens, ms, len(rec.events),
            det if ok else f"opencode failed: {out[:120]}")
    emit_row(r, results)
    return r


async def run_matrix(seeds: int, arms: list[str], results: Path) -> list[Row]:
    rows: list[Row] = []
    task = workspace_task()
    for seed in range(seeds):
        phase_cwd, transcript, evlog, n_ev = await _phase1(task)
        if "a" in arms:
            rows.append(await arm_a(task, transcript, seed, results))
        if "c" in arms:
            rows.append(await arm_c(task, seed, results))
        if "e" in arms:
            rows.append(await arm_e(task, evlog, phase_cwd, seed, results))
    return rows


def summarize(rows: list[Row]) -> str:
    if not rows:
        return "Fase 9: no rows."
    lines = [
        f"{'arm':<5}{'pass':>5}{'in':>8}{'out':>7}{'cache':>9}{'cold':>9}{'ev':>4}{'lat_s':>7}",
        "-" * 62,
    ]
    for r in sorted(rows, key=lambda x: (x.seed, x.arm)):
        cold = r.tokens_in + r.tokens_cache
        lat = r.latency_ms / 1000.0 if r.latency_ms else 0.0
        lines.append(f"{r.arm:<5}{str(r.passed):>5}{r.tokens_in:>8}{r.tokens_out:>7}"
                     f"{r.tokens_cache:>9}{cold:>9}{r.n_events:>4}{lat:>7.1f}")
        if r.detail and not r.passed:
            lines.append(f"     {r.detail[:150]}")
    lines.append("-" * 62)
    for arm in sorted({r.arm for r in rows}):
        rs = [r for r in rows if r.arm == arm]
        mp = sum(1 for r in rs if r.passed)
        mc = sum(r.tokens_in + r.tokens_cache for r in rs) / len(rs) if rs else 0
        lines.append(f"{arm:<5}{f'{mp}/{len(rs)}':>7}{mc:>8.0f} mean cold tokens")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms", nargs="*", default=["a", "c", "e"],
                    choices=["a", "c", "e"])
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--results", type=Path, default=Path("results/fase9_workspaces.jsonl"))
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.report is not None:
        rows = []
        for line in open(args.report, encoding="utf-8"):
            if not line.strip():
                continue
            rows.append(Row(**json.loads(line)))
        print(summarize(rows))
        return 0
    rows = asyncio.run(run_matrix(args.seeds, args.arms, args.results))
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())