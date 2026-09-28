# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 5; improving the relay: enriched destilado (decisions+diff), Pareto.

Addresses the relay's measured defect from Fase 2/3: the recorder destilado
carries "see the task description"; i.e. NO actual decisions. Fase 0.2
measured ~35-50% of a transcript is non-re-derivable (decisions, reasoning,
context); the current destilado ignores it, which is the whole point of the
relay. Two improvements, measured:

  A+B; enriched destilado: extract from the phase-1 transcript the text /
  thinking (decisions, reasoning, discarded paths) AND the git diff of what
  phase-1 changed, and carry those instead of an empty state summary.
  Compare against the current (poor) destilado on a multi-step decision task
  where reasoning actually matters.

  D; Pareto curve: the same destilado truncated to 200/500/1000/2000 tokens
  to find the smallest state that still completes; the relay's standing
  cold-start floor.

Uses a decision task (a subtle logic bug the phase-1 diagnoses but does not
fix) so the non-rederivable reasoning is real, not a trivial append.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fase2_bench import Task, _run_opencode, _make_worktree, _verify
from fase2_bench import _usage_from_opencode, recorder_destilado

# ---- the decision task --------------------------------------------------------

def decision_task() -> Task:
    """A bug that requires reasoning to find and fix.

    ``merge_sorted`` is supposed to merge two sorted lists but has a subtle
    off-by-one/wrong-pointer bug that only fails on specific inputs. Phase-1
    reads the code, diagnoses the bug, and leaves the fix UNDONE; a real
    decision point where the reasoning must cross the switch.
    """
    code = (
        "def merge_sorted(a, b):\n"
        "    i = j = 0\n"
        "    out = []\n"
        "    while i < len(a) and j < len(b):\n"
        "        if a[i] <= b[j]:\n"
        "            out.append(a[i])\n"
        "            i += 1\n"
        "        else:\n"
        "            out.append(b[j])\n"
        "            j += 1\n"
        "    out.extend(a[i:])\n"
        "    out.extend(b[i:])  # BUG: should be b[j:]\n"
        "    return out\n"
    )
    test = (
        "from merge_sorted import merge_sorted\n"
        "assert merge_sorted([1,3,5],[2,4,6]) == [1,2,3,4,5,6]\n"
        "assert merge_sorted([],[1,2]) == [1,2]\n"
        "assert merge_sorted([1,1,2],[1,3]) == [1,1,1,2,3]\n"
        "print('OK')\n"
    )
    verify = (
        "python3 - <<'PY'\n"
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from merge_sorted import merge_sorted\n"
        "ok = (merge_sorted([1,3,5],[2,4,6]) == [1,2,3,4,5,6]\n"
        "      and merge_sorted([],[1,2]) == [1,2]\n"
        "      and merge_sorted([1,1,2],[1,3]) == [1,1,1,2,3])\n"
        "sys.exit(0 if ok else 1)\n"
        "PY"
    )
    return Task(
        id="decision",
        prompt=("The function merge_sorted in merge_sorted.py is buggy: it "
                "merges two sorted lists but produces wrong output on some "
                "inputs. Find the bug and fix it. Then verify with the test "
                "below:\n" + verify),
        verify_cmd=verify,
        files={"merge_sorted.py": code, "test_merge_sorted.py": test},
    )


def decision_task_external() -> Task:
    """A task where the KEY decision is NOT re-derivable from the code.

    Same buggy merge_sorted, but the correct fix is NOT to make it generic:
    the caller only ever merges lists that are ALREADY sorted descending, so
    the correct fix is to reverse the input/output to preserve descending
    order. This decision is a product requirement that lives OUTSIDE the repo
   ; no amount of code-reading can derive it. It is the non-rederivable
    context (Fase 0.2's ~35-50%) made concrete: only a handoff that carries it
    can complete the task correctly.
    """
    code = (
        "def merge_sorted(a, b):\n"
        "    i = j = 0\n"
        "    out = []\n"
        "    while i < len(a) and j < len(b):\n"
        "        if a[i] <= b[j]:\n"
        "            out.append(a[i])\n"
        "            i += 1\n"
        "        else:\n"
        "            out.append(b[j])\n"
        "            j += 1\n"
        "    out.extend(a[i:])\n"
        "    out.extend(b[j:])\n"
        "    return out\n"
    )
    # Verify DESCENDING merge: the correct behavior per the external decision.
    verify = (
        "python3 - <<'PY'\n"
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from merge_sorted import merge_sorted\n"
        "ok = (merge_sorted([5,3,1],[6,4,2]) == [6,5,4,3,2,1]\n"
        "      and merge_sorted([],[2,1]) == [2,1])\n"
        "sys.exit(0 if ok else 1)\n"
        "PY"
    )
    return Task(
        id="decision_ext",
        prompt=("The function merge_sorted in merge_sorted.py is buggy: it "
                "produces wrong output on some inputs. Fix it. Then verify "
                "with:\n" + verify),
        verify_cmd=verify,
        files={"merge_sorted.py": code},
    )


# ---- enriched destilado (A+B) -------------------------------------------------

def _extract_decisions(transcript: str, limit: int = 1500) -> str:
    """Pull the non-rederivable parts out of a phase-1 opencode transcript:
    the assistant's text and reasoning (thinking), which carry decisions.
    tool_result/tool output is dropped (re-derivable from disk).
    """
    parts = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") not in ("text", "thinking", "reasoning"):
            continue
        p = obj.get("part") or {}
        txt = p.get("text") or ""
        if txt.strip():
            parts.append(txt.strip())
    return "\n".join(parts)[:limit]


def _git_diff(cwd: Path, limit: int = 1200) -> str:
    """Diff of what the phase-1 session changed on disk (re-derivable, carried
    as a pointer to make checkout the resume path)."""
    try:
        r = subprocess.run(["git", "diff", "--stat"], cwd=str(cwd),
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return "(no baseline commit)"
        return r.stdout.strip()[:limit]
    except Exception:
        return "(git diff unavailable)"


def enriched_destilado(task: Task, phase_transcript: str, phase_cwd: Path,
                       budget: Optional[int] = None) -> str:
    """The relay handoff that actually carries decisions + diff.

    Different from recorder_destilado: instead of "see the task description",
    it extracts the phase-1's reasoning/decisions (the non-rederivable 35-50%
    from Fase 0.2) and the diff of what changed on disk. ``budget`` truncates
    the whole destilado (the Pareto probe D).
    """
    decisions = _extract_decisions(phase_transcript)
    diff = _git_diff(phase_cwd)
    lines = [
        "RELAY HANDOFF (enriched). The task deliverable is NOT done; continue "
        "working toward it now.",
        f"  task: {task.id}",
        "  what the previous agent CHANGED on disk (git diff --stat):",
        f"    {diff}",
        "  decisions/reasoning the previous agent left (non-derivable, carry "
        "these; do not re-derive them):",
        decisions or "    (none captured)",
        "  task description (the goal):",
        f"    {task.prompt[:400]}",
    ]
    out = "\n".join(lines)
    if budget:
        out = out[:budget]
    return out


# ---- arms ---------------------------------------------------------------------

@dataclass
class EnrichRow:
    variant: str       # poor | rich | pareto200 ...
    task_id: str
    seed: int
    passed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache: int = 0
    latency_ms: int = 0
    detail: str = ""


def emit_row(r: EnrichRow, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(r.__dict__) + "\n")


async def run_variant(task: Task, seed: int, destilado: str,
                      results: Path) -> EnrichRow:
    cwd = _make_worktree(task)
    prompt = task.prompt + "\n\n===== relay destilado ===\n" + destilado
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    return EnrichRow("x", task.id, seed, passed, u.input_tokens,
                     u.output_tokens, u.cache_read_tokens, ms,
                     det if ok else f"opencode failed: {out[:120]}")


def summarize(rows: list[EnrichRow]) -> str:
    lines = [
        f"{'variant':<14}{'pass':>5}{'in':>8}{'cache':>9}{'cold':>9}{'lat_s':>7}",
        "-" * 54,
    ]
    for r in sorted(rows, key=lambda x: (x.variant, x.seed)):
        cold = r.tokens_in + r.tokens_cache
        lat = r.latency_ms / 1000.0 if r.latency_ms else 0.0
        lines.append(f"{r.variant:<14}{str(r.passed):>5}{r.tokens_in:>8}"
                     f"{r.tokens_cache:>9}{cold:>9}{lat:>7.1f}")
    lines.append("-" * 54)
    for v in sorted({r.variant for r in rows}):
        rs = [r for r in rows if r.variant == v]
        mp = sum(1 for r in rs if r.passed)
        mc = sum(r.tokens_in + r.tokens_cache for r in rs) / len(rs)
        lines.append(f"{v:<14}{f'{mp}/{len(rs)}':>5}{'-':>8}{'-':>9}"
                     f"{mc:>9.0f}{'-':>7}")
    return "\n".join(lines)


async def main_async(args) -> int:
    results = Path(args.results)
    rows = []
    for seed in range(args.seeds):
        # Use the EXTERNAL-decision task: the fix decision is NOT derivable
        # from the repo; the phase-1 receives it as context and the destilado
        # is the ONLY way it crosses the switch.
        task = decision_task_external()
        cwd = _make_worktree(task)
        setup = ("Read merge_sorted.py. A PRODUCT REQUIREMENT: this function "
                 "only ever receives lists that are ALREADY sorted in "
                 "DESCENDING order, and must return a DESCENDING merge. "
                 "Diagnose what fix is needed to satisfy that requirement "
                 "(you may fix it). Print DIAGNOSIS DONE when done.")
        ok, out, ms = _run_opencode(setup, cwd=cwd)
        # (a) poor destilado: does NOT carry the external requirement.
        if "poor" in args.variants:
            r = await run_variant(task, seed, recorder_destilado(task), results)
            r.variant = "poor"
            rows.append(r)
            emit_row(r, results)
        # (b) rich destilado: carries the external decision extracted from the
        # phase-1 transcript (the requirement + the diagnosis).
        if "rich" in args.variants:
            r = await run_variant(task, seed,
                                  enriched_destilado(task, out, cwd), results)
            r.variant = "rich"
            rows.append(r)
            emit_row(r, results)
        # (d) Pareto truncations of the rich destilado
        rich = enriched_destilado(task, out, cwd)
        for b in args.pareto:
            r = await run_variant(task, seed, rich[:b], results)
            r.variant = f"par{b}"
            rows.append(r)
            emit_row(r, results)
    print(summarize(rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variants", nargs="*", default=["poor", "rich"],
                    choices=["poor", "rich"])
    ap.add_argument("--pareto", nargs="*", type=int, default=[],
                    help="truncate rich destilado to these token budgets")
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--results", type=Path,
                    default=Path("app/research/results/fase5_enrich.jsonl"))
    args = ap.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
