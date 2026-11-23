# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 6; the cliff: where the relay becomes the only path (context scale).

Fase 4 showed the destilado is ~6x cheaper than re-send at ~130k-token context.
This probe finds the CLIFF: as the transcript grows, at what size does the
re-send stop COMPLETING (model context window exceeded / quality collapse)
while the destilado; fixed size; keeps passing? And, at that scale, does the
enriched destilado (decisions carried) finally beat the poor one (Fase 5 was
negative at small scale where re-derivation is cheap)?

Arms at each scale (all complete the same task: append a unique flag line):
  (a) re-send  ; fresh session, FULL prior content (the file) re-sent via stdin
  (b) resume   ; native --continue of the saturated phase-1 session
  (c) poor     ; cold start, recorder destilado (current)
  (d) rich     ; cold start, enriched destilado (decisions extracted + diff)

Scale = file size in tokens (~40k / ~120k / ~250k / ~400k). Verify is
objective (flag line present). Rows persisted abort-safe.
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

from fase2_bench import Task, _make_worktree, _verify, recorder_destilado
from fase2_bench import _usage_from_opencode
from fase4_probes import _run_probe_opencode
from fase5_enrich import enriched_destilado


def cliff_task(n_lines: int) -> Task:
    """Big-file task: append a unique flag; phase-1 reads the file in full to
    saturate context. Flag is random per invocation so no cache/prior-run
    contamination (measured failure mode)."""
    import random
    flag = "FLAG_%05d" % random.randint(0, 99999)
    lines = ["line %06d: filler padding text to fill the model context ..." % i
             for i in range(n_lines)]
    data = "\n".join(lines) + "\n"
    verify = (
        "python3 - <<'PY'\n"
        "import sys\n"
        f"d = open('data.txt').read()\n"
        f"sys.exit(0 if '\\n{flag}\\n' in d else 1)\n"
        "PY"
    )
    return Task(
        id=f"cliff{n_lines}",
        prompt=f"Append the line {flag} to the END of data.txt. "
               "Do not modify anything else. data.txt already exists.",
        verify_cmd=verify,
        files={"data.txt": data},
    )


@dataclass
class CliffRow:
    scale: str          # e.g. "40k"
    arm: str            # a/b/c/d
    passed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache: int = 0
    latency_ms: int = 0
    detail: str = ""


def emit_row(r: CliffRow, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(r.__dict__) + "\n")


async def _saturate(cwd: Path) -> str:
    """Read the file in full; return the session id."""
    ok, out, _ = _run_probe_opencode(
        "Read the file data.txt in full (every line, do not summarize). "
        "Then say DONE.", cwd=cwd, timeout=900.0)
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("{") and "sessionID" in line:
            try:
                obj = json.loads(line)
                if obj.get("sessionID"):
                    return obj["sessionID"]
            except json.JSONDecodeError:
                continue
    return ""


def _run_opencode_model(prompt: str, *, cwd: Path, model: str,
                        stdin_input: Optional[str] = None,
                        resume: bool = False,
                        session: Optional[str] = None,
                        timeout: float = 1500.0) -> tuple[bool, str, int]:
    """opencode run with an explicit --model (cross-provider cliff probe).

    Same shell=True discipline (argv-list refuses writes). Prompt via stdin
    when stdin_input given (huge re-sends would blow ARG_MAX). ``resume``
    continues the given session id (arm b) instead of starting fresh."""
    import shlex
    import shutil
    if shutil.which("opencode") is None:
        return False, "opencode CLI not installed", 0
    argv = ["opencode", "run", "--format", "json", "--auto", "--model", model]
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
        return False, "[timeout]", int(timeout * 1000)
    ms = int((time.monotonic() - start) * 1000)
    return code.returncode == 0, (code.stdout or ""), ms


async def run_scale(n_lines: int, results: Path, arms: list[str],
                    timeout: float = 1500.0,
                    model: Optional[str] = None) -> list[CliffRow]:
    rows = []
    task = cliff_task(n_lines)
    # scale label in approximate TOKENS of the file (line count ~ x40 chars ~ x10 tokens)
    scale = f"{int(n_lines*10/1000)}k"

    # (a) re-send: fresh session, full prior content via stdin. Uses its OWN
    # worktree (no saturation read needed) so huge scales don't pay a ~1M-token
    # saturation just to re-send.
    if "a" in arms:
        a_cwd = _make_worktree(task)
        prior = (a_cwd / "data.txt").read_text()
        a_prompt = task.prompt + "\n\n===== PRIOR CONTENT ===\n" + prior
        ok, out, ms = await asyncio.to_thread(
            _run_opencode_model, "", cwd=a_cwd, model=model, stdin_input=a_prompt, timeout=timeout)
        u = _usage_from_opencode(out)
        passed, det = _verify(task, a_cwd)
        rows.append(CliffRow(scale, "a", passed, u.input_tokens, u.output_tokens,
                             u.cache_read_tokens, ms,
                             det if ok else f"opencode: {out[:120]}"))
        emit_row(rows[-1], results)

    # (b) native resume of the saturated session (only if requested). This is
    # the corrected arm: the audit found the session was captured and DISCARDED
    # (no --continue), so "resume is linear" was built on a never-resuming arm.
    # Now it genuinely continues ``sess``.
    if "b" in arms:
        base = _make_worktree(task)
        sess = await _saturate(base)
        if not sess:
            rows.append(CliffRow(scale, "b", False, 0, 0, 0, 0,
                                 "no session id captured, cannot resume"))
            emit_row(rows[-1], results)
        else:
            ok, out, ms = await asyncio.to_thread(
                _run_opencode_model, task.prompt, cwd=base, model=model,
                resume=True, session=sess, timeout=timeout)
            u = _usage_from_opencode(out)
            passed, det = _verify(task, base)
            rows.append(CliffRow(scale, "b", passed, u.input_tokens, u.output_tokens,
                                 u.cache_read_tokens, ms,
                                 det if ok else f"opencode: {out[:120]}"))
            emit_row(rows[-1], results)

    # (c) poor destilado (current recorder), cold.
    if "c" in arms:
        c_cwd = _make_worktree(task)
        c_prompt = task.prompt + "\n\n===== recorder destilado ===\n" + recorder_destilado(task)
        ok, out, ms = await asyncio.to_thread(_run_opencode_model, c_prompt, cwd=c_cwd, model=model, timeout=timeout)
        u = _usage_from_opencode(out)
        passed, det = _verify(task, c_cwd)
        rows.append(CliffRow(scale, "c", passed, u.input_tokens, u.output_tokens,
                             u.cache_read_tokens, ms,
                             det if ok else f"opencode: {out[:120]}"))
        emit_row(rows[-1], results)

    # (d) rich destilado (enriched with the phase-1 transcript decisions), cold.
    if "d" in arms:
        d_cwd = _make_worktree(task)
        d_prompt = task.prompt + "\n\n===== enriched destilado ===\n" \
                  + enriched_destilado(task, "", d_cwd)
        ok, out, ms = await asyncio.to_thread(_run_opencode_model, d_prompt, cwd=d_cwd, model=model, timeout=timeout)
        u = _usage_from_opencode(out)
        passed, det = _verify(task, d_cwd)
        rows.append(CliffRow(scale, "d", passed, u.input_tokens, u.output_tokens,
                             u.cache_read_tokens, ms,
                             det if ok else f"opencode: {out[:120]}"))
        emit_row(rows[-1], results)
    return rows


def summarize(rows: list[CliffRow]) -> str:
    lines = [
        f"{'scale':<8}{'arm':<4}{'pass':>5}{'in':>9}{'cache':>9}{'cold':>9}{'lat_s':>7}",
        "-" * 55,
    ]
    for r in sorted(rows, key=lambda x: (x.scale, x.arm)):
        cold = r.tokens_in + r.tokens_cache
        lat = r.latency_ms / 1000.0 if r.latency_ms else 0.0
        lines.append(f"{r.scale:<8}{r.arm:<4}{str(r.passed):>5}{r.tokens_in:>9}"
                     f"{r.tokens_cache:>9}{cold:>9}{lat:>7.1f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scales", nargs="*", default=["40k", "120k"],
                    choices=["40k", "120k", "250k", "400k", "950k"])
    ap.add_argument("--arms", nargs="*", default=["a", "b", "c", "d"],
                    choices=["a", "b", "c", "d"],
                    help="arms to run per scale (b needs a saturation read, skip at huge scales)")
    ap.add_argument("--timeout", type=float, default=1500.0,
                    help="per-run subprocess timeout seconds (huge scales need more)")
    ap.add_argument("--model", default="opencode/deepseek-v4-flash-free",
                    help="provider/model to test (default = the free default; "
                         "the paid alias 'opencode/deepseek-v4-flash' hits a "
                         "billing-wall CreditsError on this box)")
    ap.add_argument("--results", type=Path,
                    default=Path("app/research/results/fase6_cliff.jsonl"))
    args = ap.parse_args(argv)
    n = {"40k": 3600, "120k": 11000, "250k": 23000, "400k": 37000, "950k": 88000}
    async def _main():
        rows = []
        for s in args.scales:
            try:
                rows += await run_scale(n[s], args.results, args.arms,
                                        timeout=args.timeout, model=args.model)
            except Exception as exc:
                rows.append(CliffRow(s, "ERR", False, 0, 0, 0, 0, str(exc)))
                emit_row(rows[-1], args.results)
        print(summarize(rows))
        return 0
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
