"""
actionpack — the built-in set of deterministic actions (Phase 3; org_run Etapa 3).

Shell-layer package: composes notify/, health/, memory/, cv/, security/, core/,
org/. Nothing in core/ or other inner layers may import from here.

Public surface
--------------
register_all()
    Register every action in this package with the core ACTIONS registry.
    Idempotent — safe to call any number of times. Call it from every
    long-lived process entrypoint (main.py, heartbeat, CLI, API, Telegram,
    monitor) so the judgment dispatcher always has the full action set.
"""

from __future__ import annotations

_REGISTERED = False


def register_all() -> None:
    """Register all actionpack actions. Idempotent — safe to call multiple times.

    Imports each sub-module (which defines the action functions) and wires
    them into ``lhassa.core.actions.ACTIONS`` via ``register_action``.

    Guard: a module-level flag prevents duplicate registration on a second
    call. The ``Registry`` itself also raises on true duplicates, so the flag
    is belt-and-suspenders.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    from lhassa.core.actions import _REGISTRY, register_action

    # Import sub-modules — this defines the functions without side-effects.
    from lhassa.actionpack import (  # noqa: F401
        _brain,
        _calendar_sync,
        _curate,
        _derive,
        _digest,
        _files,
        _knowledge,
        _llm,
        _memory,
        _net,
        _org,
        _reminder,
        _research,
        _script,
        _selfimprove,
        _system,
        _temple,
    )

    # Defensive guard: skip any action that is already in the registry.
    # This makes register_all() safe even if _REGISTERED is reset externally
    # (e.g. in tests) while the underlying registry still holds the entries.
    def _reg(name: str, fn) -> None:  # noqa: ANN001
        if _REGISTRY.get_or_none(name) is None:
            register_action(name, fn)

    # ── Net ──────────────────────────────────────────────────────────────────
    _reg("web_fetch", _net.web_fetch)
    _reg("ping_url", _net.ping_url)

    # ── System ───────────────────────────────────────────────────────────────
    _reg("run_command", _system.run_command)
    _reg("system_snapshot", _system.system_snapshot)
    _reg("power_mode", _system.power_mode)

    # ── Memory ───────────────────────────────────────────────────────────────
    _reg("add_fact", _memory.add_fact)
    _reg("backup_memory", _memory.backup_memory)
    _reg("consolidate_memory", _memory.consolidate_memory)
    _reg("distill_memory", _memory.distill_memory)

    # ── Brain / cross-cutting ─────────────────────────────────────────────────
    _reg("notify", _brain.notify)
    _reg("add_task", _brain.add_task)
    _reg("set_task_state", _brain.set_task_state)
    _reg("list_approvals", _brain.list_approvals)
    _reg("check_health", _brain.check_health)
    _reg("git_status_repos", _brain.git_status_repos)
    _reg("compile_cv", _brain.compile_cv)

    # ── LLM spend visibility ───────────────────────────────────────────────────
    _reg("check_spend", _llm.check_spend)

    # ── Digest (Etapa 2 — operative day via Telegram) ─────────────────────────
    _reg("send_digest", _digest.send_digest)

    # ── Master calendar reconcile ($0 set diff, no model) ─────────────────────
    _reg("calendar_sync", _calendar_sync.calendar_sync)

    # ── Calendar-derived world model ($0, deterministic — courses/commitments/
    # rhythm bootstrapped from the calendar feed into durable facts) ──────────
    _reg("derive_world_facts", _derive.derive_world_facts)

    # ── Document perception — index the owner's opt-in knowledge roots ───────
    _reg("ingest_knowledge", _knowledge.ingest_knowledge)
    _reg("list_dir", _files.list_dir)
    _reg("read_file", _files.read_file)

    # ── Org — real delegation into the hierarchy (Etapa 3) ────────────────────
    _reg("org_run", _org.org_run)

    # Drain THIS instance's org inbox on every tick (enqueue → heartbeat closes
    # the loop). Registers a watchlist check-func; see org/inbox_sense.py for
    # why this lives here rather than a direct import in core/watchlist.py.
    from lhassa.org import inbox_sense
    inbox_sense.register()

    # ── Self-improvement (§2.8, Etapa 4) ──────────────────────────────────────
    _reg("detect_repetition", _selfimprove.detect_repetition)
    _reg("review_tools", _selfimprove.review_tools)

    # ── Skills curator (deterministic lifecycle sweep) ─────────────────────────
    _reg("curate_skills", _curate.curate_skills)

    # ── Temple — internal hygiene work routed down-execute-up through the
    # temple department (see lhassa.org.temple / lhassa.seeds.temple) ────────
    _reg("temple_run", _temple.temple_run)

    # ── PTC — Programmatic Tool Calling (script-over-RPC in quarantine) ────────
    _reg("run_script", _script.run_script)

    # ── Research bridge (Telegram [[research: …]] → live web results) ──────────
    _reg("research_reply", _research.research_reply)

    # ── Reminders (the one clock: tasks + heartbeat, no separate poller) ───────
    _reg("send_reminder", _reminder.send_reminder)
