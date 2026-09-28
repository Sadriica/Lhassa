"""Tests for the modular Telegram slash-command registry.

No real network and no real LLM. ``/status`` is asserted to be **LLM-free** by
proving no provider completion is constructed/called; health is monkeypatched and
the cost ledger is pointed at a temp DB.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.notify import commands


def _cfg(**over):
    base = dict(
        heartbeat_provider="opencode",
        opencode_model="opencode/deepseek-v4-flash-free",
        opencode_bin="opencode",
        local_model_url="",
        local_model="",
        model="cloud-smart",
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── parse ─────────────────────────────────────────────────────────────────────


def test_parse_simple_command():
    assert commands.parse("/start") == ("start", "")


def test_parse_command_with_botname_and_args():
    # Telegram's group form "/cmd@BotName" → command normalised, args preserved.
    assert commands.parse("/help@Lhassa_bot") == ("help", "")
    assert commands.parse("/status x y") == ("status", "x y")


def test_parse_botname_with_args():
    assert commands.parse("/status@Lhassa_bot now please") == ("status", "now please")


def test_parse_plain_text_is_not_a_command():
    assert commands.parse("what time is it") == (None, "what time is it")


def test_parse_bare_slash_is_not_a_command():
    assert commands.parse("/") == (None, "/")
    assert commands.parse("/@Lhassa_bot") == (None, "/@Lhassa_bot")


def test_parse_is_case_insensitive_and_trims():
    assert commands.parse("  /HELP  ") == ("help", "")


# ── dispatch ──────────────────────────────────────────────────────────────────


def test_dispatch_known_command_runs_handler():
    reply = asyncio.run(commands.dispatch("/start", config=_cfg()))
    assert "Lhassa" in reply
    assert "/help" in reply


def test_dispatch_unknown_command_returns_hint():
    reply = asyncio.run(commands.dispatch("/frobnicate now", config=_cfg()))
    assert "Unknown command" in reply
    assert "/frobnicate" in reply
    assert "/help" in reply


def test_dispatch_plain_text_returns_none():
    # The signal to the caller: not a command → let the brain answer.
    assert asyncio.run(commands.dispatch("hello there", config=_cfg())) is None


def test_dispatch_handler_error_degrades(monkeypatch):
    async def _boom(args, *, config=None):
        raise RuntimeError("kaboom")

    commands.register_command("boomtest", _boom)
    try:
        reply = asyncio.run(commands.dispatch("/boomtest", config=_cfg()))
        assert "/help" in reply
    finally:
        commands.COMMANDS.unregister("boomtest")


# ── /help lists registered commands ───────────────────────────────────────────


def test_help_lists_registered_commands():
    async def _temp(args, *, config=None):
        return "ok"

    commands.register_command("zzztemp", _temp)
    try:
        reply = asyncio.run(commands.dispatch("/help", config=_cfg()))
        assert "/zzztemp" in reply
        # Built-ins are listed too.
        assert "/start" in reply
        assert "/status" in reply
    finally:
        commands.COMMANDS.unregister("zzztemp")


# ── /status is provider/model + health, with NO LLM call ──────────────────────


def _no_llm(monkeypatch):
    """Make any attempt to construct/call a provider blow up; proving /status is
    LLM-free."""
    import lhassa.llm.provider as provider_mod

    def _boom(*a, **k):
        raise AssertionError("/status must not construct a provider / call an LLM")

    monkeypatch.setattr(provider_mod, "get_model_provider", _boom, raising=False)


def test_status_reports_provider_and_model_no_llm(monkeypatch, tmp_path):
    _no_llm(monkeypatch)

    # Health is monkeypatched to a deterministic digest (no systemctl/git/disk).
    import lhassa.health.checks as checks_mod

    monkeypatch.setattr(
        checks_mod,
        "run_all",
        lambda *a, **k: {"worst_level": "notice", "ok": True, "results": {}},
    )

    # Point the cost ledger at a temp DB that does not exist → spend line omitted.
    cfg = _cfg(llm_gateway_db_path=str(tmp_path / "absent.db"))

    reply = asyncio.run(commands.dispatch("/status", config=cfg))
    assert "Provider: opencode" in reply
    assert "opencode/deepseek-v4-flash-free" in reply
    assert "Health: notice (ok)" in reply
    # No ledger DB → no spend line.
    assert "Spend today" not in reply


def test_status_includes_spend_when_ledger_present(monkeypatch, tmp_path):
    _no_llm(monkeypatch)

    import lhassa.health.checks as checks_mod

    monkeypatch.setattr(
        checks_mod,
        "run_all",
        lambda *a, **k: {"worst_level": "info", "ok": True, "results": {}},
    )

    # Build a ledger DB with two rows today and one row on an old day.
    import sqlite3
    from datetime import datetime, timezone

    db_path = tmp_path / "llm_gateway.db"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE ledger (id INTEGER PRIMARY KEY, ts TEXT, provider TEXT, "
        "model TEXT, input_tokens INT, output_tokens INT, cost_usd REAL, cache_hit INT)"
    )
    conn.executemany(
        "INSERT INTO ledger(ts, provider, model, input_tokens, output_tokens, "
        "cost_usd, cache_hit) VALUES(?,?,?,?,?,?,?)",
        [
            (f"{today}T01:00:00+00:00", "opencode", "m", 1, 1, 0.01, 0),
            (f"{today}T02:00:00+00:00", "opencode", "m", 1, 1, 0.02, 0),
            ("2020-01-01T00:00:00+00:00", "opencode", "m", 1, 1, 9.99, 0),
        ],
    )
    conn.commit()
    conn.close()

    cfg = _cfg(llm_gateway_db_path=str(db_path))
    reply = asyncio.run(commands.dispatch("/status", config=cfg))
    assert "Spend today: $0.0300" in reply  # old-day row excluded


# ── provider/model derivation across heartbeat modes ──────────────────────────


@pytest.mark.parametrize(
    "cfg_over,expected_provider,expected_model",
    [
        (dict(heartbeat_provider="cloud"), "cloud", "cloud-smart"),
        (
            dict(heartbeat_provider="local", local_model_url="http://x", local_model="ll"),
            "local",
            "ll",
        ),
        # heartbeat='local' but no url → cloud fallback (mirrors the router).
        (dict(heartbeat_provider="local", local_model_url=""), "cloud", "cloud-smart"),
    ],
)
def test_routine_provider_model_modes(cfg_over, expected_provider, expected_model):
    label, model = commands._routine_provider_model(_cfg(**cfg_over))
    assert label == expected_provider
    assert model == expected_model


# ── /approvals, /approve, /deny ────────────────────────────────────────────────


async def _init_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


def test_approvals_lists_pending(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal", "task", {"accion": "run_command", "actor_id": "brain"}
        )
    )
    reply = asyncio.run(commands.dispatch("/approvals", config=_cfg(instance_id="inst_personal")))
    assert ap_id in reply
    assert "run_command" in reply


def test_approvals_empty_queue(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch("/approvals", config=_cfg(instance_id="inst_never_used_xyz"))
    )
    assert "No pending approvals." in reply


def test_approve_resolves_pending_approval(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal", "task", {"accion": "run_command", "actor_id": "brain"}
        )
    )
    reply = asyncio.run(
        commands.dispatch(f"/approve {ap_id}", config=_cfg(instance_id="inst_personal"))
    )
    assert "approved" in reply
    remaining = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert remaining == []


def test_approve_explains_high_risk_self_approval_refusal(tmp_path, monkeypatch):
    from lhassa.config import Config

    monkeypatch.setattr(Config, "always_approve_actions", {"move_money"})
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "task",
            {"accion": "move_money", "actor_id": "telegram"},
        )
    )
    reply = asyncio.run(
        commands.dispatch(f"/approve {ap_id}", config=_cfg(instance_id="inst_personal"))
    )
    assert reply.startswith("Approval refused:")
    assert "self-approval refused" in reply
    assert asyncio.run(db.get_approval("inst_personal", ap_id))["estado"] == "pendiente"


def test_deny_resolves_pending_approval(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval("inst_personal", "task", {"accion": "run_command"})
    )
    reply = asyncio.run(
        commands.dispatch(f"/deny {ap_id}", config=_cfg(instance_id="inst_personal"))
    )
    assert "denied" in reply
    remaining = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert remaining == []


def test_approve_unknown_id_reports_clearly(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch("/approve ap_missing", config=_cfg(instance_id="inst_personal"))
    )
    assert "No pending approval" in reply


def test_approve_requires_id():
    reply = asyncio.run(commands.dispatch("/approve", config=_cfg()))
    assert "Usage" in reply


def test_approve_mint_route_turns_on_the_route(tmp_path, monkeypatch):
    """Approving a mint_route proposal from chat applies it; the approval->apply
    connection point (mirrors the code_run launch-on-approve pattern)."""
    from lhassa.notify import capabilities

    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "mint_route",
            {"accion": "mint_route", "capability": "calendar_view", "count": 7},
        )
    )
    cfg = _cfg(instance_id="inst_personal", routes_file=str(tmp_path / "routes.json"))
    reply = asyncio.run(commands.dispatch(f"/approve {ap_id}", config=cfg))

    assert "approved" in reply
    assert capabilities.is_routed("calendar_view", config=cfg)


def test_deny_mint_route_does_not_turn_on_the_route(tmp_path, monkeypatch):
    from lhassa.notify import capabilities

    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    ap_id = asyncio.run(
        db.add_approval(
            "inst_personal",
            "mint_route",
            {"accion": "mint_route", "capability": "calendar_view", "count": 7},
        )
    )
    cfg = _cfg(instance_id="inst_personal", routes_file=str(tmp_path / "routes.json"))
    reply = asyncio.run(commands.dispatch(f"/deny {ap_id}", config=cfg))

    assert "denied" in reply
    assert not capabilities.is_routed("calendar_view", config=cfg)


# ── /watch, /watches, /unwatch ─────────────────────────────────────────────────


def _watch_cfg(tmp_path, **over):
    return _cfg(watch_rules_file=str(tmp_path / "watch_rules.json"), **over)


def test_watch_add_list_remove_roundtrip(tmp_path):
    cfg = _watch_cfg(tmp_path)

    added = asyncio.run(commands.dispatch("/watch backup .* -> backup_memory", config=cfg))
    assert "Watch added" in added
    assert "backup_memory" in added

    listed = asyncio.run(commands.dispatch("/watches", config=cfg))
    assert "backup_memory" in listed
    assert listed.startswith("Watch rules:")

    removed = asyncio.run(commands.dispatch("/unwatch 0", config=cfg))
    assert "Removed watch rule 0" in removed

    empty = asyncio.run(commands.dispatch("/watches", config=cfg))
    assert "No watch rules configured." in empty


def test_watch_rejects_invalid_regex_and_writes_nothing(tmp_path):
    cfg = _watch_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/watch [unterminated -> backup_memory", config=cfg))
    assert "Invalid regex" in reply

    from lhassa.notify import telegram_inbound as _ti

    assert _ti._load_raw_watch_rules(cfg) == []


def test_watch_rejects_unknown_accion(tmp_path):
    cfg = _watch_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/watch hello -> not_a_real_action", config=cfg))
    assert "Unknown accion" in reply

    from lhassa.notify import telegram_inbound as _ti

    assert _ti._load_raw_watch_rules(cfg) == []


def test_watch_accepts_judgment_accion(tmp_path):
    cfg = _watch_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/watch urgent -> judgment", config=cfg))
    assert "Watch added" in reply
    assert "judgment" in reply


def test_watch_bad_syntax_usage_hint(tmp_path):
    cfg = _watch_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/watch no arrow here", config=cfg))
    assert "Usage" in reply


def test_unwatch_out_of_range(tmp_path):
    cfg = _watch_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/unwatch 5", config=cfg))
    assert "No watch rule at index 5" in reply


# ── /goal ────────────────────────────────────────────────────────────────────


def test_goal_schedules_recurring_judgment_task(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch(
            "/goal watch my inbox for invoices --every hourly",
            config=_cfg(instance_id="inst_personal"),
        )
    )
    assert "Goal scheduled (hourly)" in reply
    assert "watch my inbox for invoices" in reply

    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente"))
    assert len(tasks) == 1
    assert tasks[0]["accion"] == "goal"
    assert tasks[0]["recurrence"] == "3600"
    assert tasks[0]["params"].get("_judgment") is True


def test_goal_defaults_to_daily_cadence(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch("/goal keep an eye on the CV pipeline", config=_cfg(instance_id="inst_personal"))
    )
    assert "Goal scheduled (daily)" in reply
    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente"))
    assert tasks[0]["recurrence"] == "86400"


def test_goal_requires_text():
    reply = asyncio.run(commands.dispatch("/goal", config=_cfg()))
    assert "Usage" in reply


# ── /goal --verify / --turns (Wave A, TASK A2) ────────────────────────────────


def test_goal_verify_command_spec_mints_a_loop(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch(
            "/goal ship the release --verify command:true --turns 5",
            config=_cfg(instance_id="inst_personal"),
        )
    )
    assert "Goal scheduled (daily)" in reply
    assert "verify: command, max 5 turns" in reply

    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente"))
    assert len(tasks) == 1
    loop_id = tasks[0]["params"].get("_loop_id")
    assert loop_id

    loops = asyncio.run(db.list_loops("inst_personal"))
    assert len(loops) == 1
    assert loops[0]["id"] == loop_id
    assert loops[0]["max_turns"] == 5
    assert loops[0]["verifier_spec"] == {"type": "command", "cmd": "true"}
    assert loops[0]["origin"] == "telegram-goal"


def test_goal_verify_regex_spec_default_turns(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch(
            "/goal watch for READY --verify regex:READY",
            config=_cfg(instance_id="inst_personal"),
        )
    )
    assert "verify: regex, max 10 turns" in reply
    loops = asyncio.run(db.list_loops("inst_personal"))
    assert loops[0]["verifier_spec"] == {"type": "regex", "pattern": "READY"}
    assert loops[0]["max_turns"] == 10


def test_goal_verify_bad_regex_mints_nothing(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch(
            "/goal broken --verify regex:(unbalanced",
            config=_cfg(instance_id="inst_personal"),
        )
    )
    assert "Bad --verify spec" in reply
    assert asyncio.run(db.list_tasks("inst_personal", estado="pendiente")) == []
    assert asyncio.run(db.list_loops("inst_personal")) == []


def test_goal_verify_unknown_type_is_a_usage_error(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch(
            "/goal broken --verify sometype:x",
            config=_cfg(instance_id="inst_personal"),
        )
    )
    # Doesn't match the command:/regex: shorthand at all -> falls through as plain
    # goal text (no --verify recognised), which is the honest, defensive default.
    assert "Goal scheduled" in reply or "Usage" in reply


def test_goal_no_verify_is_byte_identical_to_before(tmp_path, monkeypatch):
    """Regression: no --verify -> no loop row, same reply shape as the old /goal."""
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch(
            "/goal watch my inbox for invoices --every hourly",
            config=_cfg(instance_id="inst_personal"),
        )
    )
    assert reply.endswith("]")  # no "[verify: ...]" suffix appended
    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente"))
    assert "_loop_id" not in tasks[0]["params"]
    assert asyncio.run(db.list_loops("inst_personal")) == []


# ── /code; parks a code_run approval, never runs directly ───────────────────


def test_code_requires_task():
    reply = asyncio.run(commands.dispatch("/code", config=_cfg()))
    assert "Usage" in reply
    assert "[[" not in reply  # no leaked marker on the usage hint


def test_code_parks_approval_with_expected_payload(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    from lhassa.agents import local_cli

    monkeypatch.setattr(local_cli, "detect_ai_cli", lambda: "claude")
    from lhassa.api import code_routes

    monkeypatch.setattr(code_routes, "_agent_cwd", lambda: tmp_path)

    reply = asyncio.run(
        commands.dispatch(
            "/code fix the failing test", config=_cfg(instance_id="inst_personal")
        )
    )

    # Voice: confirms what was parked, points at the coming button / fallback commands.
    assert "claude" in reply
    assert "fix the failing test" in reply
    assert "approve" in reply.lower()

    rows = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert len(rows) == 1
    row = rows[0]
    assert row["tipo"] == "code_run"
    payload = row["payload"]
    assert payload["accion"] == "code_run"
    assert payload["task"] == "fix the failing test"
    assert payload["cli"] == "claude"
    assert payload["cwd"] == str(tmp_path)

    # The hidden approval-id marker is present (the poller strips it before
    # sending) and matches the parked row's id.
    import re

    m = re.search(r"\[\[approval_id: (\S+)\]\]", reply)
    assert m and m.group(1) == row["id"]


def test_code_caps_task_length(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    from lhassa.agents import local_cli

    monkeypatch.setattr(local_cli, "detect_ai_cli", lambda: "codex")

    long_task = "x" * 900
    asyncio.run(commands.dispatch(f"/code {long_task}", config=_cfg(instance_id="inst_personal")))

    rows = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert len(rows[0]["payload"]["task"]) == 500


# ── /help renders every registered command + never drops an unlisted one ─────


def test_help_covers_every_registered_command_name():
    reply = asyncio.run(commands.dispatch("/help", config=_cfg()))
    for name in commands.COMMANDS.names():
        assert f"/{name}" in reply, f"/help is missing /{name}"


def test_help_shows_unlisted_command_via_fallback_line():
    async def _temp(args, *, config=None):
        return "ok"

    commands.register_command("zzzunlisted", _temp)
    try:
        reply = asyncio.run(commands.dispatch("/help", config=_cfg()))
        assert "Also available" in reply
        assert "/zzzunlisted" in reply
    finally:
        commands.COMMANDS.unregister("zzzunlisted")


# ── /report ────────────────────────────────────────────────────────────────────


def test_report_no_ledger_reports_zero(tmp_path):
    cfg = _cfg(llm_gateway_db_path=str(tmp_path / "absent.db"))
    reply = asyncio.run(commands.dispatch("/report", config=cfg))
    assert "Spend today: $0.0000" in reply
    assert "Last 7d: $0.0000" in reply


def test_report_reads_ledger_window(tmp_path):
    import sqlite3
    from datetime import datetime, timezone

    db_path = tmp_path / "llm_gateway.db"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE ledger (id INTEGER PRIMARY KEY, ts TEXT, provider TEXT, "
        "model TEXT, input_tokens INT, output_tokens INT, cost_usd REAL, cache_hit INT)"
    )
    conn.executemany(
        "INSERT INTO ledger(ts, provider, model, input_tokens, output_tokens, "
        "cost_usd, cache_hit) VALUES(?,?,?,?,?,?,?)",
        [
            (f"{today}T01:00:00+00:00", "opencode", "m", 1, 1, 0.01, 0),
            (f"{today}T02:00:00+00:00", "opencode", "m", 1, 1, 0.02, 0),
            ("2020-01-01T00:00:00+00:00", "opencode", "m", 1, 1, 9.99, 0),
        ],
    )
    conn.commit()
    conn.close()

    cfg = _cfg(llm_gateway_db_path=str(db_path))
    reply = asyncio.run(commands.dispatch("/report 3", config=cfg))
    assert "Spend today: $0.0300" in reply
    assert "Last 3d: $0.0300" in reply  # old-day row excluded


def test_report_bad_days_arg_usage_hint():
    reply = asyncio.run(commands.dispatch("/report soon", config=_cfg()))
    assert "Usage" in reply


# ── /memory ────────────────────────────────────────────────────────────────────


def test_memory_finds_fact_by_content(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    from lhassa.memory import facts

    asyncio.run(
        facts.add_fact(
            "inst_personal", "dato", "The owner prefers dark roast coffee.", tags=["prefs"]
        )
    )
    reply = asyncio.run(
        commands.dispatch("/memory dark roast", config=_cfg(instance_id="inst_personal"))
    )
    assert "dark roast" in reply
    assert "dato" in reply


def test_memory_finds_fact_by_tag(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    from lhassa.memory import facts

    asyncio.run(
        facts.add_fact("inst_personal", "dato", "Something unrelated.", tags=["standing"])
    )
    reply = asyncio.run(
        commands.dispatch("/memory standing", config=_cfg(instance_id="inst_personal"))
    )
    assert "Something unrelated" in reply


def test_memory_no_match(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch("/memory zzznomatch", config=_cfg(instance_id="inst_personal"))
    )
    assert "No facts match" in reply


def test_memory_requires_query():
    reply = asyncio.run(commands.dispatch("/memory", config=_cfg()))
    assert "Usage" in reply


# ── /knowledge ───────────────────────────────────────────────────────────────────


def test_knowledge_requires_query():
    reply = asyncio.run(commands.dispatch("/knowledge", config=_cfg()))
    assert "Usage" in reply


def test_knowledge_no_match(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch("/knowledge zzznomatch", config=_cfg(instance_id="inst_personal"))
    )
    assert "No indexed documents match" in reply


def test_knowledge_finds_indexed_chunk(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    root = tmp_path / "notes"
    root.mkdir()
    (root / "trip.md").write_text("The Amazon rainforest trip was unforgettable.")

    from lhassa.memory import knowledge

    cfg = _cfg(
        instance_id="inst_personal",
        knowledge_roots=str(root),
        knowledge_max_file_kb=512,
        knowledge_chunk_chars=2000,
    )
    asyncio.run(knowledge.ingest("inst_personal", config=cfg))

    reply = asyncio.run(commands.dispatch("/knowledge Amazon rainforest", config=cfg))
    assert "trip.md" in reply
    assert "Amazon" in reply


# ── /capabilities ──────────────────────────────────────────────────────────────


def test_capabilities_reports_wired_and_toggles():
    reply = asyncio.run(commands.dispatch("/capabilities", config=_cfg()))
    assert "Providers:" in reply
    assert "Channels active:" in reply
    assert "Org:" in reply
    assert "Monitor:" in reply
    assert "Voice:" in reply


def test_capabilities_never_raises_on_degraded_config(monkeypatch):
    import lhassa.llm.provider as provider_mod

    monkeypatch.setattr(provider_mod, "PROVIDERS", None, raising=False)
    reply = asyncio.run(commands.dispatch("/capabilities", config=_cfg()))
    assert isinstance(reply, str) and reply


# ── /tools ─────────────────────────────────────────────────────────────────────


def test_tools_lists_self_written_tools(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    asyncio.run(
        db.upsert_tool(
            "inst_personal", "fetch_weather", dominio="utility", estado="cuarentena"
        )
    )
    reply = asyncio.run(
        commands.dispatch("/tools", config=_cfg(instance_id="inst_personal"))
    )
    assert "fetch_weather" in reply
    assert "cuarentena" in reply


def test_tools_empty_reports_none(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch("/tools", config=_cfg(instance_id="inst_personal"))
    )
    assert "No self-written tools yet." in reply


# ── /skills ────────────────────────────────────────────────────────────────────


def test_skills_lists_installed_skills(tmp_path):
    from lhassa.skills.skillmd import Skill
    from lhassa.skills.store import SkillStore

    cfg = _cfg(skills_dir=str(tmp_path / "skills"))
    store = SkillStore(cfg)
    store.create(Skill(name="research", description="Gather and cross-check sources."))

    reply = asyncio.run(commands.dispatch("/skills", config=cfg))
    assert "research" in reply
    assert "Gather and cross-check sources" in reply


def test_skills_empty_reports_none(tmp_path):
    cfg = _cfg(skills_dir=str(tmp_path / "empty_skills"))
    reply = asyncio.run(commands.dispatch("/skills", config=cfg))
    assert "No skills installed." in reply


# ── /org ─────────────────────────────────────────────────────────────────────


def test_org_renders_standalone_admin(tmp_path):
    cfg = _cfg(org_topology_file=str(tmp_path / "missing_topology.json"), org_role="")
    reply = asyncio.run(commands.dispatch("/org", config=cfg))
    assert reply.startswith("Org chart:")
    assert "lhassa" in reply
    assert "★" in reply


def test_org_renders_departments_indented(tmp_path):
    cfg = _cfg(
        org_topology_file=str(tmp_path / "missing_topology.json"),
        org_role="general",
        org_departments="finance,optimization",
    )
    reply = asyncio.run(commands.dispatch("/org", config=cfg))
    assert "dept:finance" in reply
    assert "dept:optimization" in reply
    # departments are indented under the root.
    assert "  ● dept:finance" in reply or "  ○ dept:finance" in reply


def test_org_degrades_on_failure(monkeypatch):
    from lhassa.org import topology as topo_mod

    def _boom(config=None):
        raise RuntimeError("topology file corrupt")

    monkeypatch.setattr(topo_mod, "load_topology", _boom)
    reply = asyncio.run(commands.dispatch("/org", config=_cfg()))
    assert "couldn't" in reply


# ── /fleet ───────────────────────────────────────────────────────────────────


def test_fleet_lists_known_machines(monkeypatch):
    async def _fake_get_fleet():
        return [
            {"machine_id": "laptop-01", "last_checkin": "2026-07-01T00:00:00Z", "desired_state": {"mode": "on"}},
        ]

    import lhassa.api.fleet_routes as fleet_routes

    monkeypatch.setattr(fleet_routes, "get_fleet", _fake_get_fleet)
    reply = asyncio.run(commands.dispatch("/fleet", config=_cfg()))
    assert "laptop-01" in reply
    assert "mode=on" in reply


def test_fleet_empty_reports_none(monkeypatch):
    async def _fake_get_fleet():
        return []

    import lhassa.api.fleet_routes as fleet_routes

    monkeypatch.setattr(fleet_routes, "get_fleet", _fake_get_fleet)
    reply = asyncio.run(commands.dispatch("/fleet", config=_cfg()))
    assert "No machines known yet." in reply


def test_fleet_degrades_on_failure(monkeypatch):
    async def _boom():
        raise RuntimeError("db unreachable")

    import lhassa.api.fleet_routes as fleet_routes

    monkeypatch.setattr(fleet_routes, "get_fleet", _boom)
    reply = asyncio.run(commands.dispatch("/fleet", config=_cfg()))
    assert "couldn't" in reply


# ── /version ─────────────────────────────────────────────────────────────────


def test_version_reports_version_and_toggles():
    reply = asyncio.run(commands.dispatch("/version", config=_cfg()))
    assert "Lhassa" in reply
    assert "Python" in reply
    assert "Instance:" in reply


def test_version_degrades_on_failure(monkeypatch):
    import lhassa.cli.extra_cmds as extra_cmds

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(extra_cmds, "version_info", _boom)
    reply = asyncio.run(commands.dispatch("/version", config=_cfg()))
    assert "couldn't" in reply


# ── the bot menu is the registry (setMyCommands source) ───────────────────────────


def test_menu_entries_cover_every_registered_command():
    """The Telegram menu can never drift from the registry: every registered
    command appears, described ones first, undescribed ones with a fallback."""
    entries = commands.menu_entries()
    names = [n for n, _ in entries]
    assert set(names) >= set(commands.COMMANDS.names())
    # descriptions satisfy Telegram's constraints
    import re as _re

    for name, desc in entries:
        assert _re.fullmatch(r"[a-z0-9_]{1,32}", name)
        assert 3 <= len(desc) <= 256


def test_menu_entries_fallback_for_undescribed(monkeypatch):
    async def dummy(args, *, config=None):
        return "ok"

    commands.register_command("zzmenutest", dummy)
    try:
        entries = dict(commands.menu_entries())
        assert "zzmenutest" in entries
        assert "help" in entries["zzmenutest"]  # the generic fallback line
    finally:
        commands.COMMANDS._items.pop("zzmenutest", None)


# ── /remind; deterministic timed reminders from plain words ──────────────────────


def test_parse_remind_handles_the_owner_phrases():
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc).astimezone()

    # "a las 5pm" → 17:00 local today (future)
    run_at, what, when = commands.parse_remind("de un compromiso a las 5pm", now=now)
    local = run_at.astimezone()
    assert (local.hour, local.minute) == (17, 0)
    assert what == "un compromiso"
    assert when == "at 17:00"

    # past time rolls to tomorrow
    run_at, _, _ = commands.parse_remind("standup at 8am", now=now)
    assert run_at.astimezone().date() == (now + timedelta(days=1)).date()

    # relative
    run_at, what, _ = commands.parse_remind("in 45 min estirar", now=now)
    assert abs((run_at - now.astimezone(timezone.utc)).total_seconds() - 2700) < 2
    assert what == "estirar"

    # mañana + hora
    run_at, what, _ = commands.parse_remind("pagar arriendo mañana a las 9am", now=now)
    assert run_at.astimezone().date() == (now + timedelta(days=1)).date()
    assert run_at.astimezone().hour == 9
    assert what == "pagar arriendo"

    # no time phrase → None
    assert commands.parse_remind("que la vida es bella") is None


def test_remind_command_mints_a_send_reminder_task(tmp_path, monkeypatch):
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(
        commands.dispatch(
            "/remind llamar al banco a las 5pm", config=_cfg(instance_id="inst_personal")
        )
    )
    assert reply.startswith("⏰ Noted")
    assert "llamar al banco" in reply
    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=10))
    match = [t for t in tasks if t.get("accion") == "send_reminder"]
    assert len(match) == 1
    assert match[0]["params"]["text"] == "⏰ llamar al banco"


def test_remind_without_time_gives_usage_hint(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(commands.dispatch("/remind algo sin hora", config=_cfg()))
    assert "what and when" in reply


# ── /remind + /event; the additive .ics calendar delivery ────────────────────


def _fake_deliver(calls):
    async def fake(*, document=None, caption="", kind="message", metadata=None,
                    origin_department="", config=None, **kwargs):
        calls.append(
            {
                "document": document,
                "caption": caption,
                "kind": kind,
                "metadata": metadata,
                "origin_department": origin_department,
            }
        )
        return True, "art_fake"

    return fake


def test_remind_also_delivers_a_calendar_event(tmp_path, monkeypatch):
    from lhassa.org import deliver as deliver_mod

    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    calls: list = []
    monkeypatch.setattr(deliver_mod, "deliver", _fake_deliver(calls))

    reply = asyncio.run(
        commands.dispatch(
            "/remind llamar al banco a las 5pm", config=_cfg(instance_id="inst_personal")
        )
    )

    # the internal reminder UX is unchanged
    assert reply.startswith("⏰ Noted")
    assert "llamar al banco" in reply
    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=10))
    match = [t for t in tasks if t.get("accion") == "send_reminder"]
    assert len(match) == 1

    # AND a calendar_event was delivered
    assert len(calls) == 1
    call = calls[0]
    assert call["kind"] == "calendar_event"
    assert str(call["document"]).endswith(".ics")
    assert call["metadata"]["summary"] == "llamar al banco"
    assert call["origin_department"] == ""


def test_remind_survives_calendar_delivery_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    db = asyncio.run(_init_db(tmp_path, monkeypatch))

    async def boom(**kwargs):
        raise RuntimeError("deliver is down")

    from lhassa.org import deliver as deliver_mod

    monkeypatch.setattr(deliver_mod, "deliver", boom)

    reply = asyncio.run(
        commands.dispatch(
            "/remind llamar al banco a las 5pm", config=_cfg(instance_id="inst_personal")
        )
    )
    # the reminder still works even though the .ics delivery blew up
    assert reply.startswith("⏰ Noted")
    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=10))
    match = [t for t in tasks if t.get("accion") == "send_reminder"]
    assert len(match) == 1


def test_event_command_delivers_ics_with_no_reminder_task(tmp_path, monkeypatch):
    from lhassa.org import deliver as deliver_mod

    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    db = asyncio.run(_init_db(tmp_path, monkeypatch))
    calls: list = []
    monkeypatch.setattr(deliver_mod, "deliver", _fake_deliver(calls))

    reply = asyncio.run(
        commands.dispatch(
            "/event dentist appointment at 3pm", config=_cfg(instance_id="inst_personal")
        )
    )

    assert reply.startswith("📅 Sent")
    assert "dentist appointment" in reply

    assert len(calls) == 1
    assert calls[0]["kind"] == "calendar_event"
    assert str(calls[0]["document"]).endswith(".ics")

    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=10))
    match = [t for t in tasks if t.get("accion") == "send_reminder"]
    assert match == []


def test_event_command_degrades_when_delivery_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_CALENDAR_DIR", str(tmp_path / "cal"))
    asyncio.run(_init_db(tmp_path, monkeypatch))

    async def fails(**kwargs):
        return False, None

    from lhassa.org import deliver as deliver_mod

    monkeypatch.setattr(deliver_mod, "deliver", fails)

    reply = asyncio.run(
        commands.dispatch(
            "/event dentist appointment at 3pm", config=_cfg(instance_id="inst_personal")
        )
    )
    assert "couldn't" in reply.lower()


def test_event_without_time_gives_usage_hint(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(commands.dispatch("/event algo sin hora", config=_cfg()))
    assert "what and when" in reply


# ── /ai; search + model synthesis (a specific answer, not links) ──────────────────


def test_ai_synthesizes_an_answer_from_search_results(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    seen = {}

    async def fake_web(query, max_results=4):
        seen["query"] = query
        return [
            {"title": "Vuelos MAD-BOG", "body": "desde €520 ida y vuelta", "url": "https://x.com/a"},
        ]

    import lhassa.tools.search as search_mod

    monkeypatch.setattr(search_mod, "web_search", fake_web)
    monkeypatch.setattr(search_mod, "news_search", fake_web)

    class _Prov:
        name = "default"

        async def complete(self, messages, *, model=None, max_tokens=None, system=None):
            seen["system"] = system
            seen["user"] = messages[0]["content"]
            return SimpleNamespace(text="Los vuelos rondan €520 ida y vuelta (fuente: Vuelos MAD-BOG).", cost_usd=0.001)

    import lhassa.llm.provider as provider_mod
    import lhassa.llm.router as router_mod

    monkeypatch.setattr(provider_mod, "get_model_provider", lambda config, name=None: _Prov())
    monkeypatch.setattr(router_mod, "pick_provider", lambda *, sensitive, hard, config: _Prov())
    monkeypatch.setattr(router_mod, "pick_model", lambda p, *, hard, config: "m")
    monkeypatch.setattr(router_mod, "fallback_chain", lambda cfg, primary: [])

    reply = asyncio.run(
        commands.dispatch("/ai cuánto vale un vuelo Madrid-Bogotá", config=_cfg(instance_id="inst_personal"))
    )
    assert "€520" in reply
    assert "cuánto vale un vuelo Madrid-Bogotá" in seen["query"]
    assert "Live web results" in seen["user"]
    assert "€520" in seen["user"]  # the search results were fed to the model


def test_ai_kill_switch_stops_before_later_configured_provider(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    from lhassa.llm.gateway import KillSwitchActive

    async def fake_web(query, max_results=4):
        return []

    import lhassa.tools.search as search_mod

    monkeypatch.setattr(search_mod, "web_search", fake_web)
    monkeypatch.setattr(search_mod, "news_search", fake_web)

    class _Prov:
        def __init__(self, name, exc=None):
            self.name = name
            self.exc = exc
            self.calls = 0

        async def complete(self, messages, **kwargs):
            self.calls += 1
            if self.exc:
                raise self.exc
            return SimpleNamespace(text="must not run", cost_usd=0.0)

    killed = _Prov("default", KillSwitchActive("manual stop"))
    later = _Prov("claude")

    async def _ordered(_cfg):
        return [(killed, "m"), (later, "m")]

    monkeypatch.setattr(commands, "_resolve_chat_provider", _ordered)

    reply = asyncio.run(commands.dispatch("/ai current answer", config=_cfg()))

    assert "reach a model" in reply
    assert killed.calls == 1
    assert later.calls == 0


def test_ai_without_question_gives_hint(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    reply = asyncio.run(commands.dispatch("/ai", config=_cfg()))
    assert "look something up" in reply


# ── /calendar, /calendars ──────────────────────────────────────────────────────


def _cal_cfg(tmp_path, **over):
    return _cfg(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url="",
        **over,
    )


def test_calendars_empty_gives_a_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "No calendars configured" in reply


def test_calendar_add_list_remove_roundtrip(tmp_path):
    cfg = _cal_cfg(tmp_path)

    added = asyncio.run(
        commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg)
    )
    assert "Calendar added: Work" in added

    listed = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "Work" in listed
    # the url is masked, not echoed in full for a long url
    long_url = "https://example.edu/" + "x" * 60 + ".ics"
    asyncio.run(commands.dispatch(f"/calendar add Long {long_url}", config=cfg))
    listed2 = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert long_url not in listed2

    removed = asyncio.run(commands.dispatch("/calendar remove Work", config=cfg))
    assert "Calendar removed: Work" in removed

    listed3 = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "Work" not in listed3


def test_calendar_add_rejects_bad_url(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar add Bad not-a-url", config=cfg))
    assert "Couldn't add" in reply


def test_calendar_add_missing_args_usage_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar add OnlyName", config=cfg))
    assert "Usage" in reply


def test_calendar_remove_unknown_name(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar remove Ghost", config=cfg))
    assert "No calendar named" in reply


def test_calendar_no_args_usage_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar", config=cfg))
    assert "Usage" in reply


def test_calendar_unknown_subcommand(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar frobnicate", config=cfg))
    assert "Unknown /calendar sub-command" in reply


def test_calendar_view_no_when_usage_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar view", config=cfg))
    assert "Usage" in reply


def test_calendar_view_unparseable_when(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar view blorp", config=cfg))
    assert "Couldn't work out a time reference" in reply


def test_calendar_view_forces_a_deterministic_answer(tmp_path, monkeypatch):
    """``/calendar view friday`` answers even though a bare 'friday' has no
    calendar keyword; the command itself is the intent signal."""
    cfg = _cal_cfg(tmp_path)

    from lhassa.calendar import feed as feed_mod

    async def _fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return []

    monkeypatch.setattr(feed_mod, "upcoming", _fake_upcoming)

    reply = asyncio.run(commands.dispatch("/calendar view friday", config=cfg))
    assert reply == "Friday you don't have anything on your calendar."


def test_calendars_shows_env_shortcut_masked(tmp_path):
    full_url = "https://example.com/shortcut-secret-feed-" + "z" * 30 + ".ics"
    cfg = _cfg(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url=full_url,
    )
    reply = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "default" in reply
    assert full_url not in reply  # the full secret url is never echoed back


# ── /calendar primary + /calendar sync; master calendar reconcile ────────────


def test_calendar_primary_sets_master(tmp_path):
    cfg = _cal_cfg(tmp_path)
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))

    reply = asyncio.run(commands.dispatch("/calendar primary Work", config=cfg))
    assert "Master calendar set: Work" in reply

    from lhassa.calendar import store as _cal_store

    assert _cal_store.get_primary(config=cfg) == "Work"


def test_calendar_primary_unknown_name(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar primary Ghost", config=cfg))
    assert "No calendar named" in reply


def test_calendar_primary_missing_arg_usage_hint(tmp_path):
    cfg = _cal_cfg(tmp_path)
    reply = asyncio.run(commands.dispatch("/calendar primary", config=cfg))
    assert "Usage" in reply


def test_calendars_marks_the_primary(tmp_path):
    cfg = _cal_cfg(tmp_path)
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))
    asyncio.run(commands.dispatch("/calendar add Personal https://example.com/p.ics", config=cfg))
    asyncio.run(commands.dispatch("/calendar primary Work", config=cfg))

    listed = asyncio.run(commands.dispatch("/calendars", config=cfg))
    assert "Work (primary)" in listed
    assert "Personal (primary)" not in listed


def test_calendar_sync_reports_no_primary(tmp_path):
    cfg = _cal_cfg(tmp_path)
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))

    reply = asyncio.run(commands.dispatch("/calendar sync", config=cfg))
    assert "no primary" in reply.lower()


def test_calendar_sync_reports_found_and_delivered(tmp_path, monkeypatch):
    cfg = _cal_cfg(tmp_path, instance_id="inst_personal")
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))
    asyncio.run(commands.dispatch("/calendar primary Work", config=cfg))

    from lhassa.actionpack import _calendar_sync

    async def fake_sync(params):
        return "calendar_sync: 2 unsynced event(s), 2 delivered"

    monkeypatch.setattr(_calendar_sync, "calendar_sync", fake_sync)

    reply = asyncio.run(commands.dispatch("/calendar sync", config=cfg))
    assert "I found 2 event" in reply
    assert "sent you 2" in reply


def test_calendar_sync_reports_nothing_to_sync(tmp_path, monkeypatch):
    cfg = _cal_cfg(tmp_path, instance_id="inst_personal")
    asyncio.run(commands.dispatch("/calendar add Work https://example.edu/feed.ics", config=cfg))
    asyncio.run(commands.dispatch("/calendar primary Work", config=cfg))

    from lhassa.actionpack import _calendar_sync

    async def fake_sync(params):
        return "calendar_sync: 0 unsynced event(s), 0 delivered"

    monkeypatch.setattr(_calendar_sync, "calendar_sync", fake_sync)

    reply = asyncio.run(commands.dispatch("/calendar sync", config=cfg))
    assert "already in sync" in reply.lower()
