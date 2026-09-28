# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 8B; portability, the REAL migration test (export/import, not lost session).

Fase 4's portability probe moved the worktree but kept the SAME HOME, so the
native resume could read its session from ~/.local/share/opencode/opencode.db.
That contaminated the test. But the honest clean-HOME test revealed something
more important: **native session state CAN travel**; `opencode export <id>`
serializes the session (~KB-scale JSON) and `opencode import` restores it in a
fresh HOME, after which `--continue` writes files as if nothing moved (measured
2026-08-06). A "lost session" migration is artificial: a real operator would
export.

So the portability argument is NOT "native resume dies on move, relay is the
only path"; it is "both can travel, at different COST and AGNOSTICITY":

  (b) export+import; the native session serialized (~full size) and restored.
  (c) destilado    ; the recorder destilado (compact) carried in the repo.

Both run from the MOVED worktree with a FRESH HOME (state travels only via the
serialized artifact, not the store). Verify is objective. The relay's edge, if
any, is token size (destilado << full session at scale) and harness-agnosticism
(opencode export is opencode-specific; the destilado is not).

Degrades, never raises.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from fase2_bench import Task, _make_worktree, _verify, recorder_destilado

REAL_HOME = Path(os.path.expanduser("~"))
OPENDATA = REAL_HOME / ".local" / "share" / "opencode"


def _fresh_home() -> Path:
    """A clean HOME B: empty XDG data dir + auth copied (credential, not state)."""
    tmp = Path(tempfile.mkdtemp(prefix="cleanhome_"))
    od = tmp / ".local" / "share" / "opencode"
    od.mkdir(parents=True)
    auth = OPENDATA / "auth.json"
    if auth.exists():
        shutil.copy(auth, od / "auth.json")
    return tmp


def _run_with_home(cmd: str, *, cwd: Path, home: Path,
                   timeout: float = 300.0) -> tuple[int, str, int]:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["XDG_STATE_HOME"] = str(home / ".local" / "state")
    start = time.monotonic()
    try:
        code = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                              shell=True, input=None, text=True,
                              env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "[timeout]", int(timeout * 1000)
    ms = int((time.monotonic() - start) * 1000)
    return code.returncode, (code.stdout or ""), ms


def _capture_session_id(out: str) -> str:
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("{") and "sessionID" in line:
            try:
                o = json.loads(line)
                if o.get("sessionID"):
                    return o["sessionID"]
            except json.JSONDecodeError:
                continue
    return ""


@dataclass
class Row:
    arm: str
    task_id: str
    passed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache: int = 0
    latency_ms: int = 0
    detail: str = ""


def emit_row(r: Row, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(r.__dict__) + "\n")


def _big_file_task(n_lines: int = 300) -> Task:
    """Small context-filler task with a unique flag (append a line)."""
    import random
    flag = "FLAG_%04d" % random.randint(0, 99999)
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


async def run_portability(n_reps: int, results: Path) -> list[Row]:
    rows: list[Row] = []
    for rep in range(n_reps):
        # phase-1 in HOME A (real): create a session with on-disk state.
        task = _big_file_task(n_lines=300)
        base = _make_worktree(task)
        subprocess.run(["git", "add", "-A"], cwd=str(base), check=False)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=str(base), check=False)
        setup_prompt = ("Read the file data.txt in full (every line). Then "
                        "create a file phase1_marker.txt containing the line "
                        "PHASE1-DONE. Print DONE.")
        cmd0 = " ".join(shlex.quote(a) for a in
                        ["opencode", "run", "--format", "json", "--auto", setup_prompt])
        rc, out, _ = _run_with_home(cmd0, cwd=base, home=REAL_HOME)
        sess = _capture_session_id(out)
        # Export the session (the serialized native state that travels).
        exp = " ".join(shlex.quote(a) for a in ["opencode", "export", sess])
        rc, sjson, _ = _run_with_home(exp, cwd=base, home=REAL_HOME)
        exported = sjson.strip() if rc == 0 else ""
        # MIGRATE: move the worktree (on-disk state travels) + fresh HOME B.
        new_path = base.with_name(base.name + "_moved")
        shutil.move(str(base), str(new_path))

        # (b) export+import+continue in a FRESH HOME B (native state travels
        # via the serialized session file). Separate HOME per arm.
        home_b = _fresh_home()
        b_task = _big_file_task(n_lines=300)
        detail_b = ""
        passed_b = False
        ms_b = 0
        if not sess or not exported:
            detail_b = "(no session to export) "
        else:
            sess_file = new_path / "session_export.json"
            sess_file.write_text(exported)
            imp = " ".join(shlex.quote(a) for a in
                           ["opencode", "import", str(sess_file)])
            rc, out_imp, ms_imp = _run_with_home(imp, cwd=new_path, home=home_b,
                                                 timeout=90)
            if rc != 0:
                detail_b = f"import failed rc={rc}: {out_imp[:120]}"
            else:
                cont = " ".join(shlex.quote(a) for a in
                                ["opencode", "run", "--format", "json", "--auto",
                                 "--continue", sess, b_task.prompt])
                rc, out_b, ms_b = _run_with_home(cont, cwd=new_path, home=home_b)
                passed_b, det_b = _verify(b_task, new_path)
                detail_b = "imported+continue" + (f": {det_b}" if det_b else "")
                if rc != 0:
                    detail_b = f"rc={rc} {out_b[:150]}"
            sess_file.unlink(missing_ok=True)
        rows.append(Row("b", b_task.id, passed_b, 0, 0, 0, ms_b, detail_b))
        emit_row(rows[-1], results)

        # (c) destilado in a SEPARATE fresh HOME B2 (cold start, no import
        # artifacts from arm (b) can pollute it).
        home_b2 = _fresh_home()
        c_task = _big_file_task(n_lines=300)
        c_prompt = c_task.prompt + "\n\n===== recorder destilado ===\n" \
                   + recorder_destilado(c_task)
        cmd_c = " ".join(shlex.quote(a) for a in
                         ["opencode", "run", "--format", "json", "--auto", c_prompt])
        rc, out_c, ms_c = _run_with_home(cmd_c, cwd=new_path, home=home_b2)
        passed_c, det_c = _verify(c_task, new_path)
        rows.append(Row("c", c_task.id, passed_c, 0, 0, 0, ms_c,
                        det_c if rc == 0 else f"rc={rc} {out_c[:150]}"))
        emit_row(rows[-1], results)
    return rows


def summarize(rows: list[Row]) -> str:
    lines = [f"{'arm':<5}{'pass':>5}{'lat_s':>8}  detail",
             "-" * 70]
    for r in sorted(rows, key=lambda x: (x.arm, x.latency_ms)):
        lat = r.latency_ms / 1000.0 if r.latency_ms else 0.0
        lines.append(f"{r.arm:<5}{str(r.passed):>5}{lat:>8.1f}  {r.detail[:110]}")
    lines.append("-" * 70)
    for arm in sorted({r.arm for r in rows}):
        rs = [r for r in rows if r.arm == arm]
        lines.append(f"{arm}: {sum(1 for r in rs if r.passed)}/{len(rs)} complete")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--results", type=Path, default=Path("results/fase8b_portability.jsonl"))
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
    rows = asyncio.run(run_portability(args.reps, args.results))
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())