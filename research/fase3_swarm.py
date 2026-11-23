# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 3B; cross-harness multi-body deployment (the relay as swarm).

Dispatches the SAME task-state (Fase 2's winning arm: the deterministic
recorder destilado) to N harnesses in parallel; claude, codex, opencode,
each in its own isolated git worktree, and measures the swarm economics:

  - pass rate per harness  (objective verify, never an LLM judge)
  - wall-clock per worker  (the parallel speedup, sequential vs. gathered)
  - tokens-to-completion with cache split (S31 rho discipline) per worker,
    per harness's own accounting:
      claude  : stream-json usage {input, cache_read, cache_creation, output}
      codex   : turn.completed usage {input, cached_input, cache_write, output}
      opencode: step_finish part.tokens {input, cache.read, output}
  - merge reproducibility: independently-passing workers are reconciled at the
    state level (git merge of the worktree deltas) and re-verified; the Fase 3C
    semantic-conflict probe, run here because it's the natural finish of 3B.

The thesis being measured: the relay's external canonical state lets a swarm of
harness-agnostic bodies run the SAME task in parallel and merge back
deterministically, without context bleeding or LLM-judged merges. This is the
"rent the body ×N while the brain stays one" claim ([S37] STORM, [S38] RAH).

Degrades, never raises: a harness that errors/times out yields a failed worker
row with the reason, so no stubborn body aborts the swarm.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fase2_bench import TASKS, Task, Usage, _verify, _make_worktree, recorder_destilado

HARNESSES = ["claude", "codex", "opencode"]


# ---- per-harness accounting --------------------------------------------------

def _usage_from_claude(stdout: str) -> Usage:
    """Extract token totals from claude -p --output-format=json stream-json.

    Anthropic's accounting: `input_tokens` is only the non-cached fresh input;
    the full prompt shows up in `cache_creation_input_tokens` on first use. To
    compare honestly across harnesses, cache_creation counts as INPUT (the
    cold prefill), and cache_read as cache hits.
    """
    u = Usage()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = obj.get("usage") or {}
        if not usage:
            continue
        u.input_tokens += int(usage.get("input_tokens") or 0)
        u.input_tokens += int(usage.get("cache_creation_input_tokens") or 0)
        u.cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
        u.output_tokens += int(usage.get("output_tokens") or 0)
    return u


def _usage_from_codex(stdout: str) -> Usage:
    """Extract token totals from codex exec --json turn.completed events."""
    u = Usage()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = obj.get("usage") or {}
        if obj.get("type") != "turn.completed" or not usage:
            continue
        u.input_tokens += int(usage.get("input_tokens") or 0)
        u.cache_read_tokens += int(usage.get("cached_input_tokens") or 0)
        u.cache_read_tokens += int(usage.get("cache_write_input_tokens") or 0)
        u.output_tokens += int(usage.get("output_tokens") or 0)
    return u


_USAGE_FNS = {
    "claude": _usage_from_claude,
    "codex": _usage_from_codex,
    "opencode": None,  # reuse fase2_bench._usage_from_opencode
}


# ---- worker invocation --------------------------------------------------------

def _run_harness(bin_name: str, prompt: str, cwd: Path) -> tuple[int, str, int]:
    """Run one harness headless in ``cwd``; return (rc, stdout, ms).

    shell=True is mandatory: measured that opencode refuses file writes when
    invoked as an argv list (the Fase 2/3 invalidator). claude additionally
    needs --dangerously-skip-permissions to write in -p mode.
    """
    import shlex
    start = time.monotonic()
    try:
        if bin_name == "opencode":
            argv = ["opencode", "run", "--format", "json", "--auto", prompt]
        elif bin_name == "claude":
            argv = ["claude", "-p", "--output-format=json",
                    "--dangerously-skip-permissions", prompt]
        elif bin_name == "codex":
            # WRITE-CAPABLE sandbox (audit fix C5): the original ran codex in
            # --sandbox read-only, so it could never write the deliverable and
            # its 12/12 "pass" was vacuous (verify passed on a file the codex
            # worktree already had from the seed). workspace-write allows file
            # edits within the repo cwd; the honest measurement.
            argv = ["codex", "exec", "--json", "--sandbox", "workspace-write",
                    "--skip-git-repo-check", prompt]
        else:
            return 1, f"[unknown harness {bin_name}]", 0
        cmd = " ".join(shlex.quote(a) for a in argv)
        code = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                              shell=True, stdin=subprocess.DEVNULL, timeout=1800)
    except subprocess.TimeoutExpired:
        return 124, f"[timeout {bin_name}]", 1800000
    ms = int((time.monotonic() - start) * 1000)
    return code.returncode, (code.stdout or b"").decode("utf-8", "replace"), ms


def _usage_for(bin_name: str, stdout: str) -> Usage:
    fn = _USAGE_FNS[bin_name]
    if fn is None:
        from fase2_bench import _usage_from_opencode
        return _usage_from_opencode(stdout)
    return fn(stdout)


# ---- worker row / merge probe --------------------------------------------------

@dataclass
class WorkerRow:
    harness: str
    task_id: str
    seed: int
    passed: bool
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache: int = 0
    latency_ms: int = 0
    detail: str = ""


@dataclass
class MergeProbe:
    """Fase 3C probe: reconcile passing workers' deltas at state level and
    re-verify; the silent semantic conflict a textual merge can miss."""

    n_passing: int = 0
    merged: bool = False
    still_passes: bool = False
    detail: str = ""


async def _worker(bin_name: str, task: Task, seed: int, cwd: Path) -> WorkerRow:
    prompt = task.prompt + "\n\n===== recorder destilado (relay task-state) ===\n" \
                           + recorder_destilado(task)
    rc, out, ms = await asyncio.to_thread(_run_harness, bin_name, prompt, cwd)
    u = _usage_for(bin_name, out)
    passed, det = _verify(task, cwd)
    detail = det if rc == 0 else f"{bin_name} rc={rc}: {out[:200]}"
    return WorkerRow(bin_name, task.id, seed, passed, u.input_tokens,
                     u.output_tokens, u.cache_read_tokens, ms, detail)


async def run_swarm(tasks: list[Task], seeds: int = 1,
                    harnesses: Optional[list[str]] = None,
                    results_path: Optional[Path] = None) -> tuple[list[WorkerRow], list[MergeProbe]]:
    harnesses = harnesses or list(HARNESSES)
    rows: list[WorkerRow] = []
    probes: list[MergeProbe] = []
    for task in tasks:
        for seed in range(seeds):
            # Parallel: one isolated worktree per harness, same task-state.
            cwds = {h: _make_worktree(task) for h in harnesses}
            outcomes = await asyncio.gather(*[
                _worker(h, task, seed, cwds[h]) for h in harnesses
            ])
            for r in outcomes:
                rows.append(r)
                if results_path is not None:
                    emit_row(r, results_path)
            # Merge probe (3C): reconcile passing workers at state level.
            passing = [cwds[o.harness] for o in outcomes if o.passed]
            probes.append(await _merge_probe(task, passing))
    return rows, probes


async def _merge_probe(task: Task, passing_cwds: list[Path]) -> MergeProbe:
    """Reconcile N independently-passing worktrees by git and re-verify.

    Merge policy (the relay's deterministic state merge): take the first
    passing worker as base, then cherry-pick the state (worktree files) of the
    others and re-run the objective verify. A pass after merge = no silent
    semantic conflict; a fail = the textual merge missed a semantic one.
    """
    probe = MergeProbe(n_passing=len(passing_cwds))
    if len(passing_cwds) < 2:
        probe.detail = "need >=2 passing workers to merge"
        return probe
    base = passing_cwds[0]
    rest = passing_cwds[1:]
    # Bring each other worker's non-git files onto the base (the state merge).
    for other in rest:
        for f in other.iterdir():
            if f.name == ".git":
                continue
            target = base / f.name
            if f.is_dir():
                subprocess.run(["cp", "-r", str(f), str(target)], check=False)
            else:
                if target.exists() and target.read_bytes() != f.read_bytes():
                    # A genuine delta: both wrote the same file differently.
                    probe.detail += f" conflict:{f.name} "
                    return probe
                if not target.exists():
                    subprocess.run(["cp", str(f), str(target)], check=False)
    ok, det = _verify(task, base)
    probe.merged = True
    probe.still_passes = ok
    probe.detail = (probe.detail + " merged:" + det) if det else probe.detail
    return probe


def emit_row(r: WorkerRow, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "harness": r.harness, "task": r.task_id, "seed": r.seed,
            "passed": r.passed, "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out, "tokens_cache": r.tokens_cache,
            "latency_ms": r.latency_ms, "detail": r.detail,
        }) + "\n")


def summarize(rows: list[WorkerRow], probes: list[MergeProbe]) -> str:
    if not rows:
        return "Fase 3B: no rows."
    lines = [
        f"{'harness':<10}{'task':<13}{'#':>3}{'pass':>5}{'in':>7}{'out':>7}"
        f"{'cache':>8}{'cold':>8}{'rho%':>7}{'lat_s':>7}",
        "-" * 78,
    ]
    for r in sorted(rows, key=lambda x: (x.task_id, x.seed, x.harness)):
        cold = r.tokens_in + r.tokens_cache
        rho = (r.tokens_cache / cold * 100.0) if cold else 0.0
        lat = r.latency_ms / 1000.0 if r.latency_ms else 0.0
        lines.append(
            f"{r.harness:<10}{r.task_id:<13}{r.seed:>3}{str(r.passed):>5}"
            f"{r.tokens_in:>7}{r.tokens_out:>7}{r.tokens_cache:>8}{cold:>8}"
            f"{rho:>6.0f}%{lat:>7.1f}"
        )
    lines.append("-" * 78)
    for h in sorted({r.harness for r in rows}):
        rs = [r for r in rows if r.harness == h]
        mean_in = sum(r.tokens_in for r in rs) / len(rs)
        mean_cold = sum(r.tokens_in + r.tokens_cache for r in rs) / len(rs)
        mp = sum(1 for r in rs if r.passed)
        mean_lat = sum(r.latency_ms for r in rs) / len(rs) / 1000.0
        lines.append(
            f"{h:<10}{'<mean>':<13}{len(rs):>3}{f'{mp}/{len(rs)}':>5}"
            f"{mean_in:>7.0f}{'-':>7}{'-':>8}{mean_cold:>8.0f}{'-':>7}{mean_lat:>7.1f}"
        )
    if probes:
        lines.append("-" * 78)
        p = probes[0] if len(probes) == 1 else probes
        lines.append(f"merge probes: {len(probes)} | {p}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--harnesses", nargs="*", default=list(HARNESSES),
                    choices=HARNESSES)
    ap.add_argument("--tasks", nargs="*", default=["hello"],
                    help="task ids from TASKS")
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--results", type=Path, default=None,
                    help="JSONL path to append each completed worker row")
    args = ap.parse_args(argv)

    tasks = [t for t in TASKS if t.id in args.tasks]
    rows, probes = asyncio.run(run_swarm(tasks, seeds=args.seeds,
                                         harnesses=args.harnesses,
                                         results_path=args.results))
    print(summarize(rows, probes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
