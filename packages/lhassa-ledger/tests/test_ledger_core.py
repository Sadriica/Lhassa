# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ledger core.

The invariants worth guarding are the ones that make a report trustworthy:
an event is never written twice, a cost never appears without its source,
and a host failure never becomes our failure.
"""

from __future__ import annotations

import time

import pytest

from agent_ledger import query
from agent_ledger.event import (
    COST_FROM_HOST,
    COST_FROM_TABLE,
    COST_UNKNOWN,
    KIND_LLM_CALL,
    KIND_TOOL_CALL,
    OUTCOME_PENDING,
    AgentEvent,
)
from agent_ledger.pricing import cost_of
from agent_ledger.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "ledger.db")


def test_event_requires_a_host():
    with pytest.raises(ValueError):
        AgentEvent(host="", kind=KIND_LLM_CALL)


def test_event_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        AgentEvent(host="hermes", kind="whatever")


def test_a_cost_cannot_be_written_without_its_source():
    # The whole point of the ledger is that every figure can be traced back.
    with pytest.raises(ValueError):
        AgentEvent(host="hermes", kind=KIND_LLM_CALL, cost_usd=0.01, cost_source=COST_UNKNOWN)


def test_recording_the_same_id_twice_does_not_double_count(store):
    event = AgentEvent(id="fixed", host="hermes", kind=KIND_LLM_CALL, output_tokens=10)
    assert store.record(event)
    assert store.record(event)
    assert store.count() == 1


def test_a_broken_event_never_raises_into_the_host(store):
    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("host state is gone")

    assert store.record(Exploding()) is False
    assert store.count() == 0


def test_events_round_trip_with_their_detail(store):
    store.record(
        AgentEvent(host="orca", kind=KIND_TOOL_CALL, tool="bash", target="ls -la",
                   detail={"worktree": "feature-x"})
    )
    (loaded,) = list(store.events())
    assert loaded.host == "orca"
    assert loaded.target == "ls -la"
    assert loaded.detail == {"worktree": "feature-x"}


def test_unknown_model_yields_no_cost_rather_than_a_guess():
    cost, source = cost_of(model="some-model-nobody-priced", input_tokens=1000)
    assert cost is None
    assert source == COST_UNKNOWN


def test_known_model_is_priced_from_the_table():
    cost, source = cost_of(model="deepseek-v4-flash", input_tokens=1_000_000, output_tokens=0)
    assert source == COST_FROM_TABLE
    assert cost == pytest.approx(0.07)


def test_longest_model_match_wins():
    # "deepseek-v4-flash" must beat a shorter "deepseek" entry.
    cost, _ = cost_of(model="deepseek-v4-flash-preview", output_tokens=1_000_000)
    assert cost == pytest.approx(0.28)


def test_a_host_reported_cost_beats_our_arithmetic():
    cost, source = cost_of(model="deepseek-v4-flash", input_tokens=1_000_000, reported_cost=0.99)
    assert (cost, source) == (0.99, COST_FROM_HOST)


def test_subscription_hosts_report_a_real_zero():
    cost, source = cost_of(model="claude-opus-5", output_tokens=50_000, host="claude-code")
    assert cost == 0.0
    assert source == COST_FROM_TABLE


def test_totals_report_their_own_blind_spot(store):
    store.record(AgentEvent(host="hermes", kind=KIND_LLM_CALL, output_tokens=10,
                            cost_usd=0.5, cost_source=COST_FROM_HOST))
    store.record(AgentEvent(host="hermes", kind=KIND_LLM_CALL, output_tokens=10))
    total = query.totals(store, days=1)
    assert total.events == 2
    assert total.cost_usd == 0.5
    assert total.unpriced == 1
    assert total.trustworthy is False


def test_spend_splits_by_host(store):
    store.record(AgentEvent(host="hermes", kind=KIND_LLM_CALL, cost_usd=0.2, cost_source=COST_FROM_HOST))
    store.record(AgentEvent(host="claude-code", kind=KIND_LLM_CALL, cost_usd=0.0, cost_source=COST_FROM_TABLE))
    hosts = {row.label: row for row in query.by_host(store, days=1)}
    assert hosts["hermes"].cost_usd == 0.2
    assert hosts["claude-code"].cost_usd == 0.0


def test_old_events_fall_outside_the_window(store):
    store.record(AgentEvent(host="hermes", kind=KIND_LLM_CALL, ts=time.time() - 10 * 86400))
    assert query.totals(store, days=1).events == 0
    assert query.totals(store, days=30).events == 1


def test_pending_decisions_are_listed_oldest_first(store):
    now = time.time()
    store.record(AgentEvent(host="hermes", kind="decision", outcome=OUTCOME_PENDING,
                            tool="deploy", ts=now - 100))
    store.record(AgentEvent(host="hermes", kind="decision", outcome=OUTCOME_PENDING,
                            tool="send_email", ts=now - 500))
    rows = query.pending_decisions(store)
    assert [r["tool"] for r in rows] == ["send_email", "deploy"]


def test_days_are_ordered_newest_first(store):
    now = time.time()
    for offset in (0, 86400, 2 * 86400):
        store.record(AgentEvent(host="hermes", kind=KIND_LLM_CALL, ts=now - offset,
                                cost_usd=offset / 1e6, cost_source=COST_FROM_HOST))
    labels = [row.label for row in query.by_day(store, days=7)]
    assert labels == sorted(labels, reverse=True)


def test_a_free_tier_model_is_a_real_zero_not_an_unknown():
    # Found while testing against a live Hermes turn: the router's free tier
    # was landing in the unpriced bucket and dragging every total's
    # trustworthiness down for no reason.
    cost, source = cost_of(model="tencent/hy3:free", input_tokens=22_776, output_tokens=16)
    assert cost == 0.0
    assert source == COST_FROM_TABLE


def test_a_paid_model_is_not_mistaken_for_a_free_one():
    cost, source = cost_of(model="some-model-freedom", input_tokens=1000)
    assert cost is None
    assert source == COST_UNKNOWN


def test_tool_calls_do_not_count_as_unpriced(store):
    # Caught against live Hermes data: a costless event is not a blind spot,
    # and treating it as one made every report with tool use look unreliable.
    store.record(AgentEvent(host="hermes", kind=KIND_LLM_CALL, output_tokens=10,
                            cost_usd=0.01, cost_source=COST_FROM_HOST))
    for _ in range(5):
        store.record(AgentEvent(host="hermes", kind=KIND_TOOL_CALL, tool="read_file"))
    total = query.totals(store, days=1)
    assert total.events == 6
    assert total.unpriced == 0
    assert total.trustworthy is True


def test_a_metered_call_without_a_price_still_counts_as_unpriced(store):
    store.record(AgentEvent(host="hermes", kind=KIND_LLM_CALL, output_tokens=10))
    assert query.totals(store, days=1).unpriced == 1


def test_model_and_tool_breakdowns_do_not_bleed_into_each_other(store):
    store.record(AgentEvent(host="hermes", kind=KIND_LLM_CALL, model="m1", output_tokens=5))
    store.record(AgentEvent(host="hermes", kind=KIND_TOOL_CALL, tool="bash"))
    assert [r.label for r in query.by_model(store, days=1)] == ["m1"]
    assert [r.label for r in query.by_tool(store, days=1)] == ["bash"]
