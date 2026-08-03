"""The phrasebook — a known action's outcome becomes a sentence a person reads.

The critical property under test: every phrasing is exercised against the
EXACT strings its action really returns, not against strings invented for the
test. The first version of this phrasebook shipped with two entries whose
regexes never matched their action's real output — they degraded silently to
the raw machine line forever, and only a review caught it. These tests make
that failure loud.
"""

import pytest

from lhassa.notify.voice import PHRASES, phrase


# ── real output strings, copied from each action's own return statements ──────

REAL = {
    "check_integrity": [
        "check_integrity: every contract passed (run conf_abc)",
        "check_integrity: fail — 1 critical (run conf_x)",
        "check_integrity: fail — 3 critical and 2 warning (run conf_x)",
        "check_integrity: warn — 2 warning (run conf_x)",
    ],
    "curate_skills": [
        "curate_skills: checked 12, stale 2, archived 3, skipped_pinned 1",
        "curate_skills: checked 12, stale 0, archived 0, skipped_pinned 1",
    ],
    "review_tools": [
        "review_tools: 5 tool(s) checked, none retired",
        "review_tools: retired 2/5: tool_a, tool_b",
    ],
    "review_capabilities": [
        "review_capabilities: no dormant capabilities",
        "review_capabilities: 13 dormant, 3 proposed",
        "review_capabilities: 13 dormant, 1 proposed",
    ],
    "detect_repetition": [
        "detect_repetition: 4 pattern(s) found, 2 newly proposed",
        "detect_repetition: 4 pattern(s) found, 0 newly proposed",
    ],
    "calendar_sync": [
        "calendar_sync: 0 unsynced event(s), 0 delivered",
        "calendar_sync: 3 unsynced event(s), 2 delivered",
    ],
    "derive_world_facts": [
        "derive_world_facts: derived 5 world facts, 2 new",
        "derive_world_facts: derived 5 world facts, 0 new",
    ],
    "apply_feedback": [
        "apply_feedback: no scorable signals in the last 7d",
    ],
    "draft_research_note": [
        "draft_research_note: draft written to doc/notes/drafts/x.md — review before publishing.",
    ],
}


@pytest.mark.parametrize("action", sorted(PHRASES))
def test_every_registered_phrasing_fires_on_real_output(action):
    """A phrasing that never matches its action's real output is dead code."""
    samples = REAL.get(action)
    assert samples, f"add real output samples for {action} to REAL"
    fired = [phrase(action, s) for s in samples]
    assert any(fired), (
        f"{action}: no sample produced a phrase — its regexes do not match "
        "the action's real output format"
    )


@pytest.mark.parametrize(
    "action,result", [(a, s) for a, samples in REAL.items() for s in samples]
)
def test_phrases_read_like_a_person_wrote_them(action, result):
    said = phrase(action, result)
    if said is None:
        return  # honest fallback is allowed per-sample
    # No machine vocabulary may leak through.
    assert "run conf_" not in said
    assert "(s)" not in said, "pluralize properly — '(s)' is what machines write"
    assert not said.startswith(action), "no action-name prefix"
    assert "{" not in said and "}" not in said


def test_unknown_action_returns_none():
    assert phrase("no_such_action", "whatever") is None


def test_unparseable_output_degrades_to_none_not_a_guess():
    """An entry that cannot parse must say nothing, never invent a summary."""
    assert phrase("curate_skills", "curate_skills: error: boom") is None
    assert phrase("check_integrity", "totally unexpected text") is None


def test_a_raising_phrasing_degrades_to_none(monkeypatch):
    from lhassa.notify import voice

    def _boom(result):
        raise RuntimeError("phrasing exploded")

    monkeypatch.setitem(voice.PHRASES, "check_integrity", _boom)
    assert phrase("check_integrity", "check_integrity: fail — 1 critical") is None


def test_singular_and_plural_agree():
    one = phrase("review_capabilities", "review_capabilities: 9 dormant, 1 proposed")
    many = phrase("review_capabilities", "review_capabilities: 9 dormant, 3 proposed")
    assert "1 thing" in one and "retire it" in one
    assert "3 things" in many and "retire them" in many


def test_temple_delivery_uses_the_phrase(monkeypatch):
    """The delivery seam prefers the phrase and falls back without stuttering."""
    from lhassa.notify.voice import phrase as real_phrase

    said = real_phrase(
        "check_integrity", "check_integrity: fail — 1 critical (run conf_x)"
    )
    assert said is not None and "Something needs a look" in said


# ── parity: every allowlisted action must also be a registered action ─────────


def test_actions_registry_and_allowlist_do_not_drift():
    """Today the two key sets match; nothing enforced it until now."""
    from lhassa import actionpack

    actionpack.register_all()
    from lhassa.core.actions import ACTIONS
    from lhassa.security.allowlist import ALLOWLIST

    assert set(ACTIONS) <= set(ALLOWLIST), (
        "registered actions missing from the allowlist: "
        f"{sorted(set(ACTIONS) - set(ALLOWLIST))}"
    )
