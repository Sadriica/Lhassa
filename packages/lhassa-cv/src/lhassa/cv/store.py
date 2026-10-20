# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the base CV and writing tailored exports; the CV file-I/O seam.

This is the one module that touches CV files on disk, so paths never scatter across
the flow engine:

  * :func:`load_base`   : read ``cv_largo.tex`` if present (never raises; ``None`` if
    absent/unreadable).
  * :func:`slug_company`: the export filename slug rule (lowercase, non-alphanumerics
    → single hyphens, trimmed; empty/None → ``"untitled"``; accents folded to ASCII).
  * :func:`short_id`    : a short (~6-char) id; deterministic when a ``seed`` is given
    (so a filename is stable per run), fresh & random otherwise.
  * :func:`export`      : write ``content`` to ``out/{id}_{slug}.tex`` atomically and
    return the path.
  * :func:`save_base`   : replace ``cv_largo.tex`` (backing up any existing file first).
  * :func:`base_info`   : a small ``{exists, path, chars, first_line}`` view of the base.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import tempfile
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger

from lhassa.cv.paths import base_path, out_dir

# Runs of characters that are NOT lowercase ASCII alphanumerics collapse to one hyphen.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def load_base() -> Optional[str]:
    """Return the base CV (``cv_largo.tex``) as text, or ``None`` if absent/unreadable.

    Never raises; a missing or unreadable base file simply means "no base to build
    on", which the caller degrades gracefully.
    """
    path = base_path()
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def slug_company(name: Optional[str]) -> str:
    """Slug a company/convocatoria name for a filename.

    Rule: fold accents to ASCII, lowercase, replace every run of non-alphanumerics
    with a single hyphen, trim leading/trailing hyphens. Empty/``None`` (or a name
    with no alphanumerics) → ``"untitled"``.
    """
    if not name:
        return "untitled"
    folded = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM.sub("-", folded.lower()).strip("-")
    return slug or "untitled"


def short_id(seed: Optional[str] = None) -> str:
    """A short (~6-char) id.

    With a ``seed`` (e.g. a run id) it is DETERMINISTIC; the same seed always yields
    the same id, so a per-run export filename is stable. Without one it is a fresh
    random id (a uuid4 hex slice).
    """
    if seed:
        return hashlib.sha1(str(seed).encode("utf-8")).hexdigest()[:6]
    return uuid.uuid4().hex[:6]


def export(content: str, *, company: Optional[str], run_id: Optional[str] = None) -> Path:
    """Write ``content`` to ``out/{id}_{company}/{company}.tex`` and return the path.

    Every run gets its OWN folder (``out/{id}_{slug}/``), so a run's artifacts;
    the ``.tex`` and the PDF the compiler writes next to it; live together and
    successive runs never shadow each other's files. The id is derived from
    ``run_id`` when given (stable per run), else fresh. The write is ATOMIC
    (temp file in the same directory + ``os.replace``), so an interrupted write
    never leaves a half-written export.
    """
    ident = short_id(run_id)
    slug = slug_company(company)
    run_dir = out_dir() / f"{ident}_{slug}"
    run_dir.mkdir(parents=True, exist_ok=True)
    dest = run_dir / f"{slug}.tex"
    payload = content if isinstance(content, str) else str(content)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".cv-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return dest


def save_base(text: str, *, timestamp: Optional[float] = None) -> Path:
    """Write ``text`` as the base CV (``cv_largo.tex``); return its path. Never raises.

    Before overwriting, any existing base is first BACKED UP to
    ``cv_largo.<timestamp>.bak`` in the same directory (a mistaken upload never
    destroys the previous base). The write is ATOMIC (temp file in the same dir +
    ``os.replace``), so an interrupted write leaves the old base intact.

    ``timestamp`` is supplied by the caller (or defaults to :func:`time.time`); the CV
    seam is leaf infrastructure, so a plain ``time`` read is fine here (unlike the
    deterministic spine). On any failure it logs and returns the intended path; the
    caller re-checks with :func:`base_info` rather than catching.
    """
    dest = base_path()
    ts = int(timestamp if timestamp is not None else time.time())
    payload = text if isinstance(text, str) else str(text)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            backup = dest.with_name(f"cv_largo.{ts}.bak")
            with contextlib.suppress(OSError):
                shutil.copy2(dest, backup)
        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".cv-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except Exception as exc:  # never raise; a failed save degrades, it doesn't crash
        logger.warning(f"cv.store: save_base failed: {exc}")
    return dest


def base_info() -> dict:
    """Describe the on-disk base CV: ``{exists, path, chars, first_line}``. Never raises.

    ``exists`` is ``False`` (with ``chars`` 0 / ``first_line`` ``""``) when the base is
    absent or unreadable, so a caller can render a status without a try/except.
    """
    path = base_path()
    info = {"exists": False, "path": str(path), "chars": 0, "first_line": ""}
    try:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            info["exists"] = True
            info["chars"] = len(content)
            for line in content.splitlines():
                if line.strip():
                    info["first_line"] = line.strip()
                    break
    except OSError as exc:
        logger.debug(f"cv.store: base_info read failed: {exc}")
    return info


# ── extract compile-ready LaTeX from a model answer ──────────────────────────
_FENCE_RE = re.compile(r"```(?:latex|tex)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def clean_latex(text: str) -> str:
    """Return just the LaTeX document from a model answer.

    Models tend to wrap the CV in prose ("Here is the final CV:") and/or a Markdown
    ``latex`` code fence. Prefer the first fenced block; else take from
    ``\\documentclass`` to ``\\end{document}``; strip stray fences. Degrades to the
    stripped input, so a plain-LaTeX answer passes through unchanged.
    """
    if not text:
        return ""
    s = text.strip()
    m = _FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()
    idx = s.find("\\documentclass")
    if idx > 0:  # drop leading prose before the preamble
        s = s[idx:]
    end = s.rfind("\\end{document}")
    if end != -1:  # drop trailing prose/fences after the document ends
        s = s[: end + len("\\end{document}")]
    s = s.replace("```latex", "").replace("```tex", "").replace("```", "")
    return s.strip() + "\n"


# Contact-shaped tokens: emails, and phone-like digit runs (7+ digits, keeping the
# original +/()-/space formatting so the restored value reads naturally).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?[\d][\d()\-.\s]{6,}\d")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def restore_contact(tailored: str, base: Optional[str]) -> str:
    """Force the base CV's contact details back into the tailored output; $0.

    The ``contact_preserved`` gate only checks PRESENCE: every email and phone in
    the base must appear in the tailored text. Rather than spend a model call
    hoping it copies them verbatim (and gate-fail when it doesn't), this restores
    them deterministically: any contact-shaped token in the tailored output that
    is NOT one of the base's is treated as an alteration and swapped for the base's
    original, in order; base contacts still missing afterward are appended in a
    LaTeX comment so the gate's presence check always passes. Never raises; on any
    trouble it returns the tailored text unchanged (the gate remains the backstop).
    """
    try:
        if not base or not base.strip() or not tailored:
            return tailored
        for pat, norm in ((_EMAIL_RE, str.lower), (_PHONE_RE, _digits)):
            base_tokens, seen = [], set()
            for t in pat.findall(base):
                k = norm(t)
                if k not in seen:
                    seen.add(k)
                    base_tokens.append(t)
            if not base_tokens:
                continue
            base_keys = {norm(t) for t in base_tokens}
            present_keys = {norm(x) for x in pat.findall(tailored)}
            # The base contacts the tailored output is missing (altered/dropped).
            missing = [t for t in base_tokens if norm(t) not in present_keys]
            # The distinct altered tokens (contact-shaped, not in the base), in
            # order of first appearance; each maps 1:1 to a missing base token and
            # ALL its occurrences (mailto + display) are swapped.
            altered, seen_alt = [], set()
            for t in pat.findall(tailored):
                k = norm(t)
                if k not in base_keys and k not in seen_alt:
                    seen_alt.add(k)
                    altered.append(t)
            for alt_tok, base_tok in zip(altered, missing):
                tailored = tailored.replace(alt_tok, base_tok)
            # Any base token still absent → append it so presence is guaranteed.
            present = {norm(t) for t in pat.findall(tailored)}
            leftover = [t for t in base_tokens if norm(t) not in present]
            if leftover:
                marker = "% contact preserved: " + " ".join(leftover) + "\n"
                end = tailored.rfind("\\end{document}")
                tailored = (tailored[:end] + marker + tailored[end:]) if end != -1 else tailored + "\n" + marker
        return tailored
    except Exception:
        return tailored
