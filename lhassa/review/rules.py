# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review rules: pure functions over text and identities, no I/O but config.

Every finding carries a rule id, a severity (info/warn/critical), a subject (a
commit, ``message``, a path), an optional file and line, and a detail that never
echoes a secret or a configured personal name.

Personal data never lives here: allowed authors and forbidden names are read from
the owner's config file (``~/.lhassa/review/config.json`` by default).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lhassa.privacy import SECRET_PATTERN

SEVERITIES = ("info", "warn", "critical")
EM_DASH = chr(0x2014)  # built, not typed: the tree carries no em dash

_AI_NAMES = (
    r"claude|anthropic|copilot|chatgpt|openai|gpt-?\d|gemini|codex|cursor|aider|"
    r"devin|windsurf|cody|llm|\bai\b"
)
_CO_AUTHOR = re.compile(r"^\s*co-authored-by:\s*(.+)$", re.IGNORECASE)
_AI_NAME = re.compile(_AI_NAMES, re.IGNORECASE)
_AI_TRAILER = re.compile(
    r"^\s*(?:generated|assisted|authored|created|written)-by:", re.IGNORECASE
)
_AI_PHRASES = re.compile(
    r"generated\s+(?:with|by|using)\s+(?:\[?claude|claude\s+code|chatgpt|gpt|copilot|"
    r"an?\s+ai\b|ai\b|gemini|cursor|codex)"
    r"|written\s+(?:with\s+the\s+help\s+of|by)\s+(?:an?\s+)?(?:ai|claude|chatgpt)\b"
    r"|co-authored\s+with\s+(?:claude|ai|chatgpt|copilot)"
    r"|as\s+an\s+ai\s+(?:language\s+)?model"
    r"|claude\.com/claude-code"
    r"|\U0001F916\s*generated",
    re.IGNORECASE,
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")
_HEX_ONLY = re.compile(r"^[0-9a-fA-F]+$")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    subject: str
    detail: str
    file: str = ""
    line: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        where = self.subject
        if self.file:
            where += f" {self.file}" + (f":{self.line}" if self.line else "")
        elif self.line:
            where += f":{self.line}"
        return f"{self.severity:8} {self.rule:22} {where}  {self.detail}"


@dataclass
class ReviewConfig:
    #: fnmatch patterns over "name <email>" and over the bare email (lowercased).
    #: Empty means "do not check authorship against an expected identity".
    allowed_authors: tuple[str, ...] = ()
    #: Defaults name assistant senders only; GitHub's own web-merge committer
    #: (noreply@github.com) and the private users.noreply form are legitimate.
    unwanted_emails: tuple[str, ...] = ("noreply@anthropic.com", "noreply@openai.com")
    #: Personal names / nicknames that must not appear in public text. From the
    #: owner's config only; the repo ships none.
    forbidden_names: tuple[str, ...] = ()
    ignore_paths: tuple[str, ...] = ("*.lock", "package-lock.json", "uv.lock")
    emdash_ignore_paths: tuple[str, ...] = ()
    #: Regexes: a secret-shaped match that also matches one of these is ignored.
    secret_ignore: tuple[str, ...] = ()
    #: Rule ids whose critical findings may DENY a PreToolUse call. Empty by
    #: default: the hook only warns and logs until the owner opts a rule in.
    block_on: tuple[str, ...] = ()
    max_diff_bytes: int = 5_000_000
    notes: list[str] = field(default_factory=list)


_TUPLE_KEYS = (
    "allowed_authors", "unwanted_emails", "forbidden_names", "ignore_paths",
    "emdash_ignore_paths", "secret_ignore", "block_on",
)


def review_dir() -> Path:
    raw = os.environ.get("LHASSA_REVIEW_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".lhassa" / "review"


def config_path() -> Path:
    raw = os.environ.get("LHASSA_REVIEW_CONFIG", "").strip()
    return Path(raw).expanduser() if raw else review_dir() / "config.json"


def load_config(path: Path | None = None) -> ReviewConfig:
    """Read the owner's config; a missing or broken file degrades to defaults."""
    cfg = ReviewConfig()
    p = path or config_path()
    try:
        if not p.is_file():
            return cfg
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config root must be an object")
        for key in _TUPLE_KEYS:
            val = data.get(key)
            if isinstance(val, list):
                setattr(cfg, key, tuple(str(v) for v in val if str(v).strip()))
        if isinstance(data.get("max_diff_bytes"), int):
            cfg.max_diff_bytes = data["max_diff_bytes"]
    except Exception as exc:  # fail open: defaults, with a note for the log
        cfg.notes.append(f"config unreadable ({type(exc).__name__}); defaults used")
    return cfg


def _path_matches(path: str, patterns: tuple[str, ...]) -> bool:
    base = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(base, p) for p in patterns)


def path_ignored(path: str, cfg: ReviewConfig) -> bool:
    return _path_matches(path, cfg.ignore_paths)


def check_identity(
    role: str, name: str, email: str, cfg: ReviewConfig, subject: str
) -> list[Finding]:
    out: list[Finding] = []
    mail = email.strip().lower()
    full = f"{name.strip()} <{mail}>".lower()
    if any(fnmatch.fnmatch(mail, p.lower()) for p in cfg.unwanted_emails):
        out.append(Finding("author.unwanted-email", "warn", subject,
                           f"{role} email matches an unwanted pattern"))
    if cfg.allowed_authors and not any(
        fnmatch.fnmatch(full, p.lower()) or fnmatch.fnmatch(mail, p.lower())
        for p in cfg.allowed_authors
    ):
        out.append(Finding("author.unexpected", "warn", subject,
                           f"{role} is not in allowed_authors"))
    return out


def _secret_findings(line: str, cfg: ReviewConfig) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    if _PRIVATE_KEY.search(line):
        hits.append(("secret.private-key", "critical"))
    for m in SECRET_PATTERN.finditer(line):
        tok = m.group(0)
        if any(re.search(rx, tok) for rx in cfg.secret_ignore):
            continue
        if re.match(r"(?:AKIA|(?:sk|pk|rk|ghp|gho|ghs|ghu|ghr|xox[baprs])[-_])", tok):
            hits.append(("secret.prefixed", "critical"))
        elif len(tok) >= 32:
            # Long-token shape: skip hashes and identifiers; a real token mixes
            # upper, lower and digits.
            if _HEX_ONLY.match(tok):
                continue
            if not (re.search(r"[A-Z]", tok) and re.search(r"[a-z]", tok)
                    and re.search(r"\d", tok)):
                continue
            hits.append(("secret.long-token", "warn"))
    return hits


def check_text(
    text: str,
    *,
    subject: str,
    cfg: ReviewConfig,
    kind: str = "content",
    file: str = "",
    first_line: int = 1,
) -> list[Finding]:
    """Run the text rules. ``kind`` is ``message`` or ``content``; the AI
    attribution rules only apply to messages (a doc may legitimately name a
    provider)."""
    out: list[Finding] = []
    names = [
        re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)", re.IGNORECASE)
        for n in cfg.forbidden_names
    ]
    skip_dash = bool(file) and _path_matches(file, cfg.emdash_ignore_paths)
    for i, line in enumerate(text.split("\n")):
        no = first_line + i
        if len(line) > 20000:
            continue
        if EM_DASH in line and not skip_dash:
            out.append(Finding("emdash", "warn", subject, "em dash (U+2014)", file, no))
        for rule, sev in _secret_findings(line, cfg):
            out.append(Finding(rule, sev, subject, "secret-shaped value (not shown)", file, no))
        for rx in names:
            if rx.search(line):
                out.append(Finding("name.forbidden", "warn", subject,
                                   "a configured personal name appears (not shown)", file, no))
                break
        if kind == "message":
            m = _CO_AUTHOR.match(line)
            if m and _AI_NAME.search(m.group(1)):
                out.append(Finding("ai.trailer", "critical", subject,
                                   "Co-Authored-By trailer names an assistant", file, no))
            elif _AI_TRAILER.match(line):
                out.append(Finding("ai.trailer", "critical", subject,
                                   "AI attribution trailer", file, no))
            elif _AI_PHRASES.search(line):
                out.append(Finding("ai.mention", "critical", subject,
                                   "AI attribution phrase", file, no))
    return out


def clean_message(raw: str) -> str:
    """Apply git's default cleanup view: cut at the scissors line and blank the
    ``#`` comment lines, keeping line numbers stable."""
    lines: list[str] = []
    for line in raw.split("\n"):
        if line.startswith("# ------------------------ >8"):
            break
        lines.append("" if line.startswith("#") else line)
    return "\n".join(lines)


def worst(findings: list[Finding]) -> str:
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    return max((f.severity for f in findings), key=lambda s: rank.get(s, 0), default="")


def at_least(findings: list[Finding], level: str) -> bool:
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    return any(rank.get(f.severity, 0) >= rank.get(level, 1) for f in findings)
