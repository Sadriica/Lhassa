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
