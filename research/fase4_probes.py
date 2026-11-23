# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 4; the relay as the ONLY path (viability probes).

Two probes that re-frame the relay from "cheaper" (killed by warm cache in
Fase 2) to "the only way to keep working" (viability), the premise that
survived the gate:

  Probe 1; context saturation (post-limit): fill a session's context with a
  large file read, then switch to a small edit. The full transcript no longer
  fits / is absurdly expensive to re-send (arm a), native resume inherits a
  saturated window (arm b), the recorder destilado is compact (arm c). If only
  (c) completes, the relay is the post-limit path.

  Probe 2; portability (cwd-anchored native resume dies on move): native
  resume binds to the session's ORIGINAL cwd (measured in Fase 2 arm-b). Move
  the worktree to a new path so the original cwd is gone, then try to continue
  the session from the new path (arm b) vs. a cold destilado start there (arm c).
  If (b) fails and (c) completes, the relay is the portability path.

Both probes run the same task-state that Fase 2/3 already validated, reuse
fase2_bench's verify/recorder, and persist each arm's row abort-safe. They
degrade, never raise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fase2_bench import Task, _verify, recorder_destilado
from fase2_bench import _usage_from_opencode, _make_worktree


# ---- shared helpers ----------------------------------------------------------

def _run_probe_opencode(prompt: str, *, cwd: Path,
                        session: Optional[str] = None,
                        resume: bool = False,
                        timeout: float = 300.0,
                        stdin_input: Optional[str] = None) -> tuple[bool, str, int]:
    """Like fase2_bench._run_opencode but with a SHORT timeout, so a hung
    native-resume on a moved path (measured) returns instead of wedging the
    probe for 30 minutes.

    Shell=True: measured that opencode refuses file writes when invoked as an
    argv list (the Fase 2/3 invalidator); must go through the shell.
    ``stdin_input``: when the prompt is huge (saturated transcript), pass it on
    stdin (`opencode run -`) instead of argv; measured OSError ARG_MAX
    (Argument list too long) on a ~200k-token re-send; stdin has no such limit.
    """
    import shlex
    import shutil
    if shutil.which("opencode") is None:
        return False, "opencode CLI not installed", 0
    argv = ["opencode", "run", "--format", "json", "--auto"]
    if resume:
        argv += ["--continue", session or "last"]
    if stdin_input is not None:
        argv.append("-")
        payload = stdin_input
    else:
        argv.append(prompt)
        payload = None
    cmd = " ".join(shlex.quote(a) for a in argv)
    start = time.monotonic()
    try:
        code = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                              shell=True, input=payload,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "[timeout probe opencode]", int(timeout * 1000)
    ms = int((time.monotonic() - start) * 1000)
    return code.returncode == 0, (code.stdout or ""), ms


def _run_probe_claude(prompt: str, *, cwd: Path,
                      model: Optional[str] = None,
                      resume_last: bool = False,
                      timeout: float = 300.0) -> tuple[bool, str, int]:
    """Run claude headless (-p stream-json) in ``cwd``.

    Measured requirements for claude to actually WRITE files headless:
    --dangerously-skip-permissions is required; `-c` (continue last) resumes
    with tool-write capability while `--continue <id>` does NOT reliably. For
    the model-switch probe, --model picks the target body.
    """
    import shlex
    import shutil
    if shutil.which("claude") is None:
        return False, "claude CLI not installed", 0
    argv = ["claude", "-p", "--output-format=json", "--dangerously-skip-permissions"]
    if resume_last:
        argv.append("-c")
    if model:
        argv += ["--model", model]
    argv.append(prompt)
    cmd = " ".join(shlex.quote(a) for a in argv)
    start = time.monotonic()
    try:
        code = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                              shell=True, stdin=subprocess.DEVNULL,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "[timeout probe claude]", int(timeout * 1000)
    ms = int((time.monotonic() - start) * 1000)
    return code.returncode == 0, (code.stdout or ""), ms


def _usage_from_claude_stream(stdout: str) -> tuple[int, int, int]:
    """Sum claude's usage (input, cache_creation+cache_read, output).

    cache_creation counts as INPUT (the cold prefill of the reloaded context),
    the model-switch cost the probe measures; cache_read as cache hits.
    """
    tin = tout = tcache = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "result":
            continue
        u = obj.get("usage") or {}
        tin += int(u.get("input_tokens") or 0)
        tin += int(u.get("cache_creation_input_tokens") or 0)
        tcache += int(u.get("cache_read_input_tokens") or 0)
        tout += int(u.get("output_tokens") or 0)
    return tin, tout, tcache


@dataclass
class ProbeRow:
    probe: str
    arm: str
    task_id: str
    passed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache: int = 0
    latency_ms: int = 0
    detail: str = ""


def emit_row(r: ProbeRow, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(r.__dict__) + "\n")


def _big_file_task(n_lines: int = 11000, flag: Optional[str] = None) -> Task:
    """A task whose repo has a big file (context-filler) with a unique flag.

    The task: append the line ``FLAG_<value>`` to data.txt, verified by exact
    presence. Phase-1 reads the file in full to saturate context.

    Design notes (both measured):
    - An edit task ("change MARKER=0 to MARKER=1") on a filler file is
      hallucination-prone with a cheap model; it announced "the file already
      contains MARKER=1" when it did not, and skipped the edit.
    - The flag MUST be unique per invocation (a fresh random nonce when not
      given). With a fixed per-probe flag, opencode reused prior-context across
      bench runs that share the identical prompt and claimed the file already
      ended with the flag from an earlier run. An append of a never-seen nonce
      cannot be hallucinated or cache-reused as "already done".
    """
    import random
    flag = flag or ("FLAG_%04d" % random.randint(0, 99999))
    lines = ["line %06d: filler padding text to fill the model context ..." % i
             for i in range(n_lines)]
    data = "\n".join(lines) + "\n"
    verify = (
        "python3 - <<'PY'\n"
        "import sys\n"
        "d = open('data.txt').read()\n"
        f"sys.exit(0 if '\\n{flag}\\n' in d else 1)\n"
        "PY"
    )
    return Task(
        id="bigfile",
        prompt=f"Append the line {flag} to the END of data.txt. "
               "Do not modify anything else. data.txt already exists.",
        verify_cmd=verify,
        files={"data.txt": data},
    )


# ---- probe 1: context saturation --------------------------------------------

async def _saturate_and_phase(cwd: Path) -> str:
    """Fill context by reading the whole big file; return the phase session id."""
    ok, out, _ = _run_probe_opencode(
        "Read the file data.txt in full (every line, do not summarize). "
        "Then print the exact current MARKER value and say DONE.",
        cwd=cwd,
    )
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and "sessionID" in line:
            try:
                obj = json.loads(line)
                if obj.get("sessionID"):
                    return obj["sessionID"]
            except json.JSONDecodeError:
                continue
    return ""


async def probe_saturation(cwd: Path, task: Task, results: Path) -> list[ProbeRow]:
    rows = []
    phase_sess = await _saturate_and_phase(cwd)
    # arm (a): re-send the FULL prior context into a fresh session. The phase-1
    # session read the whole big file, so the realistic naive re-send embeds
    # that file's content as "prior transcript"; an enormous prompt that may
    # not even fit. This is the post-limit case the relay is built for.
    prior = (cwd / "data.txt").read_text() if (cwd / "data.txt").exists() else "filler "
    a_prompt = (task.prompt + "\n\n===== PRIOR CONTENT (the agent already read this) ===\n"
                + prior[:200000])
    # stdin_input: a saturated transcript exceeds ARG_MAX on argv (measured
    # OSError 'Argument list too long'); opencode reads the prompt on stdin.
    ok, out, ms = await asyncio.to_thread(
        _run_probe_opencode, "", cwd=cwd, stdin_input=a_prompt)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    rows.append(ProbeRow("saturation", "a", task.id, passed, u.input_tokens,
                         u.output_tokens, u.cache_read_tokens, ms,
                         det if ok else f"opencode failed: {out[:120]}"))
    emit_row(rows[-1], results)
    # arm (b): native resume of the saturated session.
    ok, out, ms = await asyncio.to_thread(
        _run_probe_opencode, task.prompt, cwd=cwd, session=phase_sess, resume=True)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, cwd)
    rows.append(ProbeRow("saturation", "b", task.id, passed, u.input_tokens,
                         u.output_tokens, u.cache_read_tokens, ms,
                         det if ok else f"opencode failed: {out[:120]}"))
    emit_row(rows[-1], results)
    # arm (c): cold start with only the recorder destilado.
    c_cwd = _make_worktree(task)
    c_prompt = task.prompt + "\n\n===== recorder destilado ===\n" \
                             + recorder_destilado(task)
    ok, out, ms = await asyncio.to_thread(_run_probe_opencode, c_prompt, cwd=c_cwd)
    u = _usage_from_opencode(out)
    passed, det = _verify(task, c_cwd)
    rows.append(ProbeRow("saturation", "c", task.id, passed, u.input_tokens,
                         u.output_tokens, u.cache_read_tokens, ms,
                         det if ok else f"opencode failed: {out[:120]}"))
    emit_row(rows[-1], results)
    return rows


# ---- probe 2: portability ---------------------------------------------------

async def probe_portability(base_cwd: Path, task: Task, results: Path) -> list[ProbeRow]:
    """Native resume is cwd-anchored; move the worktree away and try to resume.

    arm (b): continue the phase session from a MOVED path (original cwd gone).
    arm (c): cold destilado start in the moved path.
    """
    rows = []
    # phase-1 in base_cwd, then MOVE the whole worktree to a new path so the
    # session's ORIGINAL cwd is gone (a machine-migration/relocation).
    phase_sess = await _saturate_and_phase(base_cwd)
    new_path = base_cwd.with_name(base_cwd.name + "_moved")
    shutil.move(str(base_cwd), str(new_path))
    # arm (c): cold destilado start in the moved path. Each arm gets its OWN
    # task (unique flag) so opencode never reuses a prior run's session/cache
    # context; measured: shared prompts across bench runs made opencode claim
    # the append was already done. Run before (b) so a hung resume can't block
    # the independent cold-start measurement.
    c_task = _big_file_task(n_lines=300)
    c_prompt = c_task.prompt + "\n\n===== recorder destilado ===\n" \
                               + recorder_destilado(c_task)
    ok, out, ms = await asyncio.to_thread(_run_probe_opencode, c_prompt, cwd=new_path)
    u = _usage_from_opencode(out)
    passed, det = _verify(c_task, new_path)
    rows.append(ProbeRow("portability", "c", c_task.id, passed, u.input_tokens,
                         u.output_tokens, u.cache_read_tokens, ms,
                         det if ok else f"opencode failed: {out[:120]}"))
    emit_row(rows[-1], results)
    # arm (b): try to resume the phase session from the moved path.
    b_task = _big_file_task(n_lines=300)
    ok, out, ms = await asyncio.to_thread(
        _run_probe_opencode, b_task.prompt, cwd=new_path,
        session=phase_sess, resume=True)
    u = _usage_from_opencode(out)
    passed, det = _verify(b_task, new_path)
    rows.append(ProbeRow("portability", "b", b_task.id, passed, u.input_tokens,
                         u.output_tokens, u.cache_read_tokens, ms,
                         det if ok else f"opencode failed: {out[:120]}"))
    emit_row(rows[-1], results)
    return rows


# ---- report -----------------------------------------------------------------

def summarize(rows: list[ProbeRow]) -> str:
    lines = [
        f"{'probe':<12}{'arm':<4}{'task':<9}{'pass':>5}{'in':>8}{'out':>7}"
        f"{'cache':>9}{'cold':>9}{'lat_s':>7}",
        "-" * 70,
    ]
    for r in sorted(rows, key=lambda x: (x.probe, x.arm)):
        cold = r.tokens_in + r.tokens_cache
        lat = r.latency_ms / 1000.0 if r.latency_ms else 0.0
        lines.append(
            f"{r.probe:<12}{r.arm:<4}{r.task_id:<9}{str(r.passed):>5}"
            f"{r.tokens_in:>8}{r.tokens_out:>7}{r.tokens_cache:>9}{cold:>9}"
            f"{lat:>7.1f}"
        )
        if r.detail and not r.passed:
            lines.append(f"  {r.detail[:180]}")
    return "\n".join(lines)


# ---- probe 3: model switch in-harness (claude) ------------------------------

async def probe_model_switch(cwd: Path, task: Task, results: Path,
                             model_a: str = "sonnet",
                             model_b: str = "opus") -> list[ProbeRow]:
    """Cost of switching MODEL within one harness (claude).

    The reload question answered empirically: when you change model in Claude
    Code it re-ingests the context (cold). Measure the three ways to continue
    after switching model A->B:

      (a) re-send ; new session on B, full prior transcript re-sent
      (b) native  ; -c --model B (continue last session, model switched)
      (c) destilado; new session on B, only the recorder destilado

    All three must complete the same task (append unique flag to a file);
    tokens-in includes cache_creation (the reloaded-context prefill) so the
    'reload cost' is on the bill, per harness's own accounting.
    """
    rows = []
    # phase-1 in model A: set up, stop short. Capture the session for (b).
    setup = ("Set up this repo for the following task (do not complete it):\n"
             + task.prompt + "\nDo only initial setup and say DONE.")
    ok, out, _ = _run_probe_claude(setup, cwd=cwd, model=model_a)
    # (b) native switch: continue last session on model B.
    ok, out, ms = _run_probe_claude(task.prompt, cwd=cwd,
                                    model=model_b, resume_last=True)
    tin, tout, tc = _usage_from_claude_stream(out)
    passed, det = _verify(task, cwd)
    rows.append(ProbeRow("model_switch", "b", task.id, passed, tin, tout, tc, ms,
                         det if ok else f"claude failed: {out[:120]}"))
    emit_row(rows[-1], results)
    # (c) destilado: NEW session on B, only the recorder destilado.
    c_task = _big_file_task(n_lines=50)
    c_cwd = _make_worktree(c_task)
    c_prompt = c_task.prompt + "\n\n===== recorder destilado ===\n" \
                               + recorder_destilado(c_task)
    ok, out, ms = _run_probe_claude(c_prompt, cwd=c_cwd, model=model_b)
    tin, tout, tc = _usage_from_claude_stream(out)
    passed, det = _verify(c_task, c_cwd)
    rows.append(ProbeRow("model_switch", "c", c_task.id, passed, tin, tout, tc, ms,
                         det if ok else f"claude failed: {out[:120]}"))
    emit_row(rows[-1], results)
    # (a) re-send: NEW session on B, the full prior context re-sent (what the
    # operator's naive "reload" does). Prior context = the file phase-1 read.
    a_task = _big_file_task(n_lines=50)
    a_cwd = _make_worktree(a_task)
    prior = (a_cwd / "data.txt").read_text()
    a_prompt = (a_task.prompt + "\n\n===== PRIOR CONTENT (the agent read this) ===\n"
                + prior)
    ok, out, ms = _run_probe_claude(a_prompt, cwd=a_cwd, model=model_b)
    tin, tout, tc = _usage_from_claude_stream(out)
    passed, det = _verify(a_task, a_cwd)
    rows.append(ProbeRow("model_switch", "a", a_task.id, passed, tin, tout, tc, ms,
                         det if ok else f"claude failed: {out[:120]}"))
    emit_row(rows[-1], results)
    return rows


async def main_async(args) -> int:
    results = Path(args.results)
    rows = []
    if "saturation" in args.probes:
        try:
            task = _big_file_task()
            cwd = _make_worktree(task)
            rows += await probe_saturation(cwd, task, results)
        except Exception as exc:
            rows.append(ProbeRow("saturation", "ERR", "bigfile", False, 0, 0, 0,
                                 0, f"saturation probe failed: {exc}"))
            emit_row(rows[-1], results)
    if "portability" in args.probes:
        try:
            task = _big_file_task(n_lines=300)
            cwd = _make_worktree(task)
            subprocess.run(["git", "add", "-A"], cwd=str(cwd), check=False)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=str(cwd), check=False)
            rows += await probe_portability(cwd, task, results)
        except Exception as exc:
            rows.append(ProbeRow("portability", "ERR", "bigfile", False, 0, 0, 0,
                                 0, f"portability probe failed: {exc}"))
            emit_row(rows[-1], results)
    if "model_switch" in args.probes:
        try:
            task = _big_file_task(n_lines=50)
            cwd = _make_worktree(task)
            subprocess.run(["git", "add", "-A"], cwd=str(cwd), check=False)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=str(cwd), check=False)
            rows += await probe_model_switch(cwd, task, results)
        except Exception as exc:
            rows.append(ProbeRow("model_switch", "ERR", "bigfile", False, 0, 0, 0,
                                 0, f"model_switch probe failed: {exc}"))
            emit_row(rows[-1], results)
    print(summarize(rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probes", nargs="*", default=["saturation"],
                    choices=["saturation", "portability", "model_switch"])
    ap.add_argument("--results", type=Path,
                    default=Path("app/research/results/fase4_probes.jsonl"))
    args = ap.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
