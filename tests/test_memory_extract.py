"""Tests for :mod:`lhassa.memory.extract`, the generic document text seam.

``.tex``/plain text decodes as UTF-8, a PDF (by name or magic bytes) goes through
pypdf, and nothing ever raises. A tiny valid PDF is built inline so no third-party
writer is needed.
"""

from lhassa.memory.extract import extract_text


# ── a tiny, valid PDF with extractable text (no third-party writer needed) ────
def _make_pdf(text: str) -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        None,  # content stream, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode() + b") Tj ET"
    objs[3] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 " + str(n).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    return bytes(out)


# ── extract_text ──────────────────────────────────────────────────────────────
def test_extract_text_reads_tex():
    data = "\\documentclass{article}\n\\begin{document}\nHola\n\\end{document}".encode()
    assert extract_text(data, filename="cv.tex") == data.decode("utf-8")


def test_extract_text_reads_pdf():
    out = extract_text(_make_pdf("Hello CV Test 42"), filename="cv.pdf")
    assert "Hello CV Test 42" in out


def test_extract_text_sniffs_pdf_without_extension():
    # No ``.pdf`` name, but the %PDF- magic bytes route it to the PDF extractor.
    out = extract_text(_make_pdf("Magic Sniffed"), filename="document")
    assert "Magic Sniffed" in out


def test_extract_text_empty_and_garbage_never_raise():
    assert extract_text(b"") == ""
    # A ``.pdf`` name over non-PDF bytes → pypdf fails → "" (never raises).
    assert extract_text(b"not a pdf at all", filename="x.pdf") == ""
