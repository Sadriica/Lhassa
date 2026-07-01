"""Tests for lhassa.org.watcher — the always-on, $0 monitor watcher.

Fully offline + $0: ``gather_signals`` / ``notify`` / ``asyncio.sleep`` are patched,
no real loop ever spins and no model is ever called. The pure helpers
(``detect_events`` / ``is_active``) are exercised directly; ``react`` is checked for
its always-log-episode + notify-only-when-important + de-dupe behaviour; and
``watch_loop(once=True)`` / a sleep-raising continuous tick assert the adaptive
cadence.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.org import watcher


class _Stop(BaseException):
    """A BaseException so it escapes the loop's ``except Exception`` and breaks it."""


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset the module-level loop/de-dupe state around every test."""
    watcher._STATE["prev"] = None
    watcher._STATE["last_alert_fp"] = None
    yield
    watcher._STATE["prev"] = None
    watcher._STATE["last_alert_fp"] = None


def _cfg(**kw):
    base = dict(
        instance_id="inst_personal",
        monitor_department="interns",
        monitor_notify=True,
        monitor_think=False,
        monitor_enabled=True,
        monitor_idle_interval=300.0,
        monitor_active_interval=15.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _snap(*, failures=0, worst="info", approvals=0, runs=None):
    return {
        "failures": [{"id": i} for i in range(failures)],
        "health": {"worst_level": worst, "failing": []},
        "approvals": [{"id": i} for i in range(approvals)],
        "agent_runs": runs or {},
    }


async def _init_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


# ── detect_events: pure, deterministic diff ───────────────────────────────────────


def test_detect_identical_and_none_yield_no_events():
    s = _snap(failures=2, worst="alert", approvals=1, runs={"r1": "running"})
    assert watcher.detect_events(s, s) == []
    assert watcher.detect_events(None, s) == []  # first tick baselines silently


def test_detect_new_failures():
    ev = watcher.detect_events(_snap(failures=1), _snap(failures=3))
    fail = [e for e in ev if e["kind"] == "failures_new"]
    assert len(fail) == 1
    assert "2 new failure" in fail[0]["summary"]


def test_detect_health_worsened_only_upward():
    ev = watcher.detect_events(_snap(worst="info"), _snap(worst="alert"))
    health = [e for e in ev if e["kind"] == "health_worsened"]
    assert len(health) == 1 and health[0]["level"] == "alert"
    # improving (alert → info) is NOT an event
    assert watcher.detect_events(_snap(worst="alert"), _snap(worst="info")) == []


def test_detect_new_approvals():
    ev = watcher.detect_events(_snap(approvals=0), _snap(approvals=2))
    assert any(e["kind"] == "approvals_new" for e in ev)


def test_detect_run_started_and_finished():
    started = watcher.detect_events(_snap(runs={}), _snap(runs={"r1": "running"}))
    assert [e["kind"] for e in started] == ["run_started"]

    finished = watcher.detect_events(
        _snap(runs={"r1": "running"}), _snap(runs={"r1": "done"})
    )
    assert [e["kind"] for e in finished] == ["run_finished"]


# ── is_active: reflects state.agent_runs ──────────────────────────────────────────


def test_is_active_reflects_agent_runs(monkeypatch):
    from lhassa.state import AgentRun, state

    monkeypatch.setattr(state, "agent_runs", {})
    assert watcher.is_active(_cfg()) is False

    monkeypatch.setattr(
        state, "agent_runs", {"r1": AgentRun(id="r1", name="x", status="running")}
    )
    assert watcher.is_active(_cfg()) is True

    monkeypatch.setattr(
        state, "agent_runs", {"r1": AgentRun(id="r1", name="x", status="done")}
    )
    assert watcher.is_active(_cfg()) is False


# ── react: episode always; notify only on important; de-dupe ──────────────────────


@pytest.mark.asyncio
async def test_react_logs_episode_notifies_important_and_dedupes(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)

    calls: list[tuple] = []

    async def fake_notify(text, *, level="info", config=None):
        calls.append((text, level))
        return True

    import lhassa.notify.dispatch as dispatch

    monkeypatch.setattr(dispatch, "notify", fake_notify)

    important = [
        {"kind": "failures_new", "level": "alert", "summary": "2 new failure(s) (total 2)"}
    ]

    r1 = await watcher.react(important, department="interns", config=_cfg())
    assert r1["notified"] is True
    assert len(calls) == 1 and calls[0][1] == "error"
    assert "monitor · interns" in calls[0][0]

    # De-dupe: an identical STANDING important set does not re-notify …
    await watcher.react(important, department="interns", config=_cfg())
    assert len(calls) == 1

    # … but the episode is logged every time (observability, $0).
    eps = await db.load_recent_episodes("inst_personal", 10)
    monitor_eps = [e for e in eps if e.get("despertar_por") == "monitor"]
    assert len(monitor_eps) == 2


@pytest.mark.asyncio
async def test_react_run_event_logs_but_does_not_notify(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)

    calls: list = []

    async def fake_notify(text, *, level="info", config=None):
        calls.append(text)
        return True

    import lhassa.notify.dispatch as dispatch

    monkeypatch.setattr(dispatch, "notify", fake_notify)

    ev = [{"kind": "run_started", "level": "info", "summary": "run r1 started"}]
    r = await watcher.react(ev, department="interns", config=_cfg())

    assert calls == []  # run start/finish is episode-only
    assert r["important"] == 0
    eps = await db.load_recent_episodes("inst_personal", 5)
    assert any(e.get("despertar_por") == "monitor" for e in eps)


@pytest.mark.asyncio
async def test_react_notify_disabled_suppresses_alert(tmp_path, monkeypatch):
    await _init_db(tmp_path, monkeypatch)

    calls: list = []

    async def fake_notify(text, *, level="info", config=None):
        calls.append(text)
        return True

    import lhassa.notify.dispatch as dispatch

    monkeypatch.setattr(dispatch, "notify", fake_notify)

    important = [{"kind": "approvals_new", "level": "warn", "summary": "1 new pending approval(s) (total 1)"}]
    await watcher.react(important, department="interns", config=_cfg(monitor_notify=False))
    assert calls == []


@pytest.mark.asyncio
async def test_react_think_off_by_default_never_calls_monitor_pass(tmp_path, monkeypatch):
    await _init_db(tmp_path, monkeypatch)

    import lhassa.notify.dispatch as dispatch

    async def fake_notify(*a, **k):
        return True

    monkeypatch.setattr(dispatch, "notify", fake_notify)

    called = {"n": 0}

    async def fake_pass(**k):
        called["n"] += 1
        return {}

    monkeypatch.setattr(watcher, "monitor_pass", fake_pass)
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)  # live path off

    important = [{"kind": "failures_new", "level": "alert", "summary": "1 new failure(s) (total 1)"}]
    # think requested but LHASSA_ORG_LIVE off → still no token spend.
    await watcher.react(important, department="interns", config=_cfg(monitor_think=True))
    assert called["n"] == 0


# ── watch_loop: adaptive cadence + never-crash ────────────────────────────────────


@pytest.mark.asyncio
async def test_watch_loop_once_returns_idle_interval(monkeypatch):
    monkeypatch.setattr(watcher, "gather_signals", lambda c: _snap())
    from lhassa.state import state

    monkeypatch.setattr(state, "agent_runs", {})
    interval = await watcher.watch_loop(_cfg(), once=True)
    assert interval == 300.0


@pytest.mark.asyncio
async def test_watch_loop_cadence_active_vs_idle(monkeypatch):
    monkeypatch.setattr(watcher, "gather_signals", lambda c: _snap())
    from lhassa.state import AgentRun, state

    slept: list = []

    async def fake_sleep(secs):
        slept.append(secs)
        raise _Stop()

    monkeypatch.setattr(watcher.asyncio, "sleep", fake_sleep)

    # idle: nothing running → idle interval
    monkeypatch.setattr(state, "agent_runs", {})
    with pytest.raises(_Stop):
        await watcher.watch_loop(_cfg())
    assert slept == [300.0]

    # active: a running run → the short active interval
    watcher._STATE["prev"] = None
    slept.clear()
    monkeypatch.setattr(
        state, "agent_runs", {"r1": AgentRun(id="r1", name="x", status="running")}
    )
    with pytest.raises(_Stop):
        await watcher.watch_loop(_cfg())
    assert slept == [15.0]


@pytest.mark.asyncio
async def test_watch_loop_reacts_on_change_across_pokes(tmp_path, monkeypatch):
    await _init_db(tmp_path, monkeypatch)
    from lhassa.state import state

    monkeypatch.setattr(state, "agent_runs", {})

    seq = [_snap(failures=0), _snap(failures=2)]
    idx = {"i": 0}

    def fake_gather(_config):
        v = seq[min(idx["i"], len(seq) - 1)]
        idx["i"] += 1
        return dict(v)

    monkeypatch.setattr(watcher, "gather_signals", fake_gather)

    calls: list = []

    async def fake_notify(text, *, level="info", config=None):
        calls.append(text)
        return True

    import lhassa.notify.dispatch as dispatch

    monkeypatch.setattr(dispatch, "notify", fake_notify)

    # First poke: baselines the snapshot → no events, no alert.
    await watcher.watch_loop(_cfg(), once=True)
    assert calls == []

    # Second poke: failures rose → important event → deterministic alert.
    await watcher.watch_loop(_cfg(), once=True)
    assert len(calls) == 1 and "failure" in calls[0]


@pytest.mark.asyncio
async def test_watch_loop_never_crashes_and_backs_off(monkeypatch):
    def boom(_config):
        raise RuntimeError("signal source exploded")

    monkeypatch.setattr(watcher, "gather_signals", boom)

    slept: list = []

    async def fake_sleep(secs):
        slept.append(secs)
        raise _Stop()

    monkeypatch.setattr(watcher.asyncio, "sleep", fake_sleep)

    with pytest.raises(_Stop):
        await watcher.watch_loop(_cfg())
    # A failed tick degrades to a backoff sleep rather than crashing out.
    assert slept and slept[0] <= watcher._BACKOFF_CAP


# ── run_forever: disabled → no-op ─────────────────────────────────────────────────


def test_run_forever_disabled_is_a_noop(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("watch_loop must not run when disabled")

    monkeypatch.setattr(watcher.asyncio, "run", boom)
    rc = watcher.run_forever(_cfg(monitor_enabled=False))
    assert rc == 0
