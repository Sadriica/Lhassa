"""The cv pack's ``compile_cv`` action and its registration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_compile_cv_is_registered_by_the_pack():
    from lhassa.core.actions import ACTIONS
    from lhassa.cv.actions import compile_cv

    assert ACTIONS["compile_cv"] is compile_cv


def test_compile_cv_happy_path(tmp_path):
    pdf_path = tmp_path / "cv_largo.pdf"
    with patch("lhassa.cv.actions.compile_pdf", return_value=pdf_path), \
         patch("lhassa.cv.actions.base_path", return_value=tmp_path / "cv_largo.tex"):
        from lhassa.cv.actions import compile_cv
        result = compile_cv({})

    assert str(pdf_path) in result
    assert isinstance(result, str)


def test_compile_cv_engine_not_found():
    with patch("lhassa.cv.actions.compile_pdf", return_value=None), \
         patch("lhassa.cv.actions.base_path", return_value=Path("/tmp/cv.tex")):
        from lhassa.cv.actions import compile_cv
        result = compile_cv({})

    assert "failed" in result.lower() or "not found" in result.lower()
    assert isinstance(result, str)


def test_compile_cv_custom_path(tmp_path):
    tex = tmp_path / "custom.tex"
    pdf = tmp_path / "custom.pdf"
    with patch("lhassa.cv.actions.compile_pdf", return_value=pdf):
        from lhassa.cv.actions import compile_cv
        result = compile_cv({"tex_path": str(tex)})
    assert str(pdf) in result
    assert isinstance(result, str)
