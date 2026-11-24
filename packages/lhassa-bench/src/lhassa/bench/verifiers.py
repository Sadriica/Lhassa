# SPDX-License-Identifier: AGPL-3.0-or-later
"""Objective verifiers for the task fixtures (classify, summarize, route,
policy, extract).

Same invariant as :mod:`lhassa.agents.verify`: pure standard library, no
model, no network. They plug into the shared registry through
``register_verifier`` so a fixture spec ``{"type": "label", ...}`` goes through
the very same ``make_verifier``/``verify`` seam as every other fixture.

Term syntax shared by ``facts`` and ``json_fields``: a term is a
case-insensitive substring; ``"a|b"`` means "a or b"; a term starting with
``re:`` is a case-insensitive regular expression.

Reasoning models may emit a ``<think>...</think>`` block; it is stripped
before scoring so a model is judged on its answer, not on its scratchpad.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from lhassa.agents.verify import VerifyResult, register_verifier

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Drop ``<think>`` blocks (and an unterminated trailing one)."""
    text = _THINK_RE.sub("", text or "")
    idx = text.lower().find("<think>")
    if idx != -1:
        text = text[:idx]
    return text.strip()


def _as_text(artifact: Any) -> str:
    return strip_reasoning(artifact if isinstance(artifact, str) else str(artifact))


def term_matches(term: str, haystack: str) -> bool:
    """True when ``term`` occurs in ``haystack`` (see module docstring)."""
    if term.startswith("re:"):
        return re.search(term[3:], haystack, re.IGNORECASE | re.DOTALL) is not None
    low = haystack.lower()
    return any(alt.strip().lower() in low for alt in term.split("|") if alt.strip())


# ── label ────────────────────────────────────────────────────────────────────

def normalize_label(text: str) -> str:
    """Lowercase, trim whitespace, wrapping quotes/backticks/asterisks, trailing dot."""
    t = _as_text(text).strip()
    t = t.strip("`*\"' \t\r\n")
    t = t.rstrip(".").strip("`*\"' ")
    return re.sub(r"\s+", "", t).lower()


class LabelVerifier:
    """The whole answer, normalised, must equal ``expected`` (a label).

    ``labels`` (optional) documents the closed set; an answer outside it fails
    with a distinct detail so "wrong label" and "not a label" are separable.
    """

    name = "label"

    def __init__(self, expected: str, labels: Optional[list] = None):
        self.expected = normalize_label(expected)
        self.labels = [normalize_label(x) for x in (labels or [])]

    def verify(self, artifact, context=None) -> VerifyResult:
        got = normalize_label(artifact)
        if got == self.expected:
            return VerifyResult(True, f"label={got}", 1.0)
        kind = "wrong label" if (not self.labels or got in self.labels) else "not a label"
        return VerifyResult(False, f"{kind}: got={got[:60]!r} want={self.expected!r}", 0.0)


# ── facts (summaries) ────────────────────────────────────────────────────────

class FactsVerifier:
    """Every required term present, no forbidden term, within a word budget."""

    name = "facts"

    def __init__(self, required: list, forbidden: Optional[list] = None,
                 max_words: Optional[int] = None):
        self.required = list(required)
        self.forbidden = list(forbidden or [])
        self.max_words = max_words

    def verify(self, artifact, context=None) -> VerifyResult:
        text = _as_text(artifact)
        missing = [t for t in self.required if not term_matches(t, text)]
        present_bad = [t for t in self.forbidden if term_matches(t, text)]
        words = len(text.split())
        too_long = self.max_words is not None and words > self.max_words
        total = len(self.required) or 1
        metric = (len(self.required) - len(missing)) / total
        problems = []
        if missing:
            problems.append(f"missing={missing}")
        if present_bad:
            problems.append(f"forbidden={present_bad}")
        if too_long:
            problems.append(f"words={words}>{self.max_words}")
        if problems:
            return VerifyResult(False, "; ".join(problems), metric)
        return VerifyResult(True, f"all {len(self.required)} facts, {words} words", 1.0)


# ── json_fields (extraction) ─────────────────────────────────────────────────

def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in ``text`` (fences tolerated)."""
    text = _as_text(text)
    m = _FENCE_RE.search(text)
    candidates = [m.group(1)] if m else []
    candidates.append(text)
    decoder = json.JSONDecoder()
    for cand in candidates:
        for i, ch in enumerate(cand):
            if ch in "{[":
                try:
                    obj, _ = decoder.raw_decode(cand[i:])
                    return obj
                except ValueError:
                    continue
    raise ValueError("no JSON value found")


def _get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


class JsonFieldsVerifier:
    """Validate a model's JSON answer against objective expectations.

    Spec keys (all optional except one of them being present):

    - ``required_paths``: dotted paths that must exist.
    - ``equals``: ``{path: value}``, strings compared case-insensitively.
    - ``lists``: ``{path: {min, max, groups}}``; ``groups`` is a list of
      groups, each a list of terms that must ALL appear in ONE single item
      of that list (an item is matched on its lowercased JSON text), so
      "owner and task keyword in the same to-do" is checkable.
    - ``forbidden``: terms that must not appear anywhere in the JSON text.
    """

    name = "json_fields"

    def __init__(self, required_paths: Optional[list] = None,
                 equals: Optional[dict] = None, lists: Optional[dict] = None,
                 forbidden: Optional[list] = None):
        self.required_paths = list(required_paths or [])
        self.equals = dict(equals or {})
        self.lists = dict(lists or {})
        self.forbidden = list(forbidden or [])

    def verify(self, artifact, context=None) -> VerifyResult:
        try:
            obj = extract_json(artifact)
        except ValueError as exc:
            return VerifyResult(False, f"invalid JSON: {exc}", 0.0)
        checks = fails = 0
        problems: list[str] = []

        def check(ok: bool, msg: str) -> None:
            nonlocal checks, fails
            checks += 1
            if not ok:
                fails += 1
                problems.append(msg)

        for path in self.required_paths:
            try:
                _get_path(obj, path)
                check(True, "")
            except KeyError:
                check(False, f"missing path {path}")
        for path, want in self.equals.items():
            try:
                got = _get_path(obj, path)
                same = (str(got).strip().lower() == str(want).strip().lower()
                        if isinstance(want, str) else got == want)
                check(same, f"{path}={got!r} want {want!r}")
            except KeyError:
                check(False, f"missing path {path}")
        for path, spec in self.lists.items():
            try:
                items = _get_path(obj, path)
            except KeyError:
                check(False, f"missing list {path}")
                continue
            if not isinstance(items, list):
                check(False, f"{path} is not a list")
                continue
            lo, hi = spec.get("min"), spec.get("max")
            if lo is not None:
                check(len(items) >= lo, f"{path} has {len(items)} < {lo} items")
            if hi is not None:
                check(len(items) <= hi, f"{path} has {len(items)} > {hi} items")
            blobs = [json.dumps(i, ensure_ascii=False).lower() for i in items]
            for group in spec.get("groups", []):
                ok = any(all(term_matches(t, b) for t in group) for b in blobs)
                check(ok, f"{path}: no single item has all of {group}")
        if self.forbidden:
            blob = json.dumps(obj, ensure_ascii=False).lower()
            for term in self.forbidden:
                check(not term_matches(term, blob), f"forbidden term {term!r} present")
        metric = (checks - fails) / checks if checks else 0.0
        if fails:
            return VerifyResult(False, "; ".join(problems)[:600], metric)
        return VerifyResult(True, f"{checks} checks passed", 1.0)


for _name, _cls in (("label", LabelVerifier), ("facts", FactsVerifier),
                    ("json_fields", JsonFieldsVerifier)):
    register_verifier(_name, _cls, override=True)
