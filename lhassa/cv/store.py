"""Reading the base CV and writing tailored exports — the CV file-I/O seam.

This is the one module that touches CV files on disk, so paths never scatter across
the flow engine:

  * :func:`load_base`    — read ``cv_largo.tex`` if present (never raises; ``None`` if
    absent/unreadable).
  * :func:`slug_company` — the export filename slug rule (lowercase, non-alphanumerics
    → single hyphens, trimmed; empty/None → ``"untitled"``; accents folded to ASCII).
  * :func:`short_id`     — a short (~6-char) id; deterministic when a ``seed`` is given
    (so a filename is stable per run), fresh & random otherwise.
  * :func:`export`       — write ``content`` to ``out/{id}_{slug}.tex`` atomically and
    return the path.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
import unicodedata
import uuid
from pathlib import Path
from typing import Optional

from lhassa.cv.paths import base_path, out_dir

# Runs of characters that are NOT lowercase ASCII alphanumerics collapse to one hyphen.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def load_base() -> Optional[str]:
    """Return the base CV (``cv_largo.tex``) as text, or ``None`` if absent/unreadable.

    Never raises — a missing or unreadable base file simply means "no base to build
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

    With a ``seed`` (e.g. a run id) it is DETERMINISTIC — the same seed always yields
    the same id, so a per-run export filename is stable. Without one it is a fresh
    random id (a uuid4 hex slice).
    """
    if seed:
        return hashlib.sha1(str(seed).encode("utf-8")).hexdigest()[:6]
    return uuid.uuid4().hex[:6]


def export(content: str, *, company: Optional[str], run_id: Optional[str] = None) -> Path:
    """Write ``content`` to ``out/{id}_{slug_company(company)}.tex`` and return the path.

    The id is derived from ``run_id`` when given (stable per run), else fresh. The
    write is ATOMIC (temp file in the same directory + ``os.replace``), so an
    interrupted write never leaves a half-written export.
    """
    ident = short_id(run_id)
    dest = out_dir() / f"{ident}_{slug_company(company)}.tex"
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
