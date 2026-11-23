# SPDX-License-Identifier: AGPL-3.0-or-later
"""Git plumbing for the review engine: read commits and diffs, never write."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from lhassa.review.rules import (
    Finding,
    ReviewConfig,
    check_identity,
    check_text,
    path_ignored,
)

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class ReviewError(Exception):
    """A git failure the caller should report (and, in a hook, swallow)."""


@dataclass
class CommitInfo:
    sha: str
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str
    message: str


def git(repo: str, *args: str, check: bool = True) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewError(f"git unavailable: {exc}") from exc
    if check and proc.returncode != 0:
        raise ReviewError(proc.stderr.strip() or f"git {args[0]} failed")
    return proc.stdout if proc.returncode == 0 else ""


def list_commits(repo: str, revs: list[str]) -> list[CommitInfo]:
    """Commits selected by ``revs`` (a range, or sha plus ``--not`` args), oldest first."""
    if any(r.startswith("-") and not r.startswith("--not") and not r.startswith("--remotes")
           for r in revs):
        raise ReviewError("unsupported option in revision arguments")
    fmt = "%x1e%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B"
    out = git(repo, "log", "--reverse", f"--format={fmt}", *revs, "--")
    commits: list[CommitInfo] = []
    for chunk in out.split("\x1e"):
        if not chunk.strip():
            continue
        parts = chunk.split("\x1f", 5)
        if len(parts) == 6:
            commits.append(CommitInfo(*parts))
    return commits


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """Parse a ``-U0`` unified diff into ``(path, line_number, text)`` for added lines."""
    out: list[tuple[str, int, str]] = []
    path = ""
    line_no = 0
    for raw in diff.split("\n"):
        if raw.startswith("+++ "):
            tgt = raw[4:].strip().strip('"')
            path = "" if tgt == "/dev/null" else (tgt[2:] if tgt.startswith("b/") else tgt)
        elif raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        elif raw.startswith("@@"):
            m = _HUNK.match(raw)
            line_no = int(m.group(1)) if m else 0
        elif raw.startswith("+") and path:
            out.append((path, line_no, raw[1:]))
            line_no += 1
    return out


def scan_added(
    diff: str, subject: str, cfg: ReviewConfig
) -> list[Finding]:
    if len(diff) > cfg.max_diff_bytes:
        return [Finding("scan.skipped", "info", subject, "diff too large; content not scanned")]
    out: list[Finding] = []
    for path, no, text in added_lines(diff):
        if path_ignored(path, cfg):
            continue
        out.extend(check_text(text, subject=subject, cfg=cfg, kind="content",
                              file=path, first_line=no))
    return out


def review_commits(repo: str, revs: list[str], cfg: ReviewConfig) -> list[Finding]:
    findings: list[Finding] = []
    for c in list_commits(repo, revs):
        subj = c.sha[:10]
        findings += check_identity("author", c.author_name, c.author_email, cfg, subj)
        if (c.committer_name, c.committer_email) != (c.author_name, c.author_email):
            findings += check_identity("committer", c.committer_name, c.committer_email, cfg, subj)
        findings += check_text(c.message, subject=subj, cfg=cfg, kind="message", file="message")
        diff = git(repo, "diff-tree", "-p", "--no-commit-id", "-r", "-U0", "--root",
                   "--no-ext-diff", "--no-color", c.sha, check=False)
        findings += scan_added(diff, subj, cfg)
    return findings


def current_identities(repo: str) -> list[tuple[str, str, str]]:
    """``(role, name, email)`` for the identities a commit made now would use."""
    out: list[tuple[str, str, str]] = []
    for role, var in (("author", "GIT_AUTHOR_IDENT"), ("committer", "GIT_COMMITTER_IDENT")):
        raw = git(repo, "var", var, check=False).strip()
        m = re.match(r"^(.*?) <([^>]*)>", raw)
        if m:
            out.append((role, m.group(1), m.group(2)))
    return out
