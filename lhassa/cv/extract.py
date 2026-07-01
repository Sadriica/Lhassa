"""Document text extraction — bytes in, plain text out, for the CV work-flow bridge.

The Telegram → CV bridge receives a CV as an uploaded document (a ``.tex`` source or
a ``.pdf`` export) and needs its text to feed the "Impossible-to-reject CV" work-flow
as the base CV. This module is that one seam: it turns raw ``bytes`` (plus the
original filename, used only to pick a decoder) into text.

  * ``.tex`` / ``.txt`` / ``.md`` (or anything not recognised as a PDF) — decoded as
    UTF-8 with ``errors="replace"`` so a stray byte never blows up.
  * ``.pdf`` — text extracted with :mod:`pypdf` (a lightweight, pure-Python dep),
    page by page.

Like the rest of the CV seam it is **leaf infrastructure**: it depends only on the
standard library plus ``pypdf`` and never imports an outer shell (see
``tests/test_architecture.py``). It **never raises** — on any failure it logs a debug
line and returns ``""`` so a bad upload degrades to "no base CV" instead of crashing
the poller.
"""

from __future__ import annotations

import io

from loguru import logger

# Magic bytes every PDF starts with. We sniff this (as well as the filename) so a PDF
# sent without a ``.pdf`` name is still routed to the PDF extractor.
_PDF_MAGIC = b"%PDF-"


def _looks_like_pdf(data: bytes, filename: str) -> bool:
    """True when ``data``/``filename`` indicate a PDF (magic bytes or ``.pdf`` name)."""
    if filename.lower().endswith(".pdf"):
        return True
    return data[:5] == _PDF_MAGIC


def _extract_pdf(data: bytes) -> str:
    """Extract text from PDF ``bytes`` via pypdf. Returns ``""`` on any failure."""
    try:
        from pypdf import PdfReader
    except Exception as exc:  # dependency missing / broken — degrade, never raise
        logger.debug("cv.extract: pypdf unavailable ({})", exc)
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception as exc:  # a single bad page must not lose the rest
                logger.debug("cv.extract: pdf page extract failed ({})", exc)
        return "\n".join(parts).strip()
    except Exception as exc:
        logger.debug("cv.extract: could not read pdf ({})", exc)
        return ""


def extract_text(data: bytes, *, filename: str = "") -> str:
    """Return the plain text of an uploaded document. Never raises.

    ``.tex`` / ``.txt`` / ``.md`` (and any non-PDF payload) are decoded as UTF-8 with
    ``errors="replace"``; a ``.pdf`` (by name or magic bytes) is extracted with
    :mod:`pypdf`. Any failure logs a debug line and yields ``""`` so the caller can
    treat "could not read" as "no base CV" without a try/except.
    """
    if not data:
        return ""
    name = (filename or "").strip()
    try:
        if _looks_like_pdf(data, name):
            return _extract_pdf(data)
        # Everything else (.tex/.txt/.md/unknown) is treated as UTF-8 text.
        return data.decode("utf-8", errors="replace")
    except Exception as exc:  # belt-and-braces — extraction never raises
        logger.debug("cv.extract: extract_text failed for {!r} ({})", name, exc)
        return ""
