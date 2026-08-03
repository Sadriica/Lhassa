# SPDX-License-Identifier: AGPL-3.0-or-later
"""Path resolution for the CV seam — the single place CV file locations live.

Every CV path derives from one directory (``LHASSA_CV_DIR``, default ``~/.lhassa/cv``):

  * :func:`cv_dir`   — the base directory (``~/.lhassa/cv``).
  * :func:`base_path`— ``<dir>/cv_largo.tex`` (the fixed, long base CV).
  * :func:`out_dir`  — ``<dir>/out`` (per-run exports; created on demand).

The directory is resolved at CALL time (re-reading ``LHASSA_CV_DIR`` and falling back
to :attr:`CvConfig.cv_dir`) so a test or a freshly-set env var is picked up without a
reimport, mirroring how the flow store resolves ``config.flows_file``.
"""

from __future__ import annotations

import os
from pathlib import Path

from lhassa.cv.config import CvConfig


def cv_dir() -> Path:
    """The CV base directory, ``~/.lhassa/cv`` by default (``LHASSA_CV_DIR``)."""
    raw = (os.getenv("LHASSA_CV_DIR", "") or "").strip() or CvConfig.cv_dir
    return Path(raw).expanduser()


def base_path() -> Path:
    """``<cv_dir>/cv_largo.tex`` — the fixed base CV a run builds on."""
    return cv_dir() / "cv_largo.tex"


def out_dir() -> Path:
    """``<cv_dir>/out`` — where tailored exports land. Created on demand."""
    d = cv_dir() / "out"
    d.mkdir(parents=True, exist_ok=True)
    return d
