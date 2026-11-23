# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 8; a REAL event recorder (C9) on a MULTI-MODULE task.

The audit's C9: no instrument ever ran a real events+git recorder; the
"destilado" was a static pointer or a post-hoc parse of the transcript. This
probe builds the actual recorder; a live stream consumer that turns opencode's
JSON event stream (tool_use write/edit/bash, text, reasoning) into a
deterministic RunEvent log, then composes a destilado from THAT, while phase-1
is still producing it; and measures it against the transcript-parsed
enriched destilado on a task whose state spans MULTIPLE modules (the other
standing validity gap).

Task: a small service; ``config.py`` (a dataclass), ``engine.py`` (imports
config, does the work) and a hidden grader. phase-1 sets up config.py and
engine.py PARTIALLY, states the non-derivable product decision in its reply,
and stops. The state that must cross the switch is:
  - the partial files on disk (pointer/re-derivable),
  - the decision (which module owns what, and a subtle correctness rule) that
    is ONLY in phase-1's reply (non-rederivable).

Arms run on fresh worktrees seeded only with the phase-1 PARTIAL state:
  (a) re-send ; the full phase-1 transcript (+ partial state)
  (c) poor    ; pointer-only recorder destilado (files list, no decisions)
  (d) rich    ; enriched destilado (transcript parsed POST-HOC)
  (e) recorder; destilado from the LIVE event recorder (the C9 instrument)

The audit question answered: does the LIVE recorder (the thing a relay would
actually run) carry as well as the post-hoc parse, and does EITHER beat
pointer-only on a multi-module task where the decision is cross-module?

Anti-leak: the task prompt never mentions the product decision; the grader
verify is hidden (a python snippet the model never sees). arms never receive
the spec verbatim. Degrades, never raises.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from fase2_bench import Task, _run_opencode, _make_worktree, _verify
from fase2_bench import _usage_from_opencode, recorder_destilado
from fase5_enrich import enriched_destilado


# ---- the real recorder: live consumption of the opencode event stream --------

@dataclass
class RunEvent:
    kind: str          # decision | write | edit | bash | text
    payload: str = ""
    file: str = ""


@dataclass
class Recorder:
    """Live event recorder: consumes opencode's JSONL stream and composes a
    deterministic RunEvent log from which a destilado is derived."""

    events: list[RunEvent] = field(default_factory=list)

    def feed_line(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return
        typ = obj.get("type")
        part = obj.get("part") or {}
        if typ == "text":
            txt = (part.get("text") or "").strip()
            if txt:
                self.events.append(RunEvent("decision", txt[:600]))
        elif typ in ("reasoning", "thinking"):
            txt = (part.get("text") or "").strip()
            if txt:
                self.events.append(RunEvent("decision", "[re] " + txt[:600]))
        elif typ == "tool_use":
            tool = part.get("tool") or ""
            state = part.get("state") or {}
            status = (state.get("status") or "")
            if status != "completed":
                return
            if tool in ("write", "edit", "patch"):
                fp = (state.get("metadata") or {}).get("filepath") or ""
                fp = fp or (state.get("input") or {}).get("filePath") or ""
                content = (state.get("input") or {}).get("content") or ""
                self.events.append(RunEvent("write", content[:400], fp))
            elif tool == "bash":
                cmd = ((state.get("input") or {}).get("command") or "")[:200]
                o = (state.get("output") or "")[:200]
                self.events.append(RunEvent("bash", f"$ {cmd}\n{o}", ""))

    def destilado(self, task: Task, phase_cwd: Path) -> str:
        """Compose the relay destilado from the LIVE event log + git state."""
        writes = [e for e in self.events if e.kind == "write"]
        decisions = [e for e in self.events
                     if e.kind in ("decision", "bash", "text")]
        diff = self._git_diff(phase_cwd)
        lines = [
            "RELAY HANDOFF (live recorder). The task deliverable is NOT done; "
            "continue working toward it now.",
            f"  task: {task.id}",
            "  on disk (re-derivable, pointer): git HEAD + partial state; "
            f"git diff --stat:\n    {diff}",
            "  the previous agent said (decisions captured LIVE, non-derivable):",
        ]
        if decisions:
            for d in decisions[:12]:
                lines.append(f"    - {d.payload[:200]}")
        else:
            lines.append("    (no decision text captured by the recorder)")
        lines.append("  files the previous agent wrote/edited (pointer):")
        for w in writes[:10]:
            lines.append(f"    - {w.file}")
        if not writes:
            lines.append("    (none captured)")
        return "\n".join(lines)

    @staticmethod
    def _git_diff(cwd: Path, limit: int = 600) -> str:
        try:
            r = subprocess.run(["git", "diff", "--stat"], cwd=str(cwd),
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return "(no baseline commit)"
            return r.stdout.strip()[:limit] or "(empty diff)"
        except Exception:
            return "(git diff unavailable)"


def run_opencode_recorded(prompt: str, *, cwd: Path,
                          timeout: float = 1500.0) -> tuple[bool, str, str, int]:
    """Run opencode headless, feeding every stdout line to a live Recorder.

    Returns (ok, stdout_json, recorder.events→json, ms). shell=True mandatory
    (argv-list refuses writes, the Fase 2/3 invalidator)."""
    import shlex
    if shutil.which("opencode") is None:
        return False, "opencode CLI not installed", "", 0
    rec = Recorder()
    argv = ["opencode", "run", "--format", "json", "--auto", prompt]
    cmd = " ".join(shlex.quote(a) for a in argv)
    start = time.monotonic()
    try:
        code = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                              shell=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "[timeout]", "", int(timeout * 1000)
    stdout = (code.stdout or b"").decode("utf-8", "replace")
    for line in stdout.splitlines():
        rec.feed_line(line)
    ms = int((time.monotonic() - start) * 1000)
    evlog = json.dumps([e.__dict__ for e in rec.events])
    return code.returncode == 0, stdout, evlog, ms


# ---- the multi-module task ---------------------------------------------------

def service_task() -> Task:
    """multi-module: config.py + engine.py + hidden grader.

    phase-1 receives the non-rederivable product decision (only via its setup
    prompt): "the HEAD of the engine's output list is the single most-recent
    favorite; ties broken by STRING order of the name". A correct engine must
    expose last_favorite(config): picks the id of the entry with the newest
    tag, tie-broken by name. The decision is NOT inferable from the files.
    """
    verify = (
        "python3 - <<'PY'\n"
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from config import Entry\n"
        "from engine import last_favorite\n"
        "ok = (last_favorite(Entry('a3', tag='2024-11-02'), Entry('b1', tag='2024-11-02')) == 'b1'\n"
        "      and last_favorite(Entry('abc', tag='2024-11-01'), Entry('xyz', tag='2024-11-03')) == 'xyz'\n"
        "      and last_favorite(Entry('only', tag='2024-10-01')) == 'only')\n"
        "sys.exit(0 if ok else 1)\n"
        "PY"
    )
    config_src = (
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class Entry:\n"
        "    name: str\n"
        "    tag: str\n"
    )
    return Task(
        id="svc",
prompt=("In this repo there is config.py (an Entry dataclass with "
            "name/tag) and engine.py (currently a stub). The product "
            "decision that governs engine.last_favorite was given to the "
            "previous agent as a relay contract; it is NOT written here. "
            "Implement engine.last_favorite(*entries); it receives Entry "
            "objects as separate arguments; to satisfy that contract "
            "exactly. Honor the decision carried by the prior "
            "transcript / handoff below. Deliver engine.py only."),
        verify_cmd=verify,
        files={"config.py": config_src,
               "engine.py": "def last_favorite(entries):\n    raise NotImplementedError\n"},
    )


HIDDEN_SVC_DECISION = (
    "PRODUCT CONTRACT: engine.last_favorite receives the entries as separate "
    "arguments (each an Entry with name and tag). It must return the NAME of "
    "the entry with the newest tag; when two tags are EQUAL the tie is broken "
    "by STRING order of the name (later name wins). A single entry returns its "
    "own name."
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


async def _phase1(task: Task, results: Path) -> tuple[Path, str, str, int]:
    """phase-1: agent receives the hidden service decision via its setup prompt,
    STATES it in its reply, and partially implements engine.py (a stub that
    compiles but does NOT satisfy the contract). Returns (cwd, transcript,
    recorder_eventlog_json, n_events)."""
    cwd = _make_worktree(task)
    setup = (
        "Read config.py and engine.py (engine is a stub). The following product "
        "contract was just decided and governs last_favorite exactly:\n"
        + HIDDEN_SVC_DECISION
        + "\nState the contract plainly IN YOUR REPLY so a later agent can read "
        "it (do NOT write it into any file; it stays off disk). Implement "
        "engine.py PARTIALLY: make it a stub that imports cleanly and returns "
        "the first entry's name from the tie-breaking rule, but do NOT "
        "complete it. Print SETUP DONE when finished."
    )
    ok, out, evlog, ms = run_opencode_recorded(setup, cwd=cwd)
    subprocess.run(["git", "add", "-A"], cwd=str(cwd), check=False)
    subprocess.run(["git", "commit", "-qm", "phase1 partial"], cwd=str(cwd), check=False)
    n = 0
    if evlog:
        n = len(json.loads(evlog))
    return cwd, (out or ""), evlog, n


async def arm_a(task: Task, transcript: str, seed: int, results: Path) -> Row:
    cwd = _make_worktree(task)
    prompt = task.prompt + "\n\n===== PRIOR SETUP + TRANSCRIPT (full) ===\n" + transcript[:30000]
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


async def arm_d(task: Task, transcript: str, phase_cwd: Path,
                seed: int, results: Path) -> Row:
    """enriched: transcript parsed POST-HOC (the Fase 5/7 approximation)."""
    cwd = _make_worktree(task)
    dest = enriched_destilado(task, transcript, phase_cwd)
    prompt = task.prompt + "\n\n===== enriched destilado ===\n" + dest
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    r = Row("d", task.id, seed, passed, u.input_tokens, u.output_tokens,
            u.cache_read_tokens, ms, 0, det if ok else f"opencode failed: {out[:120]}")
    emit_row(r, results)
    return r


async def arm_e(task: Task, evlog: str, phase_cwd: Path,
                seed: int, results: Path) -> Row:
    """recorder: destilado from the LIVE event recorder (the C9 instrument).

    Unlike a/d (which re-derive from the transcript into a FRESH worktree), the
    recorder arm is the relay REAL: the destilado describes the phase-1 partial
    state and is delivered TOGETHER with that state. So arm_e clones the
    phase-1 worktree (the real on-disk state the recorder points at) instead of
    starting from the factory stub."""
    tmp = Path(tempfile.mkdtemp(prefix="fase8_rec_"))
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
    task = service_task()
    for seed in range(seeds):
        phase_cwd, transcript, evlog, n_ev = await _phase1(task, results)
        if "a" in arms:
            rows.append(await arm_a(task, transcript, seed, results))
        if "c" in arms:
            rows.append(await arm_c(task, seed, results))
        if "d" in arms:
            rows.append(await arm_d(task, transcript, phase_cwd, seed, results))
        if "e" in arms:
            rows.append(await arm_e(task, evlog, phase_cwd, seed, results))
    return rows


def summarize(rows: list[Row]) -> str:
    if not rows:
        return "Fase 8: no rows."
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
    ap.add_argument("--arms", nargs="*", default=["a", "c", "d", "e"],
                    choices=["a", "b", "c", "d", "e"])
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--results", type=Path, default=Path("results/fase8_recorder.jsonl"))
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