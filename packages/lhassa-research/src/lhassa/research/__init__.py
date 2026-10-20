# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lhassa's research line (the ``research`` capability pack).

Inner, shell-free modules sit at this level: :mod:`.evidence` (pre-model
source custody), :mod:`.assessments` (theory-first triage over the core
``idea_assessments`` tables), :mod:`.verifiers` (the ``research_corpus`` and
``idea_assessment`` gates), :mod:`.note` (``draft_research_note``) and
:mod:`.product_change` (research-to-product adoption). Everything that
composes a shell (the ``/idea`` intake action, the chat command, the seed, the
API router, the CLI sub-app, the integrity hooks) lives in :mod:`.surfaces`.
:mod:`.pack` registers all of it.
"""
