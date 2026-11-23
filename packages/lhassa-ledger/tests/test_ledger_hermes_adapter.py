# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Hermes adapter.

Hermes calls these hooks inside a live turn, so the contract under test is
mostly about what must NOT happen: no exception may escape, and a hook that
fires without the library available must be a no-op rather than a crash.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from agent_ledger.event import COST_FROM_TABLE
from agent_ledger.store import Store

ADAPTER_PATH = Path(__file__).resolve().parents[1] / "adapters" / "hermes" / "__init__.py"


@pytest.fixture()
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LEDGER_DB", str(tmp_path / "ledger.db"))
    spec = importlib.util.spec_from_file_location("agent_ledger_hermes_adapter", ADAPTER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # The adapter caches its store; point it at the temp ledger for this test.
    module._store = Store(tmp_path / "ledger.db")
    return module


def _store(adapter):
    return adapter._get_store()


def test_register_wires_the_three_hooks(adapter):
    registered = {}

    class Ctx:
        def register_hook(self, name, fn):
            registered[name] = fn

    adapter.register(Ctx())
    assert set(registered) == {"post_api_request", "pre_tool_call", "post_tool_call"}


def test_register_leaves_one_plugin_loaded_note_a_day(adapter):
    # Silence in the ledger is ambiguous on its own: a host with no llm_call
    # or tool_call rows since some date either had no turns, or its plugin
    # stopped loading. A daily note tells the two apart.
    class Ctx:
        def register_hook(self, name, fn):
            pass

    adapter.register(Ctx())
    adapter.register(Ctx())  # a second load the same day must not add a row

    notes = [e for e in _store(adapter).events() if e.kind == "note"]
    assert len(notes) == 1
    assert notes[0].host == "hermes"
    assert notes[0].detail == {"note": "plugin_loaded"}


def test_register_never_raises_even_if_the_store_is_unavailable(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "_get_store", lambda: None)

    class Ctx:
        def register_hook(self, name, fn):
            pass

    adapter.register(Ctx())  # must not raise


def test_an_api_request_is_recorded_with_a_priced_cost(adapter):
    adapter.on_post_api_request(
        model="deepseek-v4-flash",
        provider="openai",
        usage={"input_tokens": 1_000_000, "output_tokens": 0},
        api_duration=1.5,
        session_id="s1",
        turn_id="t1",
        api_request_id="req-1",
        platform="telegram",
    )
    (event,) = list(_store(adapter).events())
    assert event.host == "hermes"
    assert event.model == "deepseek-v4-flash"
    assert event.cost_usd == pytest.approx(0.07)
    assert event.cost_source == COST_FROM_TABLE
    assert event.duration_ms == 1500
    assert event.agent == "telegram"


def test_the_same_api_request_id_is_only_billed_once(adapter):
    for _ in range(3):
        adapter.on_post_api_request(
            model="deepseek-v4-flash",
            usage={"output_tokens": 10},
            api_request_id="req-same",
        )
    assert _store(adapter).count() == 1


def test_a_tool_call_records_its_target_and_duration(adapter):
    adapter.on_pre_tool_call(tool_call_id="tc1")
    adapter.on_post_tool_call(
        tool_name="read_file",
        args={"path": "/etc/hosts"},
        result={"content": "..."},
        tool_call_id="tc1",
    )
    (event,) = list(_store(adapter).events())
    assert event.tool == "read_file"
    assert event.target == "/etc/hosts"
    assert event.outcome == "ok"


def test_tool_output_is_never_stored(adapter):
    secret = "a long tool result that must not reach the ledger"
    adapter.on_post_tool_call(tool_name="read_file", args={"path": "x"},
                              result={"content": secret}, tool_call_id="tc2")
    (event,) = list(_store(adapter).events())
    assert secret not in str(event.as_dict())


def test_a_failed_tool_is_marked_as_an_error(adapter):
    adapter.on_post_tool_call(tool_name="bash", args={"command": "false"},
                              result={"error": "exit 1"}, tool_call_id="tc3")
    (event,) = list(_store(adapter).events())
    assert event.outcome == "error"


def test_hooks_swallow_garbage_from_the_host(adapter):
    # Hermes evolves; a hook that receives an unexpected shape must not take
    # the turn down with it.
    adapter.on_post_api_request(model=None, usage="not-a-dict", api_duration="soon")
    adapter.on_post_tool_call(tool_name=None, args=object(), result=object())
    adapter.on_pre_tool_call()


def test_inflight_timers_are_bounded(adapter):
    for i in range(adapter._MAX_INFLIGHT + 10):
        adapter.on_pre_tool_call(tool_call_id=f"tc-{i}")
    assert len(adapter._inflight) <= adapter._MAX_INFLIGHT
