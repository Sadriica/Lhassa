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
    """Make any attempt to construct/call a provider blow up — proving /status is
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
        db.add_approval("inst_personal", "task", {"accion": "run_command"})
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
        db.add_approval("inst_personal", "task", {"accion": "run_command"})
    )
    reply = asyncio.run(
        commands.dispatch(f"/approve {ap_id}", config=_cfg(instance_id="inst_personal"))
    )
    assert "approved" in reply
    remaining = asyncio.run(db.load_pending_approvals("inst_personal"))
    assert remaining == []


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


# ── /code — parks a code_run approval, never runs directly ───────────────────


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


# ── /remind — deterministic timed reminders from plain words ──────────────────────


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
