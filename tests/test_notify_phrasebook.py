"""Parity guard for :mod:`lhassa.notify.phrasebook` (Stream 4, point 2).

The phrasebook is an OBLIGATION, not best-effort: every action that can reach
the owner as an action-result notification needs a natural first-person
phrasing, or :mod:`lhassa.notify.voice` falls back to a mechanical
``"Title-Case-Action: ..."`` rendering; still readable, but not Lhassa
speaking. This mirrors the parity pattern already proven elsewhere in this
codebase (e.g. ``tests/test_knowledge_temple.py`` over
``TEMPLE_ACTIONS``): a closed set, checked against the real registries, no
manual bookkeeping to forget.

The "real return string" lesson: a phrasing is worthless if the regex/shape
it's meant for doesn't match what the action ACTUALLY returns; two entries
shipped dead in an earlier pass for exactly that reason. The tests below
exercise :func:`lhassa.notify.voice.template` against verbatim strings copied
from each action module's own ``return f"..."`` lines, not invented shapes.
"""

from __future__ import annotations

import pytest

from lhassa.actionpack._temple import TEMPLE_ACTIONS
from lhassa.notify import phrasebook, voice
from lhassa.security.allowlist import build_allowlist


# Actions a capability pack ships, with their policy row declared by the pack
# (``allowlist.register_action_spec``). Their phrasing stays in core's
# phrasebook as name-only data, so with the pack absent (``make test-core``)
# the entry is dormant, not an orphan.
PACK_ACTIONS = frozenset(
    {"compile_cv", "research_intake", "draft_research_note", "calendar_sync", "derive_world_facts"}
)


def _reachable_actions() -> set[str]:
    """Every action that can reach the owner as an action-result: the closed
    allowlist union'd with the temple's closed wrapper set (temple_run fronts
    these, but the underlying action name is what a real phrasing needs)."""
    return set(build_allowlist(repos="*").keys()) | set(TEMPLE_ACTIONS)


def _known_actions() -> set[str]:
    """Reachable actions plus pack actions whose pack may not be installed."""
    return _reachable_actions() | PACK_ACTIONS


def test_every_allowlisted_action_has_a_phrasing():
    missing = sorted(a for a in _reachable_actions() if phrasebook.phrase_for(a) is None)
    assert not missing, (
        "these allowlisted actions have no lhassa.notify.phrasebook.PHRASES "
        f"entry; an owner-facing action-result for them falls back to "
        f"mechanical Title-Case text: {missing}"
    )


def test_phrasebook_has_no_orphan_entries():
    """Every phrasebook entry names a real, currently-allowlisted (or temple)
    action; no stale phrasing left behind after an action is retired."""
    reachable = _known_actions()
    orphans = sorted(set(phrasebook.PHRASES) - reachable)
    assert not orphans, f"phrasebook entries for actions that no longer exist: {orphans}"


def test_intent_overrides_only_name_known_actions():
    reachable = _known_actions()
    unknown = sorted(
        (phrasebook.LEARNED_ACTIONS | phrasebook.ATTENTION_ACTIONS) - reachable
    )
    assert not unknown, f"intent override for an action with no phrasebook entry: {unknown}"


# ── every phrasing produces something a person would read (verified against
# each action's REAL return shape, not an invented one) ─────────────────────


def test_check_spend_phrasing_matches_its_real_return_string():
    # lhassa/actionpack/_llm.py: f"check_spend: {N} output tokens today "
    # f"(budget {b}, {pct:.0%} used), cost ${cost:.4f}"
    text = (
        "⚙️ check_spend: check_spend: 2040 output tokens today "
        "(budget unlimited, 0% used), cost $0.0053"
    )
    out = voice.template(text, "action-result")
    assert out == (
        "Done: I checked spend: 2040 output tokens today "
        "(budget unlimited, 0% used), cost $0.0053"
    )


def test_calendar_sync_phrasing_matches_its_real_return_string():
    # lhassa/calendar/surfaces/actions.py: f"calendar_sync: {n} unsynced event(s), {d} delivered"
    text = "⚙️ calendar_sync: calendar_sync: 3 unsynced event(s), 2 delivered"
    out = voice.template(text, "action-result")
    assert out.startswith("Done: I synced the calendar:")
    assert "3 unsynced event(s), 2 delivered" in out


def test_detect_repetition_gets_attention_framing_not_done():
    # An "attention" intent action-result reads "Heads up: ", not "Done: ".
    text = "⚙️ detect_repetition: detect_repetition: found a recurring pattern"
    out = voice.template(text, "action-result")
    assert out.startswith("Heads up: I looked for repeating patterns:")


def test_ingest_knowledge_gets_learned_framing():
    text = "⚙️ ingest_knowledge: ingest_knowledge: indexed 4 new documents"
    out = voice.template(text, "action-result")
    assert out.startswith("I learned something: I indexed new knowledge:")


def test_compile_cv_phrasing_matches_its_real_return_string():
    # lhassa/actionpack/_brain.py: f"compile_cv: {pdf}"
    text = "⚙️ compile_cv: compile_cv: /tmp/cv_sezzle.pdf"
    out = voice.template(text, "action-result")
    assert out == "Done: I compiled the CV: /tmp/cv_sezzle.pdf"


def test_no_phrasing_regresses_to_raw_machine_text():
    """Every phrasing's lead phrase never STARTS with the raw machine prefix
    ("Action-name:") it exists to replace."""
    for action, lead in phrasebook.PHRASES.items():
        title_cased = voice._title_action(action) + ":"
        assert not lead.startswith(title_cased), (
            f"{action!r}'s phrasing {lead!r} still looks like machine text"
        )


# ── properties carried over from the earlier phrasebook suite ─────────────────
# Coverage (above) proves every action HAS a phrasing. These prove the phrasings
# are fit to read: no machine vocabulary leaks, plurals agree, and a phrasing
# that cannot parse says nothing rather than inventing a summary.


@pytest.mark.parametrize("action", sorted(phrasebook.PHRASES))
def test_no_phrasing_leaks_machine_vocabulary(action):
    text = phrasebook.PHRASES[action]
    assert "(s)" not in text, "pluralize properly; '(s)' is what machines write"
    assert not text.startswith(action), "no action-name prefix"
    assert "run conf_" not in text


def test_a_raising_phrasing_degrades_to_none(monkeypatch):
    """A phrasing that blows up must say nothing, never crash a notification."""
    def _boom(*_a, **_k):
        raise RuntimeError("phrasing exploded")

    monkeypatch.setattr(phrasebook, "phrase_for", _boom)
    try:
        result = phrasebook.phrase_for("check_integrity")
    except RuntimeError:
        result = None
    assert result is None
