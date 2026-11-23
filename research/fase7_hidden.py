# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 7; the create-from-scratch / HIDDEN-SPEC discriminator.

The audit (C1/C2/C8) invalidated every prior pass-rate: seeds put the
solution on disk (verify passed doing nothing) and the verify was embedded in
the prompt (spec inferable). This probe fixes all three confounds so the
completion number means something:

  - CREATE FROM SCRATCH: the deliverable does NOT exist on disk. ``files`` only
    seeds a // placeholder (an empty module), so a run that writes NOTHING
    fails the verify (import error). No seeded solution, no seed confound.
  - HIDDEN SPEC: ``task.prompt`` deliberately omits the spec's crux (the
    ordering requirement). It is NOT derivable from the prompt text.
  - HIDDEN VERIFY: the grader (``verify_cmd``) is a python snippet the arms
    never see and is never written to disk. No spec leak through the verify.

The SPEC (a product requirement: merge into DESCENDING order) is only ever
delivered inside the phase-1 SETUP prompt; the non-rederivable context of a
real switch. The four arms differ ONLY in how that context crosses the switch:

  (a) re-send   ; fresh session, FULL phase-1 setup+transcript re-sent
  (b) native    ; native --continue of the phase-1 session
  (c) poor      ; current recorder destilado (points at disk, NO decisions)
  (d) rich      ; enriched destilado (decisions extracted from phase-1 text)

Predictions, the decision the audit demands:
  - (c) FAILS  (spec not carried by pointer-only destilado) while
  - (d) PASSES (spec carried by extracted decisions)  → the relay REALLY
        carries the non-rederivable ~35-50%; the current destilado is the
        defect, not the concept. BUILD is licensed by (d) passing.
  - if (c) AND (d) fail → the relay cannot carry hidden spec at all (concept dead).
  - if (c) passes → the model re-derives the spec from the prompt (then the
        relay is unnecessary for this spec-size; Fase-5 negative re-asserts).

Same shell=True discipline (argv-list refuses writes, measured); same
abort-safe JSONL persistence. Degrades, never raises.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from fase2_bench import Task, _run_opencode, _make_worktree, _verify
from fase2_bench import _usage_from_opencode, recorder_destilado
from fase5_enrich import enriched_destilado, _extract_decisions

PLACEHOLDER = "# placeholder; the previous agent left this module for you to fill in.\n"

HIDDEN_SPEC = (
    "PRODUCT REQUIREMENT: this function only ever receives lists that are "
    "already sorted in DESCENDING order (largest first, no ties broken by "
    "anything except the values). It must return a merge of the two lists that "
    "is ALSO in DESCENDING order (largest first), including all duplicate "
    "values from both lists. It must handle empty inputs."
)


def hidden_task() -> Task:
    """Create-from-scratch, hidden spec, hidden verify (the discriminator).

    The prompt deliberately withholds the ordering requirement: no model
    reading only ``task.prompt`` can know the answer must be descending. The
    grade targets exactly that; discovering + honoring the hidden spec.
    """
    verify = (
        "python3 - <<'PY'\n"
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from merge_sorted import merge_sorted\n"
        "ok = (merge_sorted([5,3,1],[6,4,2]) == [6,5,4,3,2,1]\n"
        "      and merge_sorted([],[2,1]) == [2,1]\n"
        "      and merge_sorted([1],[0]) == [1,0]\n"
        "      and merge_sorted([7,5,2],[5,3,3,1]) == [7,5,5,3,3,2,1])\n"
        "sys.exit(0 if ok else 1)\n"
        "PY"
    )
    return Task(
        id="hidden_spec",
        prompt=("A product requirement for the module merge_sorted.py was given "
                "to the previous agent as a relay contract; it is NOT written "
                "here. Create merge_sorted.py from scratch with a function "
                "merge_sorted(a, b) that merges two sorted lists exactly as "
                "that relay contract requires. Honor the contract carried by "
                "the prior transcript / handoff below. Deliver only "
                "merge_sorted.py. Do not claim completion until you are sure "
                "the function satisfies the contract."),
        verify_cmd=verify,
        files={"merge_sorted.py": PLACEHOLDER},
    )


# ---- arm builders ------------------------------------------------------------

@dataclass
class HiddenRow:
    arm: str            # a/b/c/d
    task_id: str
    seed: int
    passed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache: int = 0
    latency_ms: int = 0
    spec_carried: str = ""   # was the spec present in the prompt text sent?
    detail: str = ""


def emit_row(r: HiddenRow, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(r.__dict__) + "\n")


def _has_spec_cue(text: str) -> str:
    """Was the hidden spec's crux present in the prompt text itself?"""
    low = (text or "").lower()
    if "descending" in low or "largest first" in low:
        return "spec-in-prompt"
    return "none"


async def _phase1(task: Task) -> tuple[Path, str, str]:
    """Phase-1: an agent RECEIVES the hidden spec in its setup prompt and
    states its diagnosis in its reply (so transcript text carries the
    non-rederivable decision), but leaves the module un-fixed.

    Returns (cwd, transcript_text, session_id, contaminated) where
    ``contaminated`` is True if the phase-1 agent (against instructions) wrote
    code into its own worktree; which would let arm (b) pass doing nothing.
    """
    cwd = _make_worktree(task)
    setup = (
        "Read merge_sorted.py (currently a placeholder). The following product "
        "contract was just decided and must be EXACTLY honored:\n"
        + HIDDEN_SPEC
        + "\nDiagnose what the implementation must do to satisfy it and state "
        "your diagnosis plainly IN YOUR REPLY so a later agent can read it. "
        "DO NOT modify merge_sorted.py in any way; leave it byte-for-byte "
        "identical to the placeholder. Do not implement, do not write tests, "
        "do not create files. Diagnosis only. Print DIAGNOSIS DONE when finished."
    )
    ok, out, _ = _run_opencode(setup, cwd=cwd)
    session_id = ""
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("{") and "sessionID" in line:
            try:
                session_id = json.loads(line).get("sessionID", session_id)
            except json.JSONDecodeError:
                pass
    # Name the commit base so enriched_destilado's git diff reads a real base.
    # Then detect if phase-1 contaminated its own worktree by writing code that
    # is NOT the placeholder; that would let arm (b) pass doing nothing.
    subprocess.run(["git", "add", "-A"], cwd=str(cwd), check=False)
    subprocess.run(["git", "commit", "-qm", "phase1 base"], cwd=str(cwd), check=False)
    contamination = (cwd / "merge_sorted.py").read_text().strip() != PLACEHOLDER.strip()
    if contamination:
        out = (out or "") + "\nPHASE1-CONTAMINATED: merge_sorted.py was modified on disk\n"
    return cwd, (out or ""), session_id, contamination


async def arm_a(task: Task, phase_cwd: Path, transcript: str,
                seed: int, results: Path) -> HiddenRow:
    """(a) re-send; fresh session, FULL phase-1 context re-sent."""
    cwd = _make_worktree(task)
    prompt = (task.prompt + "\n\n===== PRIOR SETUP + TRANSCRIPT (full) ===\n"
              + transcript[:30000])
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    r = HiddenRow("a", task.id, seed, passed, u.input_tokens, u.output_tokens,
                  u.cache_read_tokens, ms, _has_spec_cue(prompt),
                  det if ok else f"opencode failed: {out[:120]}")
    emit_row(r, results)
    return r


async def arm_b(task: Task, phase_cwd: Path, session_id: str,
                seed: int, results: Path, contaminated: bool = False) -> HiddenRow:
    """(b) native; continue the SAME phase-1 session (its context carries the
    spec; runs in phase.cwd because a session is anchored to its cwd). If the
    phase-1 agent contaminated the worktree, the pass is vacuous and marked."""
    ok, out, ms = _run_opencode(task.prompt, cwd=phase_cwd,
                                session=session_id, resume=True)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, phase_cwd)
    note = "PHASE1-CONTAMINATED(sol-on-disk)" if contaminated else "clean"
    r = HiddenRow("b", task.id, seed, passed, u.input_tokens, u.output_tokens,
                  u.cache_read_tokens, ms, "n/a(native-session)",
                  (note + " " + det) if det else note)
    emit_row(r, results)
    return r


async def arm_c(task: Task, phase_cwd: Path, seed: int,
                results: Path) -> HiddenRow:
    """(c) poor; current recorder destilado: pointer-only, NO decisions."""
    cwd = _make_worktree(task)
    prompt = task.prompt + "\n\n===== recorder destilado (relay task-state) ===\n" \
        + recorder_destilado(task)
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    r = HiddenRow("c", task.id, seed, passed, u.input_tokens, u.output_tokens,
                  u.cache_read_tokens, ms, _has_spec_cue(prompt),
                  det if ok else f"opencode failed: {out[:120]}")
    emit_row(r, results)
    return r


async def arm_d(task: Task, phase_cwd: Path, transcript: str,
                seed: int, results: Path) -> HiddenRow:
    """(d) rich; enriched destilado: only the decisions EXTRACTED from the
    phase-1 reply (never the spec verbatim; that would be a leak, C8). If
    phase-1 failed to state the diagnosis in its reply, the relay genuinely
    lacks the decision and the arm fails honestly."""
    cwd = _make_worktree(task)
    dest = enriched_destilado(task, transcript, phase_cwd)
    probe = _extract_decisions(transcript)
    if any(k in probe.lower() for k in ("descending", "largest first")):
        dest += ("\n  relay decision (from phase-1 reply): the contract governs "
                 "ordering of the merged output; see the diagnosis above and "
                 "honor it exactly.")
    prompt = task.prompt + "\n\n===== enriched destilado (decisions carried) ===\n" + dest
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    r = HiddenRow("d", task.id, seed, passed, u.input_tokens, u.output_tokens,
                  u.cache_read_tokens, ms, _has_spec_cue(prompt),
                  det if ok else f"opencode failed: {out[:120]}")
    emit_row(r, results)
    return r


# ---- matrix / report ---------------------------------------------------------

def summarize(rows: list[HiddenRow]) -> str:
    if not rows:
        return "Fase 7: no rows."
    lines = [
        f"{'arm':<5}{'pass':>5}{'in':>8}{'out':>7}{'cache':>9}{'cold':>9}{'spec':<20}{'lat_s':>7}",
        "-" * 76,
    ]
    for r in sorted(rows, key=lambda x: (x.seed, x.arm)):
        cold = r.tokens_in + r.tokens_cache
        lat = r.latency_ms / 1000.0 if r.latency_ms else 0.0
        lines.append(f"{r.arm:<5}{str(r.passed):>5}{r.tokens_in:>8}{r.tokens_out:>7}"
                     f"{r.tokens_cache:>9}{cold:>9}{r.spec_carried:<20}{lat:>7.1f}")
        if r.detail and not r.passed:
            lines.append(f"     {r.detail[:160]}")
    lines.append("-" * 76)
    for arm in sorted({r.arm for r in rows}):
        rs = [r for r in rows if r.arm == arm]
        mp = sum(1 for r in rs if r.passed)
        mc = sum(r.tokens_in + r.tokens_cache for r in rs) / len(rs) if rs else 0
        lines.append(f"{arm:<5}{f'{mp}/{len(rs)}':>7}{mc:>8.0f} mean cold tokens")
    return "\n".join(lines)


async def run_matrix(seeds: int, arms: list[str],
                     results: Path) -> list[HiddenRow]:
    rows: list[HiddenRow] = []
    task = hidden_task()
    for seed in range(seeds):
        phase_cwd, transcript, sess, contaminated = await _phase1(task)
        # Execution order defensively: creation arms (a/c/d) first so they get
        # the exact phase-1 base; arm (b) MUTATES phase_cwd by continuing it.
        if "a" in arms:
            rows.append(await arm_a(task, phase_cwd, transcript, seed, results))
        if "c" in arms:
            rows.append(await arm_c(task, phase_cwd, seed, results))
        if "d" in arms:
            rows.append(await arm_d(task, phase_cwd, transcript, seed, results))
        if "b" in arms and sess:
            rows.append(await arm_b(task, phase_cwd, sess, seed, results,
                                    contaminated=contaminated))
        elif "b" in arms:
            r = HiddenRow("b", task.id, seed, False, 0, 0, 0, 0, "no-session", "no phase-1 session id captured")
            rows.append(r)
            emit_row(r, results)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms", nargs="*", default=["a", "c", "d"],
                    choices=["a", "b", "c", "d"])
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--results", type=Path,
                    default=Path("results/fase7_hidden.jsonl"))
    ap.add_argument("--report", type=Path, default=None,
                    help="summarize an existing results JSONL without re-running")
    args = ap.parse_args(argv)
    if args.report is not None:
        rows = []
        for line in open(args.report, encoding="utf-8"):
            if not line.strip():
                continue
            rows.append(HiddenRow(**json.loads(line)))
        print(summarize(rows))
        return 0
    rows = asyncio.run(run_matrix(args.seeds, args.arms, args.results))
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())