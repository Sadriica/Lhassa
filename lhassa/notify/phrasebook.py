# SPDX-License-Identifier: AGPL-3.0-or-later
"""The action phrasebook — a natural, first-person lead for every action-result
notification, so nothing that reaches the owner reads like a log line.

Without this table :func:`lhassa.notify.voice._clean_action_result` falls back
to a mechanical ``"Title-Case-Action: <raw detail>"`` rendering (e.g.
``"Check-spend: 2040 output tokens today..."``) for any action it doesn't
recognize — still readable, but not Lhassa speaking. :data:`PHRASES` covers
every action the owner can actually be told about (the closed union of
:func:`lhassa.security.allowlist.build_allowlist` and
:data:`lhassa.actionpack._temple.TEMPLE_ACTIONS`); ``tests/test_notify_phrasebook.py``
is the parity guard — it fails the moment a new allowlisted action ships with
no entry here, the same pattern already proven for other closed-set tables in
this codebase (see e.g. ``tests/test_knowledge_temple.py``).

**The lesson already paid for once**: a phrasing is worthless unless it is
checked against the action's REAL return string (grep the actual
``return f"..."`` in the actionpack module), never an invented one — two
phrasings shipped dead in an earlier pass because nobody did that. Every entry
below was verified against its action's current return shape before landing.

:data:`INTENT_OVERRIDES` implements "templates by intent, not only by kind"
(Stream 4, point 5): most action-results are simply "I finished something"
(the default ``action-result`` framing, "Done — "), but a few are better read
as "something needs your attention" (``alert``, "Heads up — ") or "I learned
something" (``learned``, "I learned something — "). :func:`intent_for` is the
lookup :mod:`lhassa.notify.voice` calls to pick the outer framing.
"""

from __future__ import annotations

# action name -> a natural, first-person lead that stands in for the
# mechanical "Title-Case-Action: " prefix. Joined with the (naturalized)
# detail as "<lead>: <detail>" by lhassa.notify.voice._clean_action_result.
PHRASES: dict[str, str] = {
    # ── Phase 0 ─────────────────────────────────────────────────────────────
    "sync_repo": "I synced the repo",
    "tracking_postulaciones": "I checked the applications tracker",
    # ── Brain / cross-cutting ──────────────────────────────────────────────
    "notify": "I sent a notification",
    "add_task": "I added a task",
    "set_task_state": "I updated a task",
    "list_approvals": "I checked pending approvals",
    "check_health": "I checked health",
    "git_status_repos": "I checked repo status",
    "compile_cv": "I compiled the CV",
    # ── System ──────────────────────────────────────────────────────────────
    "run_command": "I ran a command",
    "system_snapshot": "I took a system snapshot",
    "power_mode": "I switched power mode",
    # ── Net ─────────────────────────────────────────────────────────────────
    "web_fetch": "I fetched a page",
    "ping_url": "I pinged a URL",
    # ── Memory ──────────────────────────────────────────────────────────────
    "add_fact": "I saved a fact to memory",
    "backup_memory": "I backed up memory",
    "consolidate_memory": "I consolidated memory",
    "distill_memory": "I distilled memory",
    # ── LLM spend visibility ───────────────────────────────────────────────
    "check_spend": "I checked spend",
    # ── Digest ──────────────────────────────────────────────────────────────
    "send_digest": "I sent the digest",
    # ── Calendar / world model ─────────────────────────────────────────────
    "calendar_sync": "I synced the calendar",
    "derive_world_facts": "I derived some world facts",
    # ── Knowledge ───────────────────────────────────────────────────────────
    "ingest_knowledge": "I indexed new knowledge",
    "list_dir": "I listed a directory",
    "read_file": "I read a file",
    "set_config": "I updated a setting",
    "apply_feedback": "I applied a feedback signal",
    "create_project": "I created a project",
    "kg_sync": "I updated the knowledge graph",
    "kg_resolve": "I merged duplicate entities in the knowledge graph",
    "check_integrity": "I checked integrity",
    "attach_project_template": "I attached a project template",
    "draft_research_note": "I drafted a research note",
    "research_intake": "I took in a research idea",
    # ── Org — real delegation ──────────────────────────────────────────────
    "org_run": "I delegated the work",
    # ── Self-improvement ────────────────────────────────────────────────────
    "detect_repetition": "I looked for repeating patterns",
    "review_tools": "I reviewed self-written tools",
    # ── Skills curator ──────────────────────────────────────────────────────
    "curate_skills": "I tidied up skills",
    # ── Utility tribunal ────────────────────────────────────────────────────
    "review_capabilities": "I reviewed which capabilities are actually used",
    # ── Temple ──────────────────────────────────────────────────────────────
    "temple_run": "I ran an internal task",
    # ── PTC ─────────────────────────────────────────────────────────────────
    "run_script": "I ran a script",
    # ── Research bridge ─────────────────────────────────────────────────────
    "research_reply": "I looked that up",
    # ── Reminders ────────────────────────────────────────────────────────────
    "send_reminder": "I sent a reminder",
}

# Actions whose action-result is better framed as "I learned something — "
# than the default "Done — ": they ADD to what Lhassa knows rather than just
# finishing a chore.
LEARNED_ACTIONS: frozenset[str] = frozenset(
    {
        "ingest_knowledge",
        "derive_world_facts",
        "kg_sync",
        "kg_resolve",
        "draft_research_note",
        "research_intake",
        "research_reply",
        "add_fact",
    }
)

# Actions whose action-result surfaces a finding that deserves "Heads up — "
# rather than a plain "Done — ": completion isn't the point, the finding is.
ATTENTION_ACTIONS: frozenset[str] = frozenset(
    {
        "detect_repetition",
        "review_capabilities",
        "review_tools",
        "check_health",
        "check_integrity",
        "list_approvals",
    }
)


def phrase_for(action: str) -> "str | None":
    """The natural first-person lead for ``action``, or ``None`` if unknown."""
    return PHRASES.get(action)


def intent_for(action: str) -> str:
    """The notify-voice framing key for ``action``'s action-result.

    One of ``"learned"`` / ``"alert"`` / ``"action-result"`` (the default) —
    see :data:`lhassa.persona.LEAD_PHRASES` for what each renders as.
    """
    if action in LEARNED_ACTIONS:
        return "learned"
    if action in ATTENTION_ACTIONS:
        return "alert"
    return "action-result"
