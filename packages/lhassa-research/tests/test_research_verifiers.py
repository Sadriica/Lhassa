"""The research pack's objective gates: ``research_corpus`` and ``idea_assessment``.

Moved out of ``tests/test_verify.py`` with the verifiers themselves; they still
resolve through the shared ``lhassa.agents.verify`` dispatcher once the pack is
registered.
"""

import lhassa.research.pack  # noqa: F401  (registers the research verifiers)
from lhassa.agents import verify as V
from lhassa.research.verifiers import IdeaAssessmentVerifier, ResearchCorpusVerifier


# ── ResearchCorpusVerifier (shape/citations/experiment, no LLM) ─────────────

def _valid_corpus():
    return """## The idea in one sentence
One bounded claim [S1].

## Mechanics - down to the base
The captured mechanism has two stages [S1].

## Evidence the authors present
- The authors report a result [S1].
- A second claim is UNVERIFIED.

## Sources
- S1: https://example.test/paper; sha256=abc

## Mapping to Lhassa
No mapping has been verified.

## Not yet verified by us - and the experiment that would
Baseline: current route
Instrument: LhassaBench
Metric: verified pass rate
Stop rule: 20 runs or the budget cap

## Open questions
- Does it generalize?
"""


def test_research_corpus_verifier_passes_complete_cited_draft():
    result = V.verify(_valid_corpus(), spec={"type": "research_corpus"})
    assert result.passed is True
    assert "7 sections" in result.detail


def test_research_corpus_verifier_rejects_unsupported_evidence_bullet():
    text = _valid_corpus().replace(
        "- The authors report a result [S1].",
        "- The authors report a result.",
    )
    result = ResearchCorpusVerifier().verify(text)
    assert result.passed is False
    assert "lacks [S#] or UNVERIFIED" in result.detail


def test_research_corpus_verifier_rejects_symbolic_last_heading_gate():
    result = ResearchCorpusVerifier().verify("## Open questions\nAnything")
    assert result.passed is False
    assert "The idea in one sentence" in result.detail


def test_idea_assessment_verifier_recomputes_the_budget():
    import json

    from test_research_assessments import document

    result = IdeaAssessmentVerifier().verify(json.dumps(document(3)))
    assert result.passed is True
    assert "rigorous → experiment" in result.detail




def test_pack_registers_both_research_gates():
    names = set(V.VERIFIERS.names())
    assert {"research_corpus", "idea_assessment"} <= names
    assert isinstance(V.make_verifier({"type": "research_corpus"}), ResearchCorpusVerifier)
