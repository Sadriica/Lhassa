# SPDX-License-Identifier: AGPL-3.0-or-later
"""CV work-flow file-I/O seam; the fixed base CV in, a tailored ``.tex`` out.

The "Impossible-to-reject CV" work-flow builds on a fixed, long base CV
(``cv_largo.tex``) and exports one tailored ``.tex`` per run. This package is the
small, self-contained module that owns those files so paths never scatter across the
engine:

  * :mod:`lhassa.cv.config` : the one knob, ``LHASSA_CV_DIR`` (default ``~/.lhassa/cv``).
  * :mod:`lhassa.cv.paths`  : ``cv_dir`` / ``base_path`` / ``out_dir``.
  * :mod:`lhassa.cv.store`  : ``load_base`` / ``save_base`` / ``base_info`` /
    ``slug_company`` / ``short_id`` / ``export``.
  * ``extract_text`` (bytes to text for uploaded CVs), re-exported from the core
    :mod:`lhassa.memory.extract` seam.
  * :mod:`lhassa.cv.compile`: ``compile_pdf`` (a tailored ``.tex`` to a PDF to send).
  * :mod:`lhassa.cv.actions`: the ``compile_cv`` action.

Shipped by the ``lhassa-cv`` pack (:mod:`lhassa.cv.pack` registers it). Layer: leaf
file-I/O infrastructure; it never imports an outer shell (see
``tests/test_architecture.py``). The pack's shell slice lives in
:mod:`lhassa.cv.surfaces`.
"""

from lhassa.cv import knobs as _knobs  # noqa: F401  (declares Config.cv_flow_id/tectonic_bin)
from lhassa.cv.compile import compile_pdf
from lhassa.cv.paths import base_path, cv_dir, out_dir
from lhassa.cv.store import (
    base_info,
    clean_latex,
    export,
    load_base,
    restore_contact,
    save_base,
    short_id,
    slug_company,
)
from lhassa.memory.extract import extract_text

__all__ = [
    "base_info",
    "base_path",
    "compile_pdf",
    "cv_dir",
    "out_dir",
    "clean_latex",
    "export",
    "extract_text",
    "load_base",
    "restore_contact",
    "save_base",
    "short_id",
    "slug_company",
]
