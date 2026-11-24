# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Waybar state writer: payload shape, location, atomic 0600 writes, the loop."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time

import pytest

from lhassa.views import status, waybar_collect, waybar_state

SNAP = {
    "instance_id": "inst_test",
    "budget": 1000,
    "pct_used": 0.1234567,
    "cost_usd": 0.420049,
    "soft_pct": 0.8,
    "approvals": 2,
    "tasks": None,
    "beat_age": 30,
    "health_failing": ("disk",),
    "health_alert": (),
}


#: The real blocking collector, kept before the hermetic fixture replaces it.
REAL_COLLECT = waybar_state.Publisher._collect_sync

NEUTRAL = {
    "ledger": {"agents": {"window_s": 300, "total": 0, "hosts": {}, "sessions": []},
               "today": {"events": 0, "api_usd": 0.0, "top_repo": ""}, "cwds": []},
    "review": {"critical": [], "pushes": [], "commits": 0, "push_count": 0},
    "pending": [], "services": [], "repos": [], "temp": None, "cpu": None,
    "battery": (None, False), "power_mode": "",
}


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    """Never read the real ledger, review log, systemd, sysfs or the network."""
    async def no_approvals(instance_id):
        return None

    monkeypatch.setattr(waybar_state, "_publisher", None)
    monkeypatch.setattr(waybar_state, "_pending_approvals", no_approvals)
    monkeypatch.setattr(waybar_state, "_lhassa_section", lambda now: {"health": "", "digest_age_s": None, "model": ""})
    monkeypatch.setattr(waybar_state.Publisher, "_collect_sync", lambda self, snap, now: json.loads(json.dumps(NEUTRAL)))


V1_KEYS = {
    "v", "written_at", "instance_id", "cost_usd", "budget", "pct_used", "soft_pct", "approvals", "tasks",
    "beat_age", "health_failing", "health_alert", "dashboard_url",
}


def test_build_state_shape():
    payload = waybar_state.build_state(SNAP, now=1000.0, url="http://127.0.0.1:7766")
    v2 = {k: payload.pop(k) for k in ("class", "agents", "attention", "system", "today", "lhassa")}
    assert v2["class"] == "warn"
    assert v2["attention"] == {"count": 0, "items": []}  # the writer fills these in
    assert v2["system"]["suggestion"] is None  # the slot for Lhassa's profile suggestion exists
    assert payload == {
        "v": 2,
        "written_at": 1000.0,
        "instance_id": "inst_test",
        "cost_usd": 0.42,
        "budget": 1000,
        "pct_used": 0.1235,
        "soft_pct": 0.8,
        "approvals": 2,
        "tasks": None,
        "beat_age": 30,
        "health_failing": ["disk"],
        "health_alert": [],
        "dashboard_url": "http://127.0.0.1:7766",
    }
    json.dumps(payload)


def test_build_state_unknown_fields_stay_null():
    payload = waybar_state.build_state({}, now=1.0)
    assert payload["approvals"] is None
    assert payload["health_failing"] is None
    assert payload["cost_usd"] == 0.0


def test_state_path_precedence(tmp_path):
    assert waybar_state.state_path({"LHASSA_WAYBAR_STATE_FILE": "/x/y.json"}).as_posix() == "/x/y.json"
    got = waybar_state.state_path({"XDG_RUNTIME_DIR": str(tmp_path)})
    assert got == tmp_path / "lhassa" / "waybar.json"


def test_state_path_fallback_is_private_tmp_not_cwd(tmp_path):
    for env in ({}, {"XDG_RUNTIME_DIR": str(tmp_path / "missing")}):
        got = waybar_state.state_path(env)
        assert got.name == "waybar.json"
        assert got.parent.name == f"lhassa-{os.getuid()}"


def test_dashboard_url():
    assert waybar_state.dashboard_url("0.0.0.0", 7766) == "http://127.0.0.1:7766"
    assert waybar_state.dashboard_url("::", 9000) == "http://127.0.0.1:9000"
    assert waybar_state.dashboard_url("::1", 7766) == "http://[::1]:7766"
    assert waybar_state.dashboard_url("localhost", 7766) == "http://localhost:7766"


def test_enabled_switch():
    assert waybar_state.enabled({})
    assert waybar_state.enabled({"LHASSA_WAYBAR_STATE": "1"})
    for off in ("0", "off", "False", "NO"):
        assert not waybar_state.enabled({"LHASSA_WAYBAR_STATE": off})


def test_write_is_0600_in_0700_dir_and_valid_json(tmp_path):
    target = tmp_path / "run" / "lhassa" / "waybar.json"
    waybar_state.write_state({"a": 1}, target)
    assert json.loads(target.read_text()) == {"a": 1}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert [p.name for p in target.parent.iterdir()] == ["waybar.json"]  # no temp left


def test_write_replaces_atomically_via_rename(tmp_path, monkeypatch):
    target = tmp_path / "waybar.json"
    waybar_state.write_state({"n": 1}, target)
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        # At rename time the old file is still whole and the new one is complete.
        seen.append((json.loads(target.read_text()), json.loads(open(src).read())))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    waybar_state.write_state({"n": 2}, target)
    assert seen == [({"n": 1}, {"n": 2})]
    assert json.loads(target.read_text()) == {"n": 2}


def test_failed_write_keeps_old_file_and_cleans_temp(tmp_path):
    target = tmp_path / "waybar.json"
    waybar_state.write_state({"n": 1}, target)
    with pytest.raises(TypeError):
        waybar_state.write_state({"bad": object()}, target)
    assert json.loads(target.read_text()) == {"n": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["waybar.json"]


def test_stale_temp_from_same_pid_is_replaced(tmp_path):
    target = tmp_path / "waybar.json"
    (tmp_path / f".waybar.json.{os.getpid()}.tmp").write_text("junk")
    waybar_state.write_state({"n": 3}, target)
    assert json.loads(target.read_text()) == {"n": 3}


async def test_refresh_once_writes_gathered_snapshot(tmp_path, monkeypatch):
    async def fake_gather(instance_id=None):
        return dict(SNAP)

    monkeypatch.setattr(status, "gather", fake_gather)
    target = tmp_path / "w.json"
    assert await waybar_state.refresh_once(target) == target
    data = json.loads(target.read_text())
    assert data["approvals"] == 2 and data["cost_usd"] == 0.42
    assert data["dashboard_url"].startswith("http://")


async def test_refresh_once_never_raises(tmp_path, monkeypatch):
    async def boom(instance_id=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(status, "gather", boom)
    assert await waybar_state.refresh_once(tmp_path / "w.json") is None


async def test_loop_refreshes_on_request_and_removes_file_on_cancel(tmp_path, monkeypatch):
    calls = []

    async def fake_gather(instance_id=None):
        calls.append(1)
        return {**SNAP, "approvals": len(calls)}

    monkeypatch.setattr(status, "gather", fake_gather)
    monkeypatch.delenv("LHASSA_WAYBAR_STATE", raising=False)
    target = tmp_path / "w.json"
    task = asyncio.create_task(waybar_state.run_loop(interval=3600, path=target))
    for _ in range(100):
        if target.exists():
            break
        await asyncio.sleep(0.01)
    assert json.loads(target.read_text())["approvals"] == 1
    waybar_state.request_refresh()
    for _ in range(100):
        if json.loads(target.read_text())["approvals"] == 2:
            break
        await asyncio.sleep(0.01)
    assert json.loads(target.read_text())["approvals"] == 2
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not target.exists()
    waybar_state.request_refresh()  # no loop running: harmless


async def test_loop_disabled_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_WAYBAR_STATE", "0")
    target = tmp_path / "w.json"
    await asyncio.wait_for(waybar_state.run_loop(interval=0.01, path=target), 1)
    assert not target.exists()


# -- schema v2 ------------------------------------------------------------------


def test_v2_keeps_every_v1_key_and_adds_the_new_sections():
    payload = waybar_state.build_state(SNAP, now=1.0)
    assert V1_KEYS <= set(payload) and payload["v"] == waybar_state.SCHEMA_VERSION == 2
    assert {"class", "agents", "attention", "system", "today", "lhassa"} <= set(payload)
    json.dumps(payload)


def test_a_bare_snapshot_is_a_valid_calm_v2_file():
    payload = waybar_state.build_state({}, now=1.0)
    assert payload["class"] == "ok" and payload["attention"] == {"count": 0, "items": []}


def test_the_verdict_escalates_by_what_is_wrong():
    def cls(snap=None, items=(), temp="ok"):
        extra = {"attention": {"count": len(items), "items": list(items)},
                 "system": {"temp_level": temp}}
        return waybar_state.build_state({"budget": 100, **(snap or {})}, now=1.0, extra=extra)["class"]

    assert cls() == "ok"
    assert cls(items=[{"kind": "ci"}]) == "warn"
    assert cls(items=[{"kind": "approval"}]) == "warn"
    assert cls(items=[{"kind": "review"}]) == "alert"
    assert cls(items=[{"kind": "service"}]) == "alert"
    assert cls(temp="warn") == "warn" and cls(temp="alert") == "alert"
    assert cls({"pct_used": 0.9}) == "warn" and cls({"pct_used": 1.0}) == "alert"
    assert cls({"health_alert": ["disk"], "health_failing": ["disk"]}) == "alert"


def test_attention_lists_each_item_with_its_origin_in_priority_order():
    snap = {"health_failing": ["net"], "health_alert": []}
    att = waybar_state.build_attention(
        snap,
        [{"id": "ap_1", "label": "git_push"}],
        {"pending": [{"kind": "ledger", "label": "orca", "source": "ledger"}],
         "critical": [{"kind": "review", "label": "alpha", "n": 1, "source": "lhassa review"}],
         "pushes": [{"kind": "push", "label": "beta", "n": 2, "source": "pre-push review"}],
         "ci": [{"kind": "ci", "label": "alpha", "source": "GitHub Actions"}],
         "services": [{"kind": "service", "label": "lhassa-monitor", "source": "systemd"}]},
    )
    assert [i["kind"] for i in att["items"]] == ["approval", "ledger", "review", "push", "ci", "service", "health"]
    assert att["items"][0] == {"kind": "approval", "id": "ap_1", "label": "git_push", "source": "approvals"}
    assert att["count"] == 7


def test_the_temperature_class_latches_with_hysteresis():
    seq = [70, 85, 84, 81, 79, 100, 97, 94, 90, 79]
    level, seen = "ok", []
    for t in seq:
        level = waybar_state.latch(level, t)
        seen.append(level)
    assert seen == ["ok", "warn", "warn", "warn", "ok", "alert", "alert", "warn", "warn", "ok"]
    assert waybar_state.latch("alert", None) == "ok"


def test_the_relevant_key_ignores_noise_but_not_substance():
    base = waybar_state.build_state(SNAP, now=1.0, extra={
        "system": {"power_mode": "saver", "temp_c": 61.2, "temp_level": "ok", "cpu_pct": 11,
                   "battery_pct": 80, "charging": False, "suggestion": None},
        "today": {"events": 100, "commits": 0, "pushes": 0, "api_usd": 1.001, "top_repo": "a"}})
    key = waybar_state.relevant_key(base)

    def with_(**over):
        p = json.loads(json.dumps(base))
        for path, value in over.items():
            section, _, field = path.partition("__")
            if field:
                p[section][field] = value
            else:
                p[section] = value
        return waybar_state.relevant_key(p)

    assert with_(written_at=999.0) == key and with_(beat_age=99) == key
    assert with_(system__temp_c=62.4) == key and with_(system__cpu_pct=14) == key
    assert with_(today__events=110) == key and with_(today__api_usd=1.004) == key
    assert with_(system__temp_c=70.0) != key
    assert with_(system__temp_level="warn") != key
    assert with_(system__power_mode="balanced") != key
    assert with_(attention={"count": 1, "items": [{"kind": "ci"}]}) != key
    assert with_(agents={"window_s": 300, "total": 1, "hosts": {"orca": 1}, "sessions": []}) != key


# -- the quiet bar: a rewrite only when it matters ---------------------------------


def make_publisher(clock, **kw):
    return waybar_state.Publisher(ci=waybar_collect.CiWatcher(), clock=lambda: clock[0], heartbeat=30.0, **kw)


@pytest.fixture
def gathered(monkeypatch):
    box = {"snap": dict(SNAP), "got": json.loads(json.dumps(NEUTRAL))}

    async def fake_gather(instance_id=None):
        return dict(box["snap"])

    monkeypatch.setattr(status, "gather", fake_gather)
    monkeypatch.setattr(waybar_state.Publisher, "_collect_sync", lambda self, snap, now: json.loads(json.dumps(box["got"])))
    return box


async def test_an_unchanged_payload_is_not_rewritten_until_the_heartbeat(tmp_path, gathered):
    target, clock = tmp_path / "w.json", [1000.0]
    pub = make_publisher(clock)
    gathered["got"]["cpu"] = 11
    assert await pub.refresh(target) == target
    first = target.stat().st_mtime_ns
    for step in (5, 10, 20):  # inside the heartbeat, noise only
        clock[0] = 1000.0 + step
        gathered["snap"]["beat_age"] = step
        gathered["got"]["cpu"] = 11 + step % 3
        assert await pub.refresh(target) == target
    assert target.stat().st_mtime_ns == first
    clock[0] = 1031.0  # heartbeat due: same content, fresh stamp
    await pub.refresh(target)
    assert json.loads(target.read_text())["written_at"] == 1031.0


async def test_a_relevant_change_is_written_at_once(tmp_path, gathered):
    target, clock = tmp_path / "w.json", [1000.0]
    pub = make_publisher(clock)
    await pub.refresh(target)
    clock[0] = 1002.0
    gathered["got"]["services"] = [{"kind": "service", "label": "lhassa-monitor", "source": "systemd"}]
    await pub.refresh(target)
    data = json.loads(target.read_text())
    assert data["written_at"] == 1002.0 and data["class"] == "alert"
    assert any(i["kind"] == "service" for i in data["attention"]["items"])


async def test_a_burst_of_events_does_not_rewrite_the_file(tmp_path, gathered, monkeypatch):
    target, clock = tmp_path / "w.json", [1000.0]
    pub = make_publisher(clock)
    writes = []
    real = waybar_state.write_state
    monkeypatch.setattr(waybar_state, "write_state", lambda payload, path=None: writes.append(1) or real(payload, path))
    await pub.refresh(target)
    for i in range(1, 20):
        clock[0] = 1000.0 + i * 0.5
        gathered["got"]["ledger"]["today"]["events"] = i  # events tick up inside one bucket
        gathered["got"]["ledger"]["today"]["api_usd"] = 0.0001 * i
        await pub.refresh(target)
    assert len(writes) == 1


async def test_a_deleted_file_is_recreated_even_without_a_change(tmp_path, gathered):
    target, clock = tmp_path / "w.json", [1000.0]
    pub = make_publisher(clock)
    await pub.refresh(target)
    target.unlink()
    clock[0] = 1001.0
    assert await pub.refresh(target) == target and target.exists()


async def test_the_hot_package_does_not_flap_the_icon(tmp_path, gathered):
    target, clock = tmp_path / "w.json", [1000.0]
    pub = make_publisher(clock)
    classes = []
    for i, temp in enumerate([80, 86, 83, 86, 82, 79, 86]):
        clock[0] = 1000.0 + i
        gathered["got"]["temp"] = temp
        await pub.refresh(target)
        classes.append(json.loads(target.read_text())["system"]["temp_level"])
    assert classes == ["ok", "warn", "warn", "warn", "warn", "ok", "warn"]


async def test_the_written_file_is_private_and_complete(tmp_path, gathered):
    target = tmp_path / "run" / "w.json"
    await make_publisher([1.0]).refresh(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    data = json.loads(target.read_text())
    assert V1_KEYS <= set(data) and data["agents"]["total"] == 0


async def test_privacy_end_to_end_only_numbers_and_names(tmp_path, monkeypatch):
    """Through the real collectors: a personal cwd and a prompt never reach the file."""
    from tests.test_views_waybar_collect import make_ledger, repo

    ok = repo(tmp_path, "alpha")
    secret = tmp_path / "lhassa" / "personal" / "diary"
    secret.mkdir(parents=True)
    db = make_ledger(tmp_path / "ledger.db", [
        {"ts": time.time() - 5, "session": "s1", "detail": {"cwd": ok, "prompt": "TOP SECRET PLAN"}},
        {"ts": time.time() - 5, "session": "s2", "detail": {"cwd": str(secret)}},
        {"ts": time.time() - 5, "session": "s3", "host": "orca", "kind": "note",
         "detail": {"type": "window_focus", "title": "bank statement"}},
    ])
    monkeypatch.setenv("AGENT_LEDGER_DB", str(db))
    monkeypatch.setenv("LHASSA_REVIEW_DIR", str(tmp_path / "review"))
    monkeypatch.setenv("LHASSA_WAYBAR_CI_AUTO", "0")
    monkeypatch.setattr(waybar_state.Publisher, "_collect_sync", REAL_COLLECT)
    monkeypatch.setattr(waybar_collect, "failed_services", lambda run=None: [])
    monkeypatch.setattr(waybar_collect, "power_mode", lambda conf="": "saver")
    monkeypatch.setattr(waybar_collect, "package_temp", lambda root="": 55.0)
    monkeypatch.setattr(waybar_collect, "battery", lambda root="": (90, False))

    async def fake_gather(instance_id=None):
        return dict(SNAP)

    monkeypatch.setattr(status, "gather", fake_gather)
    target = tmp_path / "w.json"
    await make_publisher([time.time()]).refresh(target)
    text = target.read_text()
    for forbidden in ("TOP SECRET", "diary", "personal", "bank statement", str(tmp_path), "/home"):
        assert forbidden not in text
    data = json.loads(text)
    assert data["agents"]["total"] == 2 and data["agents"]["hosts"] == {"claude-code": 2}
    assert sorted(s["repo"] for s in data["agents"]["sessions"]) == ["", "alpha"]
    assert data["system"]["power_mode"] == "saver" and data["system"]["temp_c"] == 55.0
