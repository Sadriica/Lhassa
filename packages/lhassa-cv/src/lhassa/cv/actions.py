# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cv pack's deterministic actions (registered by :mod:`lhassa.cv.pack`).

``compile_cv`` compiles the CV ``.tex`` to a PDF through the configured LaTeX engine.
Its allowlist policy row stays in :mod:`lhassa.security.allowlist` (a name only; with
the pack absent the action is simply not registered).
"""

from __future__ import annotations

from lhassa.cv.compile import compile_pdf
from lhassa.cv.paths import base_path


def compile_cv(params: dict) -> str:
    """Compile the CV .tex to PDF via the configured LaTeX engine.

    Uses ``cv.compile.compile_pdf``. The tex path comes from params or falls
    back to the CV seam default (``cv.paths.base_path()``). Returns the PDF
    path on success, or an explanatory message if the engine is missing / the
    compile fails. Never raises.

    params:
        tex_path ; path to a .tex file (default: ~/.lhassa/cv/cv_largo.tex).
    """
    try:
        tex_path_raw = params.get("tex_path") or None
        tex_path = str(tex_path_raw) if tex_path_raw else str(base_path())

        pdf = compile_pdf(tex_path)
        if pdf is None:
            return f"compile_cv: compile failed or engine not found for {tex_path}"
        return f"compile_cv: {pdf}"

    except Exception as exc:
        return f"compile_cv: error: {exc}"


__all__ = ["compile_cv"]
