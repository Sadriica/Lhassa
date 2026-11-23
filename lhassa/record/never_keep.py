# SPDX-License-Identifier: AGPL-3.0-or-later
"""Paths whose names and contents are never written to a history log.

The rule is fail-closed: if a path cannot be evaluated, it is withheld. A
withheld event keeps the fact that something happened (kind, cost, timing) and
loses the what (the path and any text).

Matching works on the expanded, symlink-resolved path, by directory prefix or by
file-name pattern. ``~/.lhassa`` is a symlink to a private data folder, so the
resolved path is compared as well as the literal one. Extra rules can be added
with ``LHASSA_NEVER_KEEP_FILE`` (one path or glob per line) but the built-in
rules cannot be removed.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Iterable, Optional

WITHHELD = "<withheld>"

# Directory prefixes (a path inside any of these is withheld).
_DIR_RULES = (
    "~/.ssh",
    "~/.gnupg",
    "~/.lhassa",
    "~/.password-store",
    "~/.local/share/gopass",
    "~/.local/share/keyrings",
    "~/.config/keepassxc",
    "~/.config/Bitwarden",
    "~/.config/1Password",
)
# A ``personal`` directory directly under a ``lhassa`` directory.
_LHASSA_PERSONAL = ("lhassa", "personal")
# File-name globs.
_NAME_RULES = (".env", ".env.*", "*.kdbx", "*.kdb")


def _extra_rules() -> list[str]:
    path = os.getenv("LHASSA_NEVER_KEEP_FILE", "")
    if not path:
        return []
    try:
        lines = Path(path).expanduser().read_text().splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _resolved(raw: str) -> list[Path]:
    """The literal and the symlink-resolved forms of ``raw`` (both are checked)."""
    p = Path(os.path.expanduser(raw))
    forms = [Path(os.path.normpath(p))]
    try:
        forms.append(p.resolve())
    except (OSError, RuntimeError):
        pass
    return forms


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def withheld_rule(raw: str) -> Optional[str]:
    """The name of the rule that withholds ``raw``, or ``None`` if it may be kept.

    Fail-closed: any error while evaluating returns ``"unevaluable"``.
    """
    try:
        if not isinstance(raw, str) or not raw.strip():
            return None
        forms = _resolved(raw)
        dir_roots = [(r, _resolved(r)) for r in _DIR_RULES]
        for form in forms:
            for rule, roots in dir_roots:
                if any(_under(form, root) for root in roots):
                    return rule
            parts = form.parts
            for i in range(len(parts) - 1):
                if parts[i] == _LHASSA_PERSONAL[0] and parts[i + 1] == _LHASSA_PERSONAL[1]:
                    return "lhassa/personal"
            for pat in _NAME_RULES:
                if fnmatch.fnmatch(form.name, pat):
                    return pat
            for extra in _extra_rules():
                expanded = os.path.expanduser(extra)
                if fnmatch.fnmatch(str(form), expanded) or _under(form, Path(expanded)):
                    return f"user:{extra}"
        return None
    except Exception:
        return "unevaluable"


def first_withheld(values: Iterable[object]) -> Optional[str]:
    """The first rule that withholds any string in ``values``, else ``None``."""
    for v in values:
        if isinstance(v, str):
            rule = withheld_rule(v)
            if rule:
                return rule
    return None


_TOKEN_SPLIT = re.compile(r"[\s'\"`=,;:()<>|]+")


def withheld_rule_in_text(text: str) -> Optional[str]:
    """The first rule that withholds any path-like token inside free ``text``.

    A token counts as path-like when it holds a ``/``, starts with ``~`` or looks
    like a dotenv / keystore file name. Used for event text, where a path rarely
    stands alone (``cat ~/.ssh/id_rsa``). Fail-closed like :func:`withheld_rule`.
    """
    try:
        if not isinstance(text, str) or not text:
            return None
        for tok in _TOKEN_SPLIT.split(text):
            if not tok:
                continue
            if "/" in tok or tok.startswith("~") or tok.startswith(".env") or tok.endswith((".kdbx", ".kdb")):
                rule = withheld_rule(tok)
                if rule:
                    return rule
        return None
    except Exception:
        return "unevaluable"
