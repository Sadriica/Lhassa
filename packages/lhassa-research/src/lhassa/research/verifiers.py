# SPDX-License-Identifier: AGPL-3.0-or-later
"""The research line's objective gates: ``research_corpus`` and ``idea_assessment``.

Same contract as every built-in in :mod:`lhassa.agents.verify`: objective, no
model, no provider import. They register into the shared ``VERIFIERS``
registry from :mod:`lhassa.research.pack`, so a flow gate spec
``{"type": "research_corpus"}`` resolves exactly as before when the pack is
installed, and is simply unknown when it is not.
"""

from __future__ import annotations

import re

from lhassa.agents.verify import VerifyResult

# ── research corpus (structure + citation discipline, never source judgment) ──

_RESEARCH_HEADINGS = (
    "The idea in one sentence",
    "Mechanics - down to the base",
    "Evidence the authors present",
    "Sources",
    "Mapping to Lhassa",
    "Not yet verified by us - and the experiment that would",
    "Open questions",
)
_SLOP_PHRASES = (
    "in today's rapidly evolving",
    "it is important to note",
    "delve into",
    "game-changer",
    "revolutionary",
    "unlock the power",
)


def _heading_pattern(heading: str) -> str:
    escaped = re.escape(heading)
    # Corpus prose uses typographic dashes while prompts/config stay ASCII.
    escaped = escaped.replace(r"\ \-\ ", "\\s*[-\u2013\u2014]\\s*")
    return rf"(?im)^##\s+{escaped}\s*$"


def _section(text: str, heading: str) -> str:
    match = re.search(_heading_pattern(heading), text)
    if not match:
        return ""
    tail = text[match.end():]
    next_heading = re.search(r"(?m)^##\s+", tail)
    return tail[: next_heading.start()] if next_heading else tail


class ResearchCorpusVerifier:
    """Objective shape/citation gate for a corpus draft.

    It does **not** claim a source is true; that requires an experiment or human
    review.  It enforces what code can know without a model: all seven sections
    exist exactly once and in order, cited source ids are declared, evidence
    bullets carry citations or say UNVERIFIED, and the proposed experiment names
    a baseline, instrument, metric, and stop rule.
    """

    name = "research_corpus"

    def verify(self, artifact, context=None) -> VerifyResult:
        text = artifact if isinstance(artifact, str) else str(artifact)
        failures: list[str] = []
        positions: list[int] = []
        for heading in _RESEARCH_HEADINGS:
            matches = list(re.finditer(_heading_pattern(heading), text))
            if len(matches) != 1:
                failures.append(f"{heading}: expected once, found {len(matches)}")
            elif matches:
                positions.append(matches[0].start())
        if len(positions) == len(_RESEARCH_HEADINGS) and positions != sorted(positions):
            failures.append("sections are out of order")

        source_section = _section(text, "Sources")
        declared = set(re.findall(r"(?im)^\s*[-*]\s*(S\d+)\b", source_section))
        cited = set(re.findall(r"\[(S\d+)\]", text))
        unknown = cited - declared
        if unknown:
            failures.append("citations not declared in Sources: " + ", ".join(sorted(unknown)))
        if not declared:
            failures.append("Sources declares no S<number> entries")
        if declared and not cited:
            failures.append("no inline [S<number>] citations")
        for line in source_section.splitlines():
            if re.match(r"^\s*[-*]\s*S\d+\b", line) and not (
                "http://" in line
                or "https://" in line
                or re.search(r"\b(unavailable|refused|failed|not captured)\b", line, re.I)
            ):
                failures.append("source entry lacks URL or capture-failure status")
                break

        evidence = _section(text, "Evidence the authors present")
        for line in evidence.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("-", "*")):
                continue
            if "[S" not in stripped and "UNVERIFIED" not in stripped.upper():
                failures.append("evidence bullet lacks [S#] or UNVERIFIED")
                break

        experiment = _section(
            text, "Not yet verified by us - and the experiment that would"
        ).lower()
        for field in ("baseline", "instrument", "metric", "stop rule"):
            if not re.search(rf"\b{re.escape(field)}\b", experiment):
                failures.append(f"experiment lacks {field}")

        low = text.lower()
        found_slop = [phrase for phrase in _SLOP_PHRASES if phrase in low]
        if found_slop:
            failures.append("mechanical slop phrases: " + ", ".join(found_slop))

        if failures:
            return VerifyResult(False, "research corpus: " + "; ".join(failures))
        return VerifyResult(
            True,
            f"research corpus: 7 sections, {len(declared)} sources, "
            f"{len(cited)} cited source ids, concrete experiment",
        )


class IdeaAssessmentVerifier:
    """Structural gate for the theory-first idea triage document.

    The durable intake re-validates citations against the custody bundle after
    this gate. Here the gate proves that the model emitted the complete rubric
    and that its deterministic score/budget can be computed.
    """

    name = "idea_assessment"

    def verify(self, artifact, context=None) -> VerifyResult:
        from lhassa.research.assessments import parse_document, score_document

        try:
            document = parse_document(str(artifact))
            declared = {
                str(item)
                for item in document.get("source_registry", [])
                if str(item).startswith("S")
            }
            result = score_document(document, declared_source_ids=declared)
        except ValueError as exc:
            return VerifyResult(False, f"idea assessment: {exc}")
        return VerifyResult(
            True,
            "idea assessment: complete rubric; "
            f"{result['weighted_score']}/{result['max_weighted_score']}; "
            f"{result['investment_level']} → {result['decision']}",
            metric=float(result["weighted_score"]),
        )


