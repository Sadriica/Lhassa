# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for the research line (``lhassa.plugins`` -> ``research``).

Importing this module registers the pack and everything it plugs in:

* actions ``research_intake`` (the ``/idea`` door's durable intake) and
  ``draft_research_note`` (the geshe's evidence-cited drafts);
* verifiers ``research_corpus`` and ``idea_assessment``;
* the ``research`` seed (the geshe's aides + the two canonical flows);
* the ``/idea`` chat command and the integrity requirements
  (:mod:`lhassa.research.surfaces.hooks`);
* ``/api/research`` and ``lhassa research``.

The ``idea_assessments`` / ``idea_assessment_events`` tables predate the pack
and stay in core's migrations. Every registration is idempotent.
"""

from __future__ import annotations

from lhassa.extensions import register_api_router, register_cli_app, register_pack

register_pack(
    "research",
    "The research line: /idea intake with source custody, theory-first "
    "assessment, the corpus flow and its gates, evidence-cited note drafts",
    module=__name__,
)


def _register_actions() -> None:
    from lhassa.core.actions import ACTIONS, register_action
    from lhassa.research.note import draft_research_note
    from lhassa.research.surfaces.intake import research_intake

    from lhassa.security.allowlist import WILDCARD, ActionSpec, register_action_spec
    from lhassa.security.trust import TrustLevel

    for name, fn in (
        ("draft_research_note", draft_research_note),
        ("research_intake", research_intake),
    ):
        if name not in ACTIONS:
            register_action(name, fn)
    # Policy rows (deny-by-default holds without them). Drafting: local
    # instrument reads + ONE routine-model pass + a write confined to
    # doc/notes/drafts, never published by the brain. Intake: the owner's
    # explicit /idea, so HUMANO trust; source reads + a review-only draft.
    register_action_spec(
        "draft_research_note",
        ActionSpec(objetivos=WILDCARD, min_trust=TrustLevel.INTERNO, requires_approval=False),
    )
    register_action_spec(
        "research_intake",
        ActionSpec(objetivos=WILDCARD, min_trust=TrustLevel.HUMANO, requires_approval=False),
    )


def _register_verifiers() -> None:
    from lhassa.agents.verify import VERIFIERS, register_verifier
    from lhassa.research.verifiers import IdeaAssessmentVerifier, ResearchCorpusVerifier

    for name, cls in (
        ("research_corpus", ResearchCorpusVerifier),
        ("idea_assessment", IdeaAssessmentVerifier),
    ):
        if name not in VERIFIERS:
            register_verifier(name, cls)


def _apply_seed(config=None) -> dict:
    from lhassa.research.surfaces.seed import apply

    return apply(config)


def _register_seed() -> None:
    from lhassa.seeds import SEEDS, register_seed

    if "research" not in SEEDS:
        register_seed(
            "research",
            "Create the geshe's research aid department (collector/analyst/"
            "skeptic/synthesist under the scholar-monk) and the idea-intake "
            "work-flow that turns an idea into a corpus-style draft per "
            "research/METHOD.md.",
        )(_apply_seed)


def _api_router():
    from lhassa.research.surfaces.api import router

    return router


def _cli_app():
    from lhassa.research.surfaces.cli import research_app

    return research_app


_register_actions()
_register_verifiers()
_register_seed()

from lhassa.research.surfaces import hooks as _hooks  # noqa: E402

_hooks.register()

register_api_router("research", _api_router)
register_cli_app("research", _cli_app)
