# SPDX-License-Identifier: AGPL-3.0-or-later
"""Leaf configuration for the CV file-I/O seam.

Follows the codebase's class-level config convention: values are resolved from the
environment at import time, never crash on import, and expose a single knob. The CV
directory holds the fixed base CV (``cv_largo.tex``) and the per-run exports (``out/``).

Kept as its own tiny config class (rather than a field on the global :class:`Config`)
so the CV seam stays self-contained leaf infrastructure — it depends on nothing but
the standard library.
"""

from __future__ import annotations

import os
from pathlib import Path

# The default lives next to the rest of Lhassa's local state under ``~/.lhassa``.
_DEFAULT_CV_DIR = str(Path.home() / ".lhassa" / "cv")


class CvConfig:
    """Configuration for the CV work-flow directory.

    ``cv_dir`` is driven by ``LHASSA_CV_DIR`` (default ``~/.lhassa/cv``). Path
    resolution in :mod:`lhassa.cv.paths` re-reads the environment at call time so a
    test (or a freshly-set env var) takes effect without reimporting this module,
    falling back to this class attribute otherwise.
    """

    cv_dir: str = os.getenv("LHASSA_CV_DIR", "") or _DEFAULT_CV_DIR
