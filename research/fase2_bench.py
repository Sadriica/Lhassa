# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 2; 4-strategy bench (relay cost A/B), opencode-instrumented.

Runs the plan's four arms on coding tasks and compares tokens-to-completion
under a cold or warm cache, recording per-turn token + cache accounting the
way the relay must be costed (the [S31] CAPC rho discipline: attribute the
bill, don't assume it).

  (a) raw re-send   ; resume with the FULL prior transcript re-sent
  (b) native switch ; resume via the harness-native session/--continue
  (c) recorder cold ; cold start with a deterministic recorder destilado
                       (reconstruct from events+git; carry the non-rederivable
                       ~20-40% measured in Fase 0.2)
  (d) recorder cheap; recorder destilado summarized by a cheap model

Execution + accounting is **opencode**: ``opencode run --format json`` returns
the assistant text plus per-turn usage (input/output/cache.read/cache.write),
and Lhassa already parses that exact stream (:func:`lhassa.llm.provider`
OpenCodeProvider). Because the CLI subscriptions are $0 marginal cash by design
(gateway.PRICING), the honest cost metric here is **tokens-to-completion with
cache hits split out (rho)**; that is what the relay actually trades.

Each arm runs in a fresh git worktree of a target repo and is scored by an
objective command (the repo's own test/verify), never an LLM judge (standing
invariant). Instrument doc: app/research/task-state-relay.md '4-strategy bench
(Fase 2, on LhassaBench)'.

Degrades, never raises: a CLI that errors/times out yields a failed row with the
reason in ``detail`` so a stubborn arm never aborts the matrix.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

OPENCODE_BIN = "opencode"
ARM_LABELS = {"a": "raw re-send", "b": "native switch", "c": "recorder cold",
              "d": "recorder cheap"}


# ---- data shapes -------------------------------------------------------------

@dataclass
class Usage:
    """Per-arm token accounting (S15/S31 discipline): totals + cache split."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def effective_input(self) -> int:
        """Cache-read input is discounted by [S31] rho (~0.1x) for cost parity."""
        return int(self.input_tokens + self.cache_read_tokens * 0.1)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens


@dataclass
class ArmResult:
    arm: str
    task_id: str
    passed: bool
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache: int = 0
    latency_ms: int = 0
    detail: str = ""
    seed: int = 0


@dataclass
class Task:
    id: str
    prompt: str
    verify_cmd: str
    repo: Optional[str] = None  # git URL/path to seed the worktree (else empty)
    files: dict[str, str] = None  # path -> initial file content


# ---- process helpers ---------------------------------------------------------

async def _shell(cmd: list[str], *, cwd: Optional[Path] = None,
                 timeout: float = 1800.0) -> tuple[int, str, int]:
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd or "."),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        ms = int((time.monotonic() - start) * 1000)
        return (proc.returncode or 0), (stdout_b or b"").decode("utf-8", "replace"), ms
    except asyncio.TimeoutError:
        return 124, f"[timeout {cmd[0]}]", int((time.monotonic() - start) * 1000)


def _make_worktree(task: Task) -> Path:
    """Create a fresh temp git dir; clone the repo if given, seed files."""
    tmp = Path(tempfile.mkdtemp(prefix="fase2_"))
    if task.repo:
        subprocess.run(["git", "clone", "-q", task.repo, str(tmp / "repo")],
                       check=True, timeout=300)
        wd = tmp / "repo"
    else:
        subprocess.run(["git", "init", "-q", str(tmp)], check=True)
        wd = tmp
    if task.files:
        for rel, content in task.files.items():
            p = wd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return wd


def _run_opencode(prompt: str, *, cwd: Path,
                  session: Optional[str] = None,
                  resume: bool = False) -> tuple[bool, str, int]:
    """Run opencode headless (--format json) in ``cwd``; return (ok, json, ms).

    Same stream shape Lhassa's OpenCodeProvider parses (--format json). For
    arm (b) ``resume``/``session`` continue an existing session id.

    CRITICAL (measured 2026-08-05): opencode must be invoked via the SHELL,
    not as an argv list. With a list (subprocess.run([...])) opencode refuses
    to perform file writes (rc=0, model "works", zero files on disk). With
    shell=True the same prompt writes files. This bug silently invalidates any
    bench measured through the argv-list path; Fase 2/3 rows are suspect and
    must be re-verified under the corrected runner before trust.
    """
    if shutil.which(OPENCODE_BIN) is None:
        return False, "opencode CLI not installed", 0
    argv = [OPENCODE_BIN, "run", "--format", "json", "--auto"]
    if resume:
        argv += ["--continue", session or "last"]
    argv.append(prompt)
    import shlex
    cmd = " ".join(shlex.quote(a) for a in argv)
    start = time.monotonic()
    try:
        code = subprocess.run(cmd, cwd=str(cwd), capture_output=True, shell=True,
                              timeout=1800)
    except subprocess.TimeoutExpired:
        return False, "[timeout opencode run]", 1800000
    ms = int((time.monotonic() - start) * 1000)
    return code.returncode == 0, (code.stdout or b"").decode("utf-8", "replace"), ms


def _usage_from_opencode(stdout: str) -> Usage:
    """Extract token totals from the opencode JSON event stream.

    opencode emits one JSON object per line; per-turn accounting lives in the
    ``step-finish`` part as ``part.tokens.{input,output,cache.read}``
    (cache.write, reasoning also present). Sum across the run for the arm's
    true bill; the S15/S31 discipline, attributed not assumed.
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
        if obj.get("type") not in ("step_finish",):
            continue
        p = obj.get("part") or {}
        tokens = p.get("tokens") or {}
        u.input_tokens += int(tokens.get("input") or 0)
        u.output_tokens += int(tokens.get("output") or 0)
        cache = tokens.get("cache") or {}
        u.cache_read_tokens += int(cache.get("read") or 0)
    return u


def _verify(task: Task, cwd: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["bash", "-lc", task.verify_cmd], cwd=str(cwd),
            capture_output=True, text=True, timeout=600,
        )
        return proc.returncode == 0, (proc.stdout or proc.stderr or "")[:400]
    except subprocess.TimeoutExpired:
        return False, "[verify timeout]"


# ---- recorder (the relay's destilado) ----------------------------------------

def recorder_destilado(task: Task) -> str:
    """Deterministic recorder: derive canonical state from the task's git/files.

    Emits a compact destilado of WHAT IS NON-REDERIVABLE from disk plus pointers
    to what is (git HEAD, file list); the shape Fase 0.2 says a relay must
    carry (decisions + context, not tool_result dumps).

    Design note (measured): the destilado must NOT restate the task prompt as
    'context to carry'; doing that plus a 'do not resend' hint makes a cheap
    model believe the deliverable is already done and give up (measured: 1k
    in-tokens, no file written, 0/9 on codegrade/domainledger). The destilado
    is a STATE SUMMARY; the deliverable remains open and must be said so.
    """
    lines = [
        "STATE SUMMARY (relay handoff). The task deliverable is NOT done; "
        "continue working toward it now.",
        f"  task: {task.id}",
        f"  repo: {task.repo or '(empty)'}",
        "  on disk (re-derivable, pointer only): git HEAD + these files:",
    ]
    if task.files:
        lines += [f"    {p}" for p in task.files]
    lines.append(
        "  prior decisions to honor: see the task description above; it is the "
        "only non-derivable context, and it is already in front of you."
    )
    return "\n".join(lines)


# ---- the four arms -----------------------------------------------------------

@dataclass
class Phase1:
    """Phase 1 = one opencode session that sets up the task's git base and
    stops JUST short of completing (a real mid-task switch point). Its
    transcript-text feeds arm (a), its session id feeds arm (b), and both are
    what the recorder (c)/(d) must make unnecessary."""

    transcript: str = ""
    session_id: str = ""
    cwd: Optional[Path] = None


async def _phase1(task: Task) -> Phase1:
    cwd = _make_worktree(task)
    # A phase-1 session: set up the repo state and hand back; then "switch".
    setup = (
        "Set up this repo for the following task (do not complete it):\n"
        + task.prompt
        + "\nDo only the initial setup commit and print DONE when it is committed."
    )
    ok, out, ms = _run_opencode(setup, cwd=cwd)
    session_id = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and 'sessionID' in line:
            try:
                session_id = json.loads(line).get("sessionID", session_id)
            except json.JSONDecodeError:
                pass
    return Phase1(transcript=out, session_id=session_id, cwd=cwd)


def _clone_phase(phase: Phase1) -> Path:
    """Clone the phase-1 worktree into a fresh dir so every arm starts from the
    same git base in isolation (a proper A/B, no cross-arm pollution)."""
    tmp = Path(tempfile.mkdtemp(prefix="fase2_arm_"))
    subprocess.run(["git", "clone", "-q", str(phase.cwd), str(tmp / "repo")],
                   check=True, timeout=300)
    return tmp / "repo"


async def arm_a(task: Task, phase: Phase1, seed: int = 0) -> ArmResult:
    """(a) raw re-send; naive baseline: resume by re-sending the FULL phase-1
    transcript into a fresh session."""
    cwd = _clone_phase(phase)
    prompt = task.prompt + "\n\n===== PRIOR TRANSCRIPT ===\n" + phase.transcript[:12000]
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    return ArmResult("a", task.id, passed, u.input_tokens, u.output_tokens,
                     u.cache_read_tokens, ms,
                     det if ok else f"opencode failed: {out[:200]}", seed)


async def arm_b(task: Task, phase: Phase1, seed: int = 0) -> ArmResult:
    """(b) native switch; continue the SAME phase-1 opencode session.

    Important: resuming a session ties it to its own cwd (the phase-1
    worktree), so arm (b) must run and verify in ``phase.cwd``; NOT a clone.
    Cloning breaks the resume contract. This is itself a finding: the harness
    keeps its cwd, not the operator's."""
    cwd = phase.cwd
    ok, out, ms = _run_opencode(task.prompt, cwd=cwd, session=phase.session_id, resume=True)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    return ArmResult("b", task.id, passed, u.input_tokens, u.output_tokens,
                     u.cache_read_tokens, ms,
                     det if ok else f"opencode failed: {out[:200]}", seed)


async def arm_c(task: Task, seed: int = 0) -> ArmResult:
    """(c) recorder cold; cold start seeded by the deterministic destilado."""
    cwd = _make_worktree(task)
    prompt = task.prompt + "\n\n===== recorder destilado ===\n" + recorder_destilado(task)
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    return ArmResult("c", task.id, passed, u.input_tokens, u.output_tokens,
                     u.cache_read_tokens, ms, det if ok else f"opencode failed: {out[:200]}", seed)


async def arm_d(task: Task, seed: int = 0) -> ArmResult:
    """(d) recorder cheap; destilado via a cheap model pass (best-effort).

    If a cheap model provider is not resolvable this degrades to arm (c) with a
    note in ``detail``; the token bill still reflects what actually ran.
    """
    cwd = _make_worktree(task)
    dest = recorder_destilado(task)
    try:
        from lhassa.config import Config
        from lhassa.llm.provider import get_model_provider

        cfg = Config()
        provider = get_model_provider(cfg, name="default")

        comp = await provider.complete(
            [{"role": "user",
              "content": f"Summarize to <=200 tokens what is NOT derivable from "
                          f"disk, for a fresh agent continuing this task:\n{dest}"}],
            max_tokens=220,
        )
        summary = getattr(comp, "text", "") or ""
        prompt = task.prompt + "\n\n===== recorder destilado (cheap) ===\n" + summary[:2000]
    except Exception:
        summary = ""
        prompt = task.prompt + "\n\n===== recorder destilado (cheap: degraded) ===\n" + dest
    ok, out, ms = _run_opencode(prompt, cwd=cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    detail = det if ok else f"opencode failed: {out[:200]}"
    if not summary:
        detail = "(cheap degraded to recorder) " + detail
    return ArmResult("d", task.id, passed, u.input_tokens, u.output_tokens,
                     u.cache_read_tokens, ms, detail, seed)


# ---- matrix / report ---------------------------------------------------------

def emit_row(r: ArmResult, path: Path) -> None:
    """Append one row as JSONL immediately (abort-safe persistence)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "arm": r.arm, "task": r.task_id, "seed": r.seed,
            "passed": r.passed, "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out, "tokens_cache": r.tokens_cache,
            "latency_ms": r.latency_ms, "detail": r.detail,
        }) + "\n")


def summarize(rows: list[ArmResult]) -> str:
    if not rows:
        return "Fase 2: no rows."
    lines = [
        f"{'arm':<13}{'task':<13}{'#':>3}{'pass':>5}{'in':>7}{'out':>7}{'cache':>8}"
        f"{'warm':>8}{'cold':>8}{'rho%':>7}{'lat_s':>7}",
        "-" * 85,
    ]
    for r in sorted(rows, key=lambda x: (x.task_id, x.seed, x.arm)):
        warm = r.tokens_in + int(r.tokens_cache * 0.1)   # warm cache: hits 10% cost
        cold = r.tokens_in + r.tokens_cache               # cold cache: hits full
        rho = (r.tokens_cache / (r.tokens_in + r.tokens_cache) * 100.0
               if (r.tokens_in + r.tokens_cache) else 0.0)
        lat = r.latency_ms / 1000.0 if r.latency_ms else 0.0
        lines.append(
            f"{ARM_LABELS.get(r.arm, r.arm):<13}{r.task_id:<14}{r.seed:>3}"
            f"{str(r.passed):>5}"
            f"{r.tokens_in:>7}{r.tokens_out:>7}{r.tokens_cache:>8}"
            f"{warm:>8}{cold:>8}{rho:>6.0f}%{lat:>7.1f}"
        )
    lines.append("-" * 85)
    # per-arm aggregate means (cold mode, the economical gate metric)
    for arm in sorted({r.arm for r in rows}):
        rs = [r for r in rows if r.arm == arm]
        mean_in = sum(r.tokens_in for r in rs) / len(rs)
        mean_cache = sum(r.tokens_cache for r in rs) / len(rs)
        mean_cold = sum(r.tokens_in + r.tokens_cache for r in rs) / len(rs)
        mp = sum(1 for r in rs if r.passed)
        lines.append(
            f"{ARM_LABELS.get(arm, arm):<13}{'<mean>':<14}{len(rs):>3}"
            f"{f'{mp}/{len(rs)}':>7}{mean_in:>7.0f}{'-':>7}{mean_cache:>8.0f}"
            f"{'-':>8}{mean_cold:>8.0f}{'-':>7}{'-':>7}"
        )
    return "\n".join(lines)


async def run_matrix(tasks: list[Task], arms: list[str], seeds: int = 1,
                     results_path: Optional[Path] = None) -> list[ArmResult]:
    rows: list[ArmResult] = []
    # Execution order matters: arm (b) resumes and therefore MUTATES phase.cwd;
    # arms (a)/(c)/(d) clone phase.cwd at their own time, so they must run before
    # (b) or they would inherit its already-completed state. Reorder defensively.
    def _key(arm: str) -> int:
        return {"a": 0, "c": 1, "d": 2, "b": 3}.get(arm, 4)
    ordered = sorted(arms, key=_key)
    for task in tasks:
        for seed in range(seeds):
            phase = await _phase1(task)
            for arm in ordered:
                if arm == "a":
                    r = await arm_a(task, phase, seed)
                elif arm == "c":
                    r = await arm_c(task, seed)
                elif arm == "d":
                    r = await arm_d(task, seed)
                elif arm == "b":
                    r = await arm_b(task, phase, seed)
                rows.append(r)
                # Paper-grade persistence: emit each row the moment it lands so
                # an aborted run never loses already-measured arms (measured:
                # buffering until the end lost a full ~54-run gate on abort).
                if results_path is not None:
                    emit_row(r, results_path)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms", nargs="*", default=["a", "c"],
                    choices=["a", "b", "c", "d"],
                    help="arms to run (default: a c; cheap validation)")
    ap.add_argument("--tasks", nargs="*", default=["hello"],
                    help="task ids from TASKS")
    ap.add_argument("--seeds", type=int, default=1,
                    help="independent repetitions per task (fresh phase-1 each)")
    ap.add_argument("--results", type=Path, default=None,
                    help="JSONL path to append each completed row (abort-safe)")
    ap.add_argument("--list-tasks", action="store_true")
    ap.add_argument("--report", type=Path, default=None,
                    help="summarize an existing results JSONL without re-running")
    args = ap.parse_args(argv)

    if args.list_tasks:
        for t in TASKS:
            print(f"  {t.id:<12} {t.prompt[:60]}")
        return 0

    if args.report is not None:
        rows = []
        for line in open(args.report, encoding="utf-8"):
            if not line.strip():
                continue
            d = json.loads(line)
            d["task_id"] = d.pop("task")
            rows.append(ArmResult(**d))
        print(summarize(rows))
        return 0

    tasks = [t for t in TASKS if t.id in args.tasks]
    rows = asyncio.run(run_matrix(tasks, args.arms, seeds=args.seeds,
                                  results_path=args.results))
    print(summarize(rows))
    return 0


# ---- bundled coding tasks ----------------------------------------------------

TASKS: list[Task] = [
    Task(
        id="hello",
        prompt="Write a Python module hello.py with a function greet(name) that "
               "returns 'Hello, {name}!'. Then create test_hello.py that imports "
               "it and asserts greet('Witara') == 'Hello, Witara!'. Run the test.",
        verify_cmd="python3 test_hello.py -q",
        files={"hello.py": "def greet(name):\n    return 'Hello, {0}!'.format(name)\n",
               "test_hello.py": "from hello import greet\n"
                                 "assert greet('Witara') == 'Hello, Witara!'\n"
                                 "print('OK')\n"},
    ),
    Task(
        id="fib",
        prompt="Write a Python module fib.py with a function fib(n) that returns "
               "the n-th Fibonacci number (fib(0)=0, fib(1)=1). Then create "
               "test_fib.py that imports it and asserts fib(10)==55 and fib(20)==6765. "
               "Run the test.",
        verify_cmd="python3 test_fib.py -q",
        files={"fib.py": "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n",
               "test_fib.py": "from fib import fib\n"
                              "assert fib(10) == 55\n"
                              "assert fib(20) == 6765\n"
                              "print('OK')\n"},
    ),
    Task(
        id="countwords",
        prompt="Write a Python module countwords.py with a function count_words(text) "
               "that returns a dict mapping each word to its count (case-insensitive, "
               "split on whitespace). Then create test_countwords.py that imports it "
               "and asserts count_words('the cat and the dog') == {'the': 2, 'cat': 1, "
               "'and': 1, 'dog': 1}. Run the test.",
        verify_cmd="python3 test_countwords.py -q",
        files={"countwords.py": "def count_words(text):\n    from collections import Counter\n"
                                "    return dict(Counter(text.lower().split()))\n",
               "test_countwords.py": "from countwords import count_words\n"
                                     "assert count_words('the cat and the dog') == {'the': 2, 'cat': 1, 'and': 1, 'dog': 1}\n"
                                     "print('OK')\n"},
    ),
    Task(
        id="stats",
        prompt="Write a Python module stats.py with functions mean(nums), "
               "min_num(nums), max_num(nums). Then create test_stats.py that imports "
               "them and asserts mean([1,2,3,4])==2.5, min_num([3,1,2])==1, "
               "max_num([3,1,2])==3. Run the test.",
        verify_cmd="python3 test_stats.py -q",
        files={"stats.py": "def mean(nums):\n    return sum(nums) / len(nums)\n"
                           "def min_num(nums):\n    return min(nums)\n"
                           "def max_num(nums):\n    return max(nums)\n",
               "test_stats.py": "from stats import mean, min_num, max_num\n"
                                "assert mean([1,2,3,4]) == 2.5\n"
                                "assert min_num([3,1,2]) == 1\n"
                                "assert max_num([3,1,2]) == 3\n"
                                "print('OK')\n"},
    ),
    Task(
        id="palindrome",
        prompt="Write a Python module palindrome.py with a function "
               "is_palindrome(s) that returns True if s reads the same forward "
               "and backward (ignore case and spaces). Then create "
               "test_palindrome.py that imports it and asserts "
               "is_palindrome('A man a plan a canal Panama') is True and "
               "is_palindrome('hello') is False. Run the test.",
        verify_cmd="python3 test_palindrome.py -q",
        files={"palindrome.py": "def is_palindrome(s):\n"
                                "    t = ''.join(ch for ch in s.lower() if not ch.isspace())\n"
                                "    return t == t[::-1]\n",
               "test_palindrome.py": "from palindrome import is_palindrome\n"
                                     "assert is_palindrome('A man a plan a canal Panama') is True\n"
                                     "assert is_palindrome('hello') is False\n"
                                     "print('OK')\n"},
    ),
    Task(
        id="fizzbuzz",
        prompt="Write a Python module fizzbuzz.py with a function fizzbuzz(n) that "
               "returns a list of strings for 1..n: 'Fizz' for multiples of 3, "
               "'Buzz' for multiples of 5, 'FizzBuzz' for both, else the number "
               "as a string. Then create test_fizzbuzz.py that imports it and "
               "asserts fizzbuzz(15)[-3:] == ['13', '14', 'FizzBuzz']. Run the test.",
        verify_cmd="python3 test_fizzbuzz.py -q",
        files={"fizzbuzz.py": "def fizzbuzz(n):\n"
                              "    out = []\n"
                              "    for i in range(1, n + 1):\n"
                              "        s = ''\n"
                              "        if i % 3 == 0: s += 'Fizz'\n"
                              "        if i % 5 == 0: s += 'Buzz'\n"
                              "        out.append(s or str(i))\n"
                              "    return out\n",
               "test_fizzbuzz.py": "from fizzbuzz import fizzbuzz\n"
                                   "assert fizzbuzz(15)[-3:] == ['13', '14', 'FizzBuzz']\n"
                                   "print('OK')\n"},
    ),
]


if __name__ == "__main__":
    raise SystemExit(main())
