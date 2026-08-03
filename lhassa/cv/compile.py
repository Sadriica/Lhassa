# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compile a tailored CV ``.tex`` into a PDF — the last mile of the CV seam.

The Telegram → CV bridge exports a tailored ``.tex``; the owner would rather receive
a ready-to-read **PDF**. This module is that one seam: it runs a LaTeX engine on a
``.tex`` and returns the produced ``.pdf`` (same directory, same stem).

The engine is ``tectonic`` by default (self-contained, non-interactive) — overridable
via ``LHASSA_TECTONIC_BIN`` and resolved at call time through :func:`shutil.which`, the
same env-first pattern the rest of the CV seam uses (see :mod:`lhassa.cv.paths`). A
``pdflatex``-style binary works too (the argv is chosen by the binary name).

Like the rest of the CV seam it is **leaf infrastructure** (standard library only) and
**never raises**: a missing engine, a non-zero exit, a timeout, or no PDF produced all
log a debug line and return ``None`` so the caller cleanly falls back to sending the
raw ``.tex`` instead of crashing the poller.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Union

from loguru import logger

# A LaTeX run is not instant (tectonic may fetch packages on a cold cache); give it a
# generous but bounded budget so a wedged engine can never hang the poller.
_TIMEOUT = 120.0


def _engine_bin() -> str:
    """The LaTeX engine binary name — ``LHASSA_TECTONIC_BIN`` or ``tectonic``.

    Resolved at call time (env-first, mirroring :func:`lhassa.cv.paths.cv_dir`) so a
    test or a freshly-set override is picked up without a reimport.
    """
    return (os.getenv("LHASSA_TECTONIC_BIN", "") or "").strip() or "tectonic"


def _argv(engine: str, src: Path, outdir: Path) -> list:
    """Build the non-interactive argv that writes the PDF next to ``src``.

    ``tectonic`` uses ``--outdir``; a ``pdflatex``-style engine uses
    ``-interaction=nonstopmode -output-directory``. The engine is picked by its binary
    name so an override to e.g. ``pdflatex`` still produces the right command line.
    """
    name = Path(engine).name.lower()
    if name.startswith("tectonic"):
        return [engine, "--outdir", str(outdir), str(src)]
    return [
        engine,
        "-interaction=nonstopmode",
        "-output-directory",
        str(outdir),
        str(src),
    ]


def compile_pdf(tex_path: Union[str, Path]) -> Optional[Path]:
    """Compile ``tex_path`` to a PDF and return its path, or ``None``. Never raises.

    Runs the LaTeX engine (:func:`_engine_bin`, default ``tectonic``) non-interactively,
    writing the PDF next to the ``.tex`` (same directory, same stem). Returns the ``.pdf``
    :class:`~pathlib.Path` on success. Returns ``None`` — logging a debug line — when the
    engine is not on PATH, the source is missing, the run errors/times out/exits non-zero,
    or no PDF is produced, so the caller can fall back to the raw ``.tex``.
    """
    src = Path(tex_path)
    try:
        if not src.is_file():
            logger.debug("cv.compile: no such .tex to compile: {}", src)
            return None
    except OSError as exc:
        logger.debug("cv.compile: could not stat {} ({})", src, exc)
        return None

    engine = _engine_bin()
    resolved = shutil.which(engine)
    if resolved is None:
        logger.debug("cv.compile: LaTeX engine {!r} not found on PATH", engine)
        return None

    outdir = src.parent
    pdf = outdir / (src.stem + ".pdf")
    argv = _argv(resolved, src, outdir)

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=_TIMEOUT,
            cwd=str(outdir),
        )
    except (subprocess.SubprocessError, OSError) as exc:  # timeout / spawn failure
        logger.debug("cv.compile: engine {!r} failed to run ({})", engine, exc)
        return None

    if proc.returncode != 0:
        tail = (getattr(proc, "stderr", b"") or b"")[-300:]
        logger.debug(
            "cv.compile: engine {!r} exited {} ({!r})", engine, proc.returncode, tail
        )
        return None

    if not pdf.is_file():
        logger.debug("cv.compile: engine {!r} produced no PDF at {}", engine, pdf)
        return None

    return pdf
