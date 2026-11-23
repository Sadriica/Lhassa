# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hook adapters: git ``commit-msg`` / ``pre-push`` and Claude Code ``PreToolUse``.

Policy: warn and log; never block unless ``--strict`` (git hooks) or a rule the
owner listed in ``block_on`` fires critical (PreToolUse). Every PreToolUse
failure is fail-open: an internal error allows the call and is logged.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from lhassa.review import gitscan
from lhassa.review.log import record_run, scrub_url
from lhassa.review.rules import (
    Finding,
    ReviewConfig,
    at_least,
    check_identity,
    check_text,
    clean_message,
    load_config,
    path_ignored,
)

ZERO_SHA = "0" * 40
_GIT_CMD = re.compile(r"\bgit\s+((?:-[Cc]\s+\S+\s+)*)(commit|push)\b")
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_MAX_CONTEXT_LINES = 12


def strict_default() -> bool:
    return os.environ.get("LHASSA_REVIEW_STRICT", "").strip().lower() in ("1", "true", "yes")


def _report(findings: list[Finding], limit: int = 50) -> str:
    return "\n".join("lhassa review: " + f.render() for f in findings[:limit])


def message_findings(path: str, repo: str, cfg: ReviewConfig) -> list[Finding]:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    findings = check_text(clean_message(raw), subject="message", cfg=cfg,
                          kind="message", file=Path(path).name)
    for role, name, email in gitscan.current_identities(repo):
        findings += check_identity(role, name, email, cfg, "identity")
    return findings


def commit_msg_hook(path: str, repo: str, strict: bool | None = None) -> int:
    strict = strict_default() if strict is None else strict
    cfg = load_config()
    try:
        findings = message_findings(path, repo, cfg)
    except Exception as exc:
        record_run("git-hook", "commit-msg", [], error=type(exc).__name__, repo=repo)
        return 0
    record_run("git-hook", "commit-msg", findings, repo=repo, notes=cfg.notes)
    if findings:
        print(_report(findings))
    return 1 if strict and at_least(findings, "warn") else 0


def pre_push_hook(remote: str, url: str, stdin_text: str, repo: str,
                  strict: bool | None = None) -> int:
    strict = strict_default() if strict is None else strict
    cfg = load_config()
    findings: list[Finding] = []
    refs: list[dict] = []
    try:
        for line in stdin_text.splitlines():
            parts = line.split()
            if len(parts) != 4:
                continue
            local_ref, local_sha, remote_ref, remote_sha = parts
            refs.append({"local": local_ref, "remote": remote_ref,
                         "new_branch": remote_sha == ZERO_SHA,
                         "delete": local_sha == ZERO_SHA})
            if local_sha == ZERO_SHA:
                continue
            if remote_sha == ZERO_SHA:
                revs = [local_sha, "--not", f"--remotes={remote}"]
            else:
                revs = [f"{remote_sha}..{local_sha}"]
            findings += gitscan.review_commits(repo, revs, cfg)
    except Exception as exc:
        record_run("git-hook", "pre-push", findings, repo=repo, remote=remote,
                   url=scrub_url(url), refs=refs, error=type(exc).__name__)
        return 0
    record_run("git-hook", "pre-push", findings, repo=repo, remote=remote,
               url=scrub_url(url), refs=refs, notes=cfg.notes)
    if findings:
        print(_report(findings))
    return 1 if strict and at_least(findings, "warn") else 0


def _git_target(command: str) -> tuple[str, str] | None:
    """``(verb, dir)`` for the first ``git commit|push`` in a shell command."""
    m = _GIT_CMD.search(command)
    if not m:
        return None
    directory = ""
    opts = m.group(1).split()
    for i, tok in enumerate(opts):
        if tok == "-C" and i + 1 < len(opts):
            directory = opts[i + 1]
    return m.group(2), directory.strip("'\"")


def _upstream_revs(repo: str) -> list[str]:
    up = gitscan.git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                     check=False).strip()
    return [f"{up}..HEAD"] if up else ["HEAD", "--not", "--remotes"]


def _bash_findings(command: str, cwd: str, cfg: ReviewConfig) -> list[Finding]:
    target = _git_target(command)
    if target is None:
        return []
    verb, directory = target
    repo = directory if os.path.isabs(directory) else os.path.join(cwd, directory) if directory else cwd
    if verb == "commit":
        # The whole command text carries the message (-m, heredoc): scan it as one.
        findings = check_text(command, subject="commit-command", cfg=cfg, kind="message")
        for role, name, email in gitscan.current_identities(repo):
            findings += check_identity(role, name, email, cfg, "identity")
        staged = gitscan.git(repo, "diff", "--cached", "-U0", "--no-ext-diff", "--no-color",
                             check=False)
        return findings + gitscan.scan_added(staged, "staged", cfg)
    return gitscan.review_commits(repo, _upstream_revs(repo), cfg)


def _write_findings(tool: str, tin: dict, cfg: ReviewConfig) -> list[Finding]:
    path = str(tin.get("file_path") or tin.get("notebook_path") or "")
    if path and path_ignored(path, cfg):
        return []
    chunks: list[str] = []
    for key in ("content", "new_string", "new_source"):
        if isinstance(tin.get(key), str):
            chunks.append(tin[key])
    for edit in tin.get("edits") or []:
        if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
            chunks.append(edit["new_string"])
    out: list[Finding] = []
    for chunk in chunks:
        out += check_text(chunk, subject=tool, cfg=cfg, kind="content", file=path)
    return out


def pretooluse(stdin_text: str) -> tuple[dict | None, list[Finding]]:
    """Return ``(hook_json_or_None, findings)``. Raises on bad input; the caller
    turns any exception into fail-open."""
    payload = json.loads(stdin_text)
    if not isinstance(payload, dict):
        raise ValueError("payload is not an object")
    tool = str(payload.get("tool_name", ""))
    tin = payload.get("tool_input") or {}
    cwd = str(payload.get("cwd") or os.getcwd())
    cfg = load_config()
    findings: list[Finding] = []
    if tool == "Bash" and isinstance(tin.get("command"), str):
        findings = _bash_findings(tin["command"], cwd, cfg)
    elif tool in _WRITE_TOOLS and isinstance(tin, dict):
        findings = _write_findings(tool, tin, cfg)
    if not findings:
        return None, findings
    blocking = [f for f in findings if f.severity == "critical" and f.rule in cfg.block_on]
    if blocking:
        reason = "lhassa review blocked this call (policy: block_on):\n" + _report(blocking, 10)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}, findings
    # No permissionDecision on purpose: "allow" would skip the user's permission prompt.
    summary = _report(findings, _MAX_CONTEXT_LINES)
    return {"systemMessage": f"lhassa review: {len(findings)} finding(s)",
            "hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "additionalContext": summary}}, findings


def pretooluse_main(stdin_text: str) -> int:
    """Entry point for the Claude Code adapter. Always exits 0 (fail open)."""
    try:
        result, findings = pretooluse(stdin_text)
    except Exception as exc:
        record_run("claude-pretooluse", "error", [], error=type(exc).__name__,
                   outcome="allow-failopen")
        return 0
    denied = bool(result and result["hookSpecificOutput"].get("permissionDecision") == "deny")
    record_run("claude-pretooluse", "check", findings,
               outcome="deny" if denied else ("warn" if findings else "allow"))
    if result:
        print(json.dumps(result))
    return 0


__all__ = ["commit_msg_hook", "pre_push_hook", "pretooluse", "pretooluse_main",
           "message_findings"]
