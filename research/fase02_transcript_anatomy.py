# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fase 0.2; transcript re-derivability anatomy (read-only).

Deterministic parser over the owner's real harness transcripts. Maps every
transcript content block to (tool_result -> re-derivable from disk) vs
(text / thinking / tool_use.input -> not), then reports chars re-derivable
per harness and per project (task type).

Instrument doc: app/research/task-state-relay.md  ->  "Experiment: transcript
re-derivability (Fase 0.2, read-only)".

Harness backends:
  claude-code  ~/.claude/projects/**/*.jsonl        (content block schemas)
  codex        ~/.codex/sessions/**/*.jsonl         (response_item schema)
  opencode     ~/.local/share/opencode/opencode.db  (SQLite: part + message)

Classification (matches the note's instrument):
  tool_result / function_call_output / part.state.output   -> re-derivable
  tool_use.input / function_call.arguments                 -> not-rederiv
  assistant/user text / thinking / reasoning               -> not-rederiv
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

CLAUDE_HOME = Path.home() / ".claude" / "projects"
CODEX_HOME = Path.home() / ".codex" / "sessions"
OPEND_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


@dataclass
class Outline:
    rederivable: int = 0
    call_input: int = 0
    text: int = 0
    reasoning: int = 0

    def totals(self) -> int:
        return self.rederivable + self.call_input + self.text + self.reasoning

    def share(self) -> float:
        t = self.totals()
        return (self.rederivable / t * 100.0) if t else 0.0


@dataclass
class Run:
    harness: str
    project: str
    path: str
    outline: Outline = field(default_factory=Outline)


def _claude_blocks(p: Path) -> Iterator[tuple[str, str]]:
    try:
        for line in p.open(encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = d.get("message", {})
            content = m.get("content")
            if isinstance(content, str):
                yield ("text", content)
                continue
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                t = c.get("type")
                if t == "tool_result":
                    body = c.get("content")
                    if isinstance(body, list):
                        txt = "".join(
                            b.get("text", "") for b in body if isinstance(b, dict)
                        )
                    elif isinstance(body, str):
                        txt = body
                    else:
                        txt = ""
                    yield ("tool_result", txt)
                elif t == "tool_use":
                    try:
                        inp = json.dumps(c.get("input", {}), ensure_ascii=False)
                    except (TypeError, ValueError):
                        inp = ""
                    yield ("tool_use_input", inp)
                elif t == "thinking":
                    yield ("reasoning", c.get("thinking", "") or "")
                elif t == "text":
                    yield ("text", c.get("text", "") or "")
                else:
                    yield ("other", "")
    except OSError:
        pass


def _codex_blocks(p: Path) -> Iterator[tuple[str, str]]:
    try:
        for line in p.open(encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "response_item":
                continue
            ri = d.get("payload", {}) or {}
            t = ri.get("type")
            if t == "function_call_output":
                yield ("tool_result", ri.get("output", "") or "")
            elif t == "function_call":
                yield ("tool_use_input", str(ri.get("arguments", "") or ""))
            elif t == "reasoning":
                yield ("reasoning", str(ri.get("summary", "") or ""))
            elif t == "message":
                for cb in ri.get("content", []) or []:
                    if not isinstance(cb, dict):
                        continue
                    ctype = cb.get("type", "")
                    body = cb.get("text") or cb.get("content") or ""
                    if ctype in ("input_text", "output_text", "text"):
                        yield ("text", str(body))
                    else:
                        yield ("other", str(body))
    except OSError:
        pass


def _opencode_blocks(db: Path) -> Iterator[tuple[str, str]]:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT data FROM part")
        for (data,) in cur.fetchall():
            try:
                d = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                continue
            t = d.get("type")
            if t == "tool":
                st = d.get("state", {}) or {}
                out = st.get("output")
                if out is not None:
                    yield ("tool_result",
                           out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))
                inp = st.get("input")
                if inp is not None:
                    yield (
                        "tool_use_input",
                        inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False),
                    )
            elif t == "text":
                yield ("text", d.get("text", "") or "")
            elif t == "reasoning":
                yield ("reasoning", d.get("text", "") or "")
            elif t == "patch":
                yield ("other", json.dumps(d, ensure_ascii=False))
        conn.close()
    except (sqlite3.Error, OSError):
        pass


def _acc(run: Run, kind: str, text: str) -> None:
    o = run.outline
    ln = len(text or "")
    if kind == "tool_result":
        o.rederivable += ln
    elif kind == "tool_use_input":
        o.call_input += ln
    elif kind == "reasoning":
        o.reasoning += ln
    else:  # text, other
        o.text += ln


def _codex_cwd(p: Path) -> str:
    try:
        for line in p.open(encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") == "session_meta":
                return (d.get("payload", {}) or {}).get("cwd", "") or ""
    except OSError:
        pass
    return ""


def _proj_label(proj: str) -> str:
    p = proj.replace("-", "/").lower()
    if "code" in p or "repo" in p or "git" in p:
        return "code"
    if "uni" in p or "lucas" in p or "course" in p or "exercise" in p:
        return "uni/study"
    if "orca" in p or "workspaces" in p:
        return "workspaces"
    if "venta" in p or "sig" in p or "vinco" in p:
        return "domain/venta"
    return "other"


def collect() -> list[Run]:
    runs: list[Run] = []

    if CLAUDE_HOME.is_dir():
        for p in CLAUDE_HOME.rglob("*.jsonl"):
            run = Run("claude-code", _proj_label(p.parent.name), str(p))
            for kind, txt in _claude_blocks(p):
                _acc(run, kind, txt)
            if run.outline.totals() > 0:
                runs.append(run)

    if CODEX_HOME.is_dir():
        for p in sorted(CODEX_HOME.rglob("*.jsonl")):
            if "plugins" in str(p):
                continue
            run = Run("codex", _proj_label(_codex_cwd(p)), str(p))
            for kind, txt in _codex_blocks(p):
                _acc(run, kind, txt)
            if run.outline.totals() > 0:
                runs.append(run)

    if OPEND_PATH.is_file():
        run = Run("opencode", "opencode", str(OPEND_PATH))
        for kind, txt in _opencode_blocks(OPEND_PATH):
            _acc(run, kind, txt)
        if run.outline.totals() > 0:
            runs.append(run)

    return runs


def render(runs: list[Run]) -> None:
    if not runs:
        print("no transcripts found")
        return
    by_name: dict[str, list[Run]] = {}
    for r in runs:
        by_name.setdefault(r.harness, []).append(r)

    print(f"{'harness':<12}{'runs':>6}{'rederivable':>12}{'red%':>8}{'med%':>8}")
    print("-" * 46)
    for name, rs in by_name.items():
        tot_r = sum(r.outline.rederivable for r in rs)
        tot_a = sum(r.outline.totals() for r in rs)
        pct = (tot_r / tot_a * 100.0) if tot_a else 0.0
        shares = sorted(r.outline.share() for r in rs)
        med = shares[len(shares) // 2] if shares else 0.0
        print(f"{name:<12}{len(rs):>6}{tot_r:>12}{pct:>7.1f}%{med:>7.1f}%")

    print("\nnon-rederivable breakdown (chars, all runs):")
    for name, rs in by_name.items():
        ci = sum(r.outline.call_input for r in rs)
        tx = sum(r.outline.text for r in rs)
        rz = sum(r.outline.reasoning for r in rs)
        nt = ci + tx + rz
        if not nt:
            continue
        print(
            f"  {name:<12} tool_use.input {ci/nt*100:5.1f}%   "
            f"text {tx/nt*100:5.1f}%   thinking {rz/nt*100:5.1f}%   "
            f"(non-red chars {nt:,})"
        )

    print("\nby task type (aggregated):")
    by_proj: dict[str, Outline] = {}
    for r in runs:
        o = by_proj.setdefault(r.project, Outline())
        o.rederivable += r.outline.rederivable
        o.call_input += r.outline.call_input
        o.text += r.outline.text
        o.reasoning += r.outline.reasoning
    for proj, o in sorted(by_proj.items(), key=lambda kv: kv[1].totals(), reverse=True):
        print(
            f"  {proj:<20} red {o.rederivable/ o.totals()*100 if o.totals() else 0:6.1f}%  "
            f"chars {o.totals():>13,}"
        )

    print("\nby project (top 15 runs by rederivable share):")
    ranked = sorted(runs, key=lambda r: r.outline.share(), reverse=True)
    for r in ranked[:15]:
        print(
            f"  {r.harness:<12} {r.project:<20} {r.outline.share():>6.1f}% "
            f"(red={r.outline.rederivable:,}, total={r.outline.totals():,})"
        )


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    runs = collect()
    render(runs)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))