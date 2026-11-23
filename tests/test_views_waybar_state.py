# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Waybar state writer: payload shape, location, atomic 0600 writes, the loop."""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest

from lhassa.views import status, waybar_state

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


def test_build_state_shape():
    payload = waybar_state.build_state(SNAP, now=1000.0, url="http://127.0.0.1:7766")
    assert payload == {
        "v": 1,
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
