# SPDX-License-Identifier: AGPL-3.0-or-later
"""list_dir / read_file — deterministic file perception, $0, roots-bounded.

Reading a file or listing a directory needs NO model — these are the internal
hands for it, so the chat never has to spin up an AI CLI just to look at a
path. The security frontier is the SAME opt-in seam document perception uses
(:mod:`lhassa.memory.knowledge`): a path is only touchable when it resolves
INSIDE one of ``config.knowledge_roots``, and the hard ignore-list (dotfiles,
``.env*``, keys, tokens…) applies even inside a root — a secret-shaped name is
refused, never read. No roots configured → an honest refusal naming the knob.
Read-only, never raises, output capped for a chat bubble.
"""

from __future__ import annotations

from pathlib import Path

# Caps chosen for the Telegram surface: a listing/read is an answer, not a dump.
_MAX_ENTRIES = 100
_MAX_READ_CHARS = 4000
_MAX_FILE_BYTES = 512 * 1024


def _resolved_roots(cfg) -> list[Path]:
    """Knowledge roots PLUS protected roots — both readable by these actions.

    The protected tier's per-access approval gate lives in
    ``security.approval.needs_approval`` (checked at MINT time, before the task
    may run); by the time this action executes, a protected read has already
    been tapped through by the owner.
    """
    from lhassa.memory.knowledge import _resolve_root, _roots_from_config

    roots = []
    raw_protected = (getattr(cfg, "protected_roots", "") or "")
    extra = [r.strip() for r in raw_protected.split(",") if r.strip()]
    for raw in [*_roots_from_config(cfg), *extra]:
        resolved = _resolve_root(raw)
        if resolved is not None:
            roots.append(resolved)
    return roots


def _resolve_within_roots(raw_path: str, cfg) -> "tuple[Path, str] | tuple[None, str]":
    """Resolve ``raw_path`` and require it inside a knowledge root.

    Returns ``(path, "")`` on success or ``(None, reason)`` on refusal — the
    reason is an owner-facing sentence, never an exception.
    """
    import os

    roots = _resolved_roots(cfg)
    if not roots:
        return None, (
            "no knowledge roots are set — point LHASSA_KNOWLEDGE_ROOTS at the "
            "folder(s) I may read first."
        )
    try:
        path = Path(os.path.expanduser((raw_path or "").strip())).resolve()
    except Exception:
        return None, "that path can't be resolved."
    if not str(raw_path or "").strip():
        return None, "no path given — pass path=<file or folder>."
    inside = any(path == r or r in path.parents for r in roots)
    if not inside:
        return None, (
            f"{path} is outside your configured roots (knowledge + protected) — "
            "I only read inside them."
        )
    return path, ""


def _hard_ignored(path: Path, root_ceiling: list[Path]) -> bool:
    """True when the file/dir name — or ANY component under its root — is
    secret-shaped per the knowledge ignore-list."""
    from lhassa.memory.knowledge import _is_hard_ignored_dir, _is_hard_ignored_file

    if path.is_dir():
        if _is_hard_ignored_dir(path.name):
            return True
    elif _is_hard_ignored_file(path.name):
        return True
    for parent in path.parents:
        if any(parent == r for r in root_ceiling):
            break
        if _is_hard_ignored_dir(parent.name):
            return True
    return False


def list_dir(params: dict) -> str:
    """List a directory inside the knowledge roots. Never raises.

    params:
        path — directory to list (required).
        config — optional Config override (tests).
    """
    try:
        if not isinstance(params, dict):
            params = {}
        from lhassa.config import Config

        cfg = params.get("config") or Config
        path, reason = _resolve_within_roots(str(params.get("path", "")), cfg)
        if path is None:
            return f"list_dir: {reason}"
        if not path.is_dir():
            return f"list_dir: {path} is not a directory."
        roots = _resolved_roots(cfg)
        if _hard_ignored(path, roots):
            return f"list_dir: {path.name} is on the hard ignore-list."

        from lhassa.memory.knowledge import _is_hard_ignored_dir, _is_hard_ignored_file

        entries: list[str] = []
        hidden = 0
        for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.is_dir():
                if _is_hard_ignored_dir(child.name):
                    hidden += 1
                    continue
                entries.append(f"{child.name}/")
            else:
                if _is_hard_ignored_file(child.name):
                    hidden += 1
                    continue
                try:
                    size = child.stat().st_size
                except OSError:
                    size = -1
                entries.append(f"{child.name} ({size} B)" if size >= 0 else child.name)
            if len(entries) >= _MAX_ENTRIES:
                entries.append("… (truncated)")
                break
        note = f" ({hidden} secret-shaped entr{'y' if hidden == 1 else 'ies'} hidden)" if hidden else ""
        body = "\n".join(entries) if entries else "(empty)"
        return f"list_dir: {path}{note}\n{body}"
    except Exception as exc:
        return f"list_dir errored: {exc}"


def read_file(params: dict) -> str:
    """Read a text file inside the knowledge roots. Never raises.

    params:
        path — file to read (required).
        config — optional Config override (tests).
    """
    try:
        if not isinstance(params, dict):
            params = {}
        from lhassa.config import Config

        cfg = params.get("config") or Config
        path, reason = _resolve_within_roots(str(params.get("path", "")), cfg)
        if path is None:
            return f"read_file: {reason}"
        if not path.is_file():
            return f"read_file: {path} is not a file."
        roots = _resolved_roots(cfg)
        if _hard_ignored(path, roots):
            return f"read_file: {path.name} is on the hard ignore-list — I don't read secret-shaped files."
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                return f"read_file: {path.name} is larger than {_MAX_FILE_BYTES // 1024} KB — too big for a chat read."
        except OSError as exc:
            return f"read_file: can't stat {path.name}: {exc}"

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"read_file: can't read {path.name}: {exc}"
        if not text.strip():
            return f"read_file: {path.name} is empty."
        clipped = text[:_MAX_READ_CHARS]
        suffix = f"\n… (truncated at {_MAX_READ_CHARS} chars)" if len(text) > len(clipped) else ""
        return f"read_file: {path}\n{clipped}{suffix}"
    except Exception as exc:
        return f"read_file errored: {exc}"
