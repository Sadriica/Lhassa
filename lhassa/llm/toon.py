# SPDX-License-Identifier: AGPL-3.0-or-later
"""TOON (Token-Oriented Object Notation) encoder. Wave 5, build-and-shelve.

**Deliberately UNWIRED.** Nothing in the repo calls this module today. The
house style for bulk-tabular data in a prompt is to hand-render compact lines
inline at the call site (see ``memory/graph.py``/``memory/graph_retrieve.py``
for examples of ad hoc compact rendering); there is currently no place that
puts a large uniform table of dicts into an LLM prompt, so there is nothing to
wire this into yet. It is built now so it is ready the day one shows up; the
natural first consumer would be a bulk graph-expansion result table out of
``memory/graph_retrieve.py`` (many ``{"text": ..., "source": ..., "via": ...}``
rows), where a JSON array repeats the same three key names on every element
and TOON's one-header-many-rows shape amortizes that repetition away. Do not
wire this in speculatively; wait for an actual multi-row payload that would
benefit, then reach for ``to_toon``.

Grammar (TOON v3 spirit, kept minimal; uniform arrays of objects only, no
nested arrays-of-objects support beyond JSON-encoding the cell):

    name[N]{field1,field2,...}:
      v1,v2,...
      v1,v2,...

- Line 1: array ``name``, row count ``N``, and the field list ONCE, in
  first-seen order across all rows (a non-uniform row set uses the union of
  every row's keys).
- One indented (two-space) CSV-style row per object thereafter, values in
  field order, missing keys rendered as an empty cell.

Cell encoding:

- ``None`` -> empty cell (indistinguishable from an actual empty string or a
  missing key on the way back; a known, documented lossy edge of a MINIMAL
  encoder, not a bug).
- ``bool`` -> ``true`` / ``false`` (also indistinguishable from the literal
  strings ``"true"``/``"false"`` on the way back).
- ``int`` / ``float`` -> ``str(value)`` verbatim.
- ``dict`` / ``list`` -> compact ``json.dumps`` (no spaces), then quoted like
  any other string cell (a JSON object/array almost always contains a comma).
- ``str`` (and the JSON text above) -> backslash-escaped (``\\\\`` ->
  ``\\\\\\\\``, real newline -> ``\\\\n``, real CR -> ``\\\\r``) so a row is
  always exactly one physical line, THEN double-quoted (with inner ``"``
  doubled) if the escaped text contains a comma, a quote, or leading/trailing
  whitespace.

:func:`from_toon` is a best-effort inverse for round-trip verification only;
it re-parses ints/floats/bools and JSON-decodes cells that look like a JSON
object/array, but genuinely cannot distinguish ``None`` from ``""``, or the
number/bool literals from their string spellings. That ambiguity is inherent
to a text format this small; do not build code that depends on the inverse
being exact for those cases.

Character count (``len(text)``) is used as the "token savings" proxy
throughout this module's tests; it is NOT a real tokenizer count, just a
cheap, deterministic stand-in that tracks token count closely enough for a
repetition-heavy tabular payload (repeated key names dominate both measures).
"""

from __future__ import annotations

import json
from typing import Any


def _escape_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def _unescape_text(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _encode_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, separators=(",", ":"))
    else:
        text = str(value)
    text = _escape_text(text)
    if "," in text or '"' in text or text != text.strip():
        text = '"' + text.replace('"', '""') + '"'
    return text


def _decode_cell(text: str) -> Any:
    if text == "":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if text and text[0] in "{[":
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            pass
    return text


def _parse_row(line: str) -> list[str]:
    cells: list[str] = []
    i = 0
    n = len(line)
    while True:
        if i < n and line[i] == '"':
            j = i + 1
            buf: list[str] = []
            while j < n:
                if line[j] == '"':
                    if j + 1 < n and line[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    j += 1
                    break
                buf.append(line[j])
                j += 1
            cells.append(_unescape_text("".join(buf)))
            i = j
            if i < n and line[i] == ",":
                i += 1
                continue
            break
        else:
            j = line.find(",", i)
            if j == -1:
                cells.append(_unescape_text(line[i:n]))
                break
            cells.append(_unescape_text(line[i:j]))
            i = j + 1
    return cells


def to_toon(rows: list[dict], *, name: str = "rows") -> str:
    """Encode ``rows`` (a list of flat dicts) as a minimal TOON table.

    Never raises: any row-level surprise (unserializable value, etc.) is
    rendered via ``str(value)`` rather than blowing up the whole encode.
    """
    if not rows:
        return f"{name}[0]{{}}:"

    fields: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)

    header = f"{name}[{len(rows)}]{{{','.join(fields)}}}:"
    lines = [header]
    for row in rows:
        try:
            cells = [_encode_cell(row.get(field)) for field in fields]
        except Exception:
            cells = [str(row.get(field, "")) for field in fields]
        lines.append("  " + ",".join(cells))
    return "\n".join(lines)


def from_toon(text: str) -> list[dict]:
    """Best-effort inverse of :func:`to_toon`: see module docstring for the
    documented lossiness (None/"" and bool/number-vs-string ambiguity).

    Never raises: malformed input degrades to ``[]``.
    """
    try:
        lines = text.split("\n")
        if not lines or not lines[0].strip():
            return []
        header = lines[0]
        brace_start = header.index("{")
        brace_end = header.index("}", brace_start)
        field_text = header[brace_start + 1 : brace_end]
        fields = [f for f in field_text.split(",") if f != ""] if field_text else []

        result = []
        for line in lines[1:]:
            if not line.strip():
                continue
            cells = _parse_row(line.strip())
            row = {}
            for idx, field in enumerate(fields):
                row[field] = _decode_cell(cells[idx]) if idx < len(cells) else None
            result.append(row)
        return result
    except Exception:
        return []
