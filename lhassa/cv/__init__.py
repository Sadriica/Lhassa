"""CV work-flow file-I/O seam — the fixed base CV in, a tailored ``.tex`` out.

The "Impossible-to-reject CV" work-flow builds on a fixed, long base CV
(``cv_largo.tex``) and exports one tailored ``.tex`` per run. This package is the
small, self-contained module that owns those files so paths never scatter across the
engine:

  * :mod:`lhassa.cv.config` — the one knob, ``LHASSA_CV_DIR`` (default ``~/.lhassa/cv``).
  * :mod:`lhassa.cv.paths`  — ``cv_dir`` / ``base_path`` / ``out_dir``.
  * :mod:`lhassa.cv.store`  — ``load_base`` / ``slug_company`` / ``short_id`` / ``export``.

Layer: leaf infrastructure used BY the flow engine — it depends on nothing but the
standard library and never imports an outer shell (see ``tests/test_architecture.py``).
"""

from lhassa.cv.paths import base_path, cv_dir, out_dir
from lhassa.cv.store import clean_latex, export, load_base, short_id, slug_company

__all__ = [
    "base_path",
    "cv_dir",
    "out_dir",
    "clean_latex",
    "export",
    "load_base",
    "short_id",
    "slug_company",
]
