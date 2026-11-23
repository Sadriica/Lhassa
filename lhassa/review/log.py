# SPDX-License-Identifier: AGPL-3.0-or-later
"""Append-only JSONL run log under ``~/.lhassa/review/`` (0600, never raises)."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from lhassa.review.rules import Finding, review_dir

LOG_NAME = "review.jsonl"
_USERINFO = re.compile(r"(://)[^/@\s]+@")


def log_path() -> Path:
    return review_dir() / LOG_NAME


def scrub_url(url: str) -> str:
    """Drop ``user:token@`` from a remote URL before it is logged."""
    return _USERINFO.sub(r"\1", url)


def record_run(source: str, event: str, findings: list[Finding], **extra: object) -> None:
    """Append one record. Any failure is swallowed: logging must never break a hook."""
    try:
        d = review_dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": source,
            "event": event,
            "counts": {s: sum(1 for f in findings if f.severity == s)
                       for s in ("info", "warn", "critical")},
            "findings": [f.to_dict() for f in findings[:200]],
            **extra,
        }
        line = json.dumps(entry, ensure_ascii=True) + "\n"
        fd = os.open(log_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, line.encode("ascii"))
        finally:
            os.close(fd)
    except Exception:
        pass
