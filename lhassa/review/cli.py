# SPDX-License-Identifier: AGPL-3.0-or-later
"""Command-line front end: ``python -m lhassa.review`` and ``lhassa review``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lhassa.review import gitscan, hooks
from lhassa.review.log import record_run
from lhassa.review.rules import (
    Finding,
    at_least,
    check_text,
    clean_message,
    load_config,
)


def _emit(findings: list[Finding], as_json: bool) -> None:
    if as_json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
    elif findings:
        for f in findings:
            print(f.render())
    else:
        print("review: no findings")


def _exit(findings: list[Finding], strict: bool, fail_on: str) -> int:
    return 1 if strict and at_least(findings, fail_on) else 0


def run_commit_range(rng: str, repo: str = ".", *, as_json: bool = False,
                     strict: bool = False, fail_on: str = "warn",
                     log: bool = True) -> int:
    cfg = load_config()
    try:
        findings = gitscan.review_commits(repo, [rng], cfg)
    except gitscan.ReviewError as exc:
        print(f"review: {exc}", file=sys.stderr)
        return 2
    if log:
        record_run("cli", "commit-range", findings, repo=repo, range=rng)
    _emit(findings, as_json)
    return _exit(findings, strict, fail_on)


def run_message(path: str, *, as_json: bool = False, strict: bool = False,
                fail_on: str = "warn", log: bool = True) -> int:
    return _run_text(path, "message", as_json, strict, fail_on, log)


def run_text(path: str, *, as_json: bool = False, strict: bool = False,
             fail_on: str = "warn", log: bool = True) -> int:
    return _run_text(path, "content", as_json, strict, fail_on, log)


def _run_text(path: str, kind: str, as_json: bool, strict: bool, fail_on: str,
              log: bool) -> int:
    cfg = load_config()
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"review: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if kind == "message":
        raw = clean_message(raw)
    findings = check_text(raw, subject=kind, cfg=cfg, kind=kind, file=Path(path).name)
    if log:
        record_run("cli", kind, findings, file=Path(path).name)
    _emit(findings, as_json)
    return _exit(findings, strict, fail_on)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lhassa review",
                                description="Deterministic pre-flight review (no LLM).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--json", action="store_true", dest="as_json")
        sp.add_argument("--strict", action="store_true",
                        help="exit 1 when findings reach --fail-on")
        sp.add_argument("--fail-on", choices=("warn", "critical"), default="warn")
        sp.add_argument("--no-log", action="store_true")

    cr = sub.add_parser("commit-range", help="review a git revision range")
    cr.add_argument("range")
    cr.add_argument("--repo", default=".")
    common(cr)
    for name in ("message", "text"):
        sp = sub.add_parser(name, help=f"review a {name} file")
        sp.add_argument("file")
        common(sp)

    hk = sub.add_parser("hook", help="hook entry points (used by scripts/hooks)")
    hsub = hk.add_subparsers(dest="hook", required=True)
    cm = hsub.add_parser("commit-msg")
    cm.add_argument("file")
    cm.add_argument("--repo", default=".")
    cm.add_argument("--strict", action="store_true")
    pp = hsub.add_parser("pre-push")
    pp.add_argument("remote", nargs="?", default="")
    pp.add_argument("url", nargs="?", default="")
    pp.add_argument("--repo", default=".")
    pp.add_argument("--strict", action="store_true")
    hsub.add_parser("pretooluse")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "commit-range":
        return run_commit_range(args.range, args.repo, as_json=args.as_json,
                                strict=args.strict, fail_on=args.fail_on,
                                log=not args.no_log)
    if args.cmd in ("message", "text"):
        fn = run_message if args.cmd == "message" else run_text
        return fn(args.file, as_json=args.as_json, strict=args.strict,
                  fail_on=args.fail_on, log=not args.no_log)
    # Hooks: never raise, and exit 0 unless strict says otherwise.
    try:
        if args.hook == "commit-msg":
            return hooks.commit_msg_hook(args.file, args.repo, args.strict or None)
        if args.hook == "pre-push":
            return hooks.pre_push_hook(args.remote, args.url, sys.stdin.read(), args.repo,
                                       args.strict or None)
        return hooks.pretooluse_main(sys.stdin.read())
    except Exception:
        return 0
