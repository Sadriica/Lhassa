# SPDX-License-Identifier: AGPL-3.0-or-later
"""The "never keep" policy: what the ledger refuses to remember.

The ledger records that something happened, not always *what*. When an event
touches a path nobody should be able to read back out of an audit trail (SSH
keys, password stores, the owner's private Lhassa data, `.env` files), the
store keeps the event but replaces its payload: `target` becomes
`"<withheld>"` and `detail` shrinks to `{"withheld": true, "reason": ...}`.

Design rules:

1. **Fail closed.** If evaluating the policy raises for any reason, the event
   is withheld. A bug in the policy can lose detail; it cannot leak it.
2. **Additive configuration.** The built-in rules cannot be removed. A user
   file (path in `AGENT_LEDGER_NEVER_KEEP`) can only add rules. If that file
   exists but cannot be read, everything is withheld until it can.
3. **Stdlib only, fast.** The Claude Code hook runs this on every tool call.

Matching is on normalized paths: `~` and `$HOME` expanded, `..` collapsed and
symlinks resolved, and both the textual and the resolved form are tested (so
`~/.lhassa`, a symlink to a private data folder, is protected under either
name). A rule matches by directory prefix, by file-name glob, or (for the
Lhassa private folder) by path component. Shell commands are scanned token by
token, so `cat ~/.ssh/id_ed25519` is withheld too.

User file format, one rule per line, `#` comments:

    path: ~/work/secrets        a directory (prefix match); globs allowed
    name: *.pem                 a file-name glob
    host: some-host             do not keep anything from this host
    session: abc123             do not keep anything from this session
    ~/other/dir                 bare line with a slash or ~ is a path
    *.key                       any other bare line is a name glob
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

ENV_RULES_FILE = "AGENT_LEDGER_NEVER_KEEP"
WITHHELD_TARGET = "<withheld>"

REASON_POLICY_ERROR = "policy-error"
REASON_RULES_UNREADABLE = "user-rules-unreadable"

# (rule name, directory). Directory rules match the directory and everything
# under it.
DEFAULT_PATH_RULES: tuple[tuple[str, str], ...] = (
    ("ssh", "~/.ssh"),
    ("gnupg", "~/.gnupg"),
    ("lhassa-home", "~/.lhassa"),
    ("password-store", "~/.password-store"),
    ("gopass", "~/.local/share/gopass"),
    ("keepassxc", "~/.config/keepassxc"),
    ("bitwarden", "~/.config/Bitwarden"),
    ("1password", "~/.config/1Password"),
    ("keyrings", "~/.local/share/keyrings"),
)

# File-name globs, matched against the final path component.
DEFAULT_NAME_RULES: tuple[tuple[str, str], ...] = (
    ("dotenv", ".env"),
    ("dotenv-variant", ".env.*"),
    ("keepass-db", "*.kdbx"),
)

# A directory named `lhassa` with a `personal` component anywhere below it.
RULE_LHASSA_PERSONAL = "lhassa/personal"

_MAX_DEPTH = 24
_SPLIT_WS = re.compile(r"[\s'\"`;|&<>(),{}\[\]]+")
_SPLIT_SUB = re.compile(r"[=:]")
_GLOB_CHARS = re.compile(r"[*?\[]")


def _strings(value: Any, depth: int = 0) -> Iterator[str]:
    """Every string reachable in a detail payload (keys and values)."""
    if depth > _MAX_DEPTH:
        raise ValueError("detail nested too deeply to inspect")
    if isinstance(value, str):
        yield value
    elif isinstance(value, bytes):
        yield value.decode("utf-8", "replace")
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k, depth + 1)
            yield from _strings(v, depth + 1)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item, depth + 1)
    elif isinstance(value, os.PathLike):
        yield os.fspath(value)
    elif value is None or isinstance(value, (bool, int, float)):
        return
    else:
        yield str(value)


def _tokens(text: str) -> Iterator[str]:
    """The whole string, then each shell-ish token and its `a=b` / `a:b` parts."""
    yield text
    for tok in _SPLIT_WS.split(text):
        if not tok:
            continue
        yield tok
        if "=" in tok or ":" in tok:
            for part in _SPLIT_SUB.split(tok):
                if part:
                    yield part


def _ancestors(path: str) -> Iterator[str]:
    while True:
        yield path
        parent = os.path.dirname(path)
        if parent == path or not parent:
            return
        path = parent


@dataclass(frozen=True)
class _PathRule:
    name: str
    root: str          # as written, for the reason string
    is_glob: bool
    norm: str          # expanded + normalized
    real: str          # realpath of `norm`


class Policy:
    """Decides whether an event's payload may be kept. Cheap to build."""

    def __init__(
        self,
        *,
        home: Path | str | None = None,
        rules_file: Path | str | None = None,
        use_env: bool = True,
    ) -> None:
        self.home = str(home) if home is not None else os.path.expanduser("~")
        self._load_error = False
        self.hosts: set[str] = set()
        self.sessions: set[str] = set()
        self._path_rules: list[_PathRule] = []
        self._name_rules: list[tuple[str, str]] = list(DEFAULT_NAME_RULES)
        for name, root in DEFAULT_PATH_RULES:
            self._add_path(name, root)
        if rules_file is None and use_env:
            env = os.environ.get(ENV_RULES_FILE)
            rules_file = env if env else None
        if rules_file is not None:
            self._load_user_file(Path(self._expand(str(rules_file))))

    @classmethod
    def from_env(cls) -> "Policy":
        return cls()

    # -- configuration -------------------------------------------------------

    def _expand(self, text: str) -> str:
        if text == "~" or text.startswith("~/"):
            text = self.home + text[1:]
        if "$" in text:
            text = text.replace("${HOME}", self.home).replace("$HOME", self.home)
            text = os.path.expandvars(text)
        return text

    def _add_path(self, name: str, root: str) -> None:
        expanded = os.path.normpath(self._expand(root))
        self._path_rules.append(
            _PathRule(
                name=name,
                root=root,
                is_glob=bool(_GLOB_CHARS.search(expanded)),
                norm=expanded,
                real=os.path.realpath(expanded) if not _GLOB_CHARS.search(expanded) else expanded,
            )
        )

    def _load_user_file(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return  # no file, no extra rules
        except Exception:
            logger.warning("agent-ledger: cannot read %s; withholding everything", path)
            self._load_error = True
            return
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, rest = line.partition(":")
            key = key.strip().lower()
            rest = rest.strip()
            if sep and key in ("path", "name", "host", "session") and rest:
                kind, value = key, rest
            elif line.startswith("~") or "/" in line:
                kind, value = "path", line
            else:
                kind, value = "name", line
            if kind == "path":
                self._add_path(f"user:{value}", value)
            elif kind == "name":
                self._name_rules.append((f"user:{value}", value))
            elif kind == "host":
                self.hosts.add(value)
            else:
                self.sessions.add(value)

    # -- evaluation ----------------------------------------------------------

    def evaluate(self, event: Any) -> Optional[str]:
        """The reason to withhold this event, or None. May raise (the store
        treats a raise as "withhold")."""
        if self._load_error:
            return REASON_RULES_UNREADABLE
        if event.host in self.hosts:
            return f"host:{event.host}"
        if event.session and event.session in self.sessions:
            return f"session:{event.session}"
        detail = event.detail if isinstance(event.detail, dict) else {}
        cwd = detail.get("cwd")
        cwd = cwd if isinstance(cwd, str) and cwd.startswith("/") else ""
        seen: set[str] = set()
        texts = [event.target] if event.target else []
        texts.extend(_strings(detail))
        for text in texts:
            for tok in _tokens(text):
                if tok in seen:
                    continue
                seen.add(tok)
                reason = self._check_token(tok, cwd)
                if reason:
                    return reason
        return None

    def check_path(self, path: str, cwd: str = "") -> Optional[str]:
        """Convenience for callers that hold a single path."""
        if self._load_error:
            return REASON_RULES_UNREADABLE
        return self._check_token(path, cwd)

    def _check_token(self, tok: str, cwd: str) -> Optional[str]:
        if len(tok) > 4096:
            tok = tok[:4096]
        # File-name rules apply even to a bare name like `.env`.
        base = os.path.basename(tok.rstrip("/"))
        for name, pattern in self._name_rules:
            if fnmatch.fnmatchcase(base, pattern):
                return f"name:{pattern}"
        if not ("/" in tok or tok.startswith("~") or tok.startswith("$") or tok in (".", "..")):
            return None
        path = self._expand(tok)
        if not os.path.isabs(path):
            if not cwd:
                return None  # a relative path with no cwd cannot be placed
            path = os.path.join(cwd, path)
        norm = os.path.normpath(path)
        variants = {norm, os.path.realpath(norm)}
        for variant in variants:
            vbase = os.path.basename(variant)
            for name, pattern in self._name_rules:
                if fnmatch.fnmatchcase(vbase, pattern):
                    return f"name:{pattern}"
            parts = variant.split("/")
            for i, part in enumerate(parts):
                if part == "lhassa" and "personal" in parts[i + 1:]:
                    return f"path:{RULE_LHASSA_PERSONAL}"
            for rule in self._path_rules:
                if rule.is_glob:
                    if any(fnmatch.fnmatchcase(a, rule.norm) for a in _ancestors(variant)):
                        return f"path:{rule.root}"
                    continue
                for root in (rule.norm, rule.real):
                    if variant == root or variant.startswith(root.rstrip("/") + "/"):
                        return f"path:{rule.root}"
        return None
