# SPDX-License-Identifier: AGPL-3.0-or-later
"""The standalone Waybar widget script (scripts/waybar/lhassa-waybar)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "waybar" / "lhassa-waybar"
NOW = 10_000.0


def _load():
    loader = SourceFileLoader("lhassa_waybar_script", str(SCRIPT))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


wb = _load()


def state(**over):
    base = {
        "v": 1, "written_at": NOW - 5, "cost_usd": 0.42, "budget": 1000, "pct_used": 0.1,
        "soft_pct": 0.8, "approvals": 0, "tasks": 0, "health_failing": [],
        "dashboard_url": "http://127.0.0.1:7766",
    }
    base.update(over)
    return base


def render(tmp_path, payload=None, raw=None, env=None):
    path = tmp_path / "waybar.json"
    if raw is not None:
        path.write_text(raw)
    elif payload is not None:
        path.write_text(json.dumps(payload))
    return wb.render(str(path), NOW, {"LHASSA_WAYBAR_LANG": "en", **(env or {})})


def test_ok(tmp_path):
    out = render(tmp_path, state())
    assert out["class"] == "ok"
    assert "$0.42" in out["text"] and "·" not in out["text"]
    assert "Spent today: $0.42" in out["tooltip"]
    assert "Budget used: 10%" in out["tooltip"]
    assert "All calm" in out["tooltip"]
    assert set(out) == {"text", "tooltip", "class"}


def test_pending_approvals_warn(tmp_path):
    out = render(tmp_path, state(approvals=3, tasks=2))
    assert out["class"] == "warn"
    assert out["text"].endswith("$0.42 · 3")
    assert "Approvals waiting: 3" in out["tooltip"]
    assert "Tasks queued: 2" in out["tooltip"]
    assert "All calm" not in out["tooltip"]


def test_high_budget_warn_and_exhausted_alert(tmp_path):
    assert render(tmp_path, state(pct_used=0.85))["class"] == "warn"
    assert render(tmp_path, state(pct_used=1.0))["class"] == "alert"


def test_unlimited_budget_never_warns(tmp_path):
    out = render(tmp_path, state(budget=0, pct_used=0.0))
    assert out["class"] == "ok"
    assert "Budget used" not in out["tooltip"]


def test_a_health_check_at_alert_level_beats_warn(tmp_path):
    out = render(tmp_path, state(approvals=1, health_failing=["disk", "net"], health_alert=["disk"]))
    assert out["class"] == "alert"
    assert "Health: disk, net" in out["tooltip"]


def test_a_health_warning_alone_is_a_warn_not_an_alert(tmp_path):
    """A failing check below alert level (e.g. a system unit that is not Lhassa's)."""
    out = render(tmp_path, state(health_failing=["failed_units"], health_alert=[]))
    assert out["class"] == "warn"
    assert "Health: failed_units" in out["tooltip"]


def test_an_older_state_file_without_health_alert_degrades_to_warn(tmp_path):
    payload = state(health_failing=["disk"])
    payload.pop("health_alert", None)
    assert render(tmp_path, payload)["class"] == "warn"


def test_old_file_is_stale(tmp_path):
    out = render(tmp_path, state(written_at=NOW - 3600))
    assert out["class"] == "stale"
    assert "no brain" in out["text"]
    assert "1h00m" in out["tooltip"]


def test_stale_threshold_is_configurable(tmp_path):
    payload = state(written_at=NOW - 100)
    assert render(tmp_path, payload)["class"] == "stale"
    assert render(tmp_path, payload, env={"LHASSA_WAYBAR_STALE_S": "300"})["class"] == "ok"


def test_future_timestamp_is_not_stale(tmp_path):
    assert render(tmp_path, state(written_at=NOW + 50))["class"] == "ok"


def test_missing_file_is_idle(tmp_path):
    out = render(tmp_path)
    assert out["class"] == "idle" and "no brain" in out["text"]


@pytest.mark.parametrize("raw", ["", "{not json", "[]", "null", '{"cost_usd": 1}', '{"written_at": "x"}'])
def test_corrupt_file_is_idle_not_a_crash(tmp_path, raw):
    out = render(tmp_path, raw=raw)
    assert out["class"] == "idle"
    assert "unreadable" in out["tooltip"]


def test_garbage_field_types_degrade(tmp_path):
    out = render(tmp_path, state(cost_usd="lots", approvals="x", health_failing="oops", pct_used=None))
    assert out["class"] == "ok" and "$0.00" in out["text"]


def test_spanish(tmp_path):
    out = render(tmp_path, env={"LHASSA_WAYBAR_LANG": "es"})
    assert "sin cerebro" in out["text"]
    assert "Aprobaciones" in render(tmp_path, state(approvals=1), env={"LHASSA_WAYBAR_LANG": "es"})["tooltip"]


def test_markup_is_escaped(tmp_path):
    out = render(tmp_path, state(health_failing=["<b>&x"]))
    assert "<b>" not in out["tooltip"] and "&lt;b&gt;&amp;x" in out["tooltip"]


def test_state_path_rules(tmp_path):
    assert wb.state_path({"LHASSA_WAYBAR_STATE_FILE": "/a/b"}) == "/a/b"
    assert wb.state_path({"XDG_RUNTIME_DIR": str(tmp_path)}) == str(tmp_path / "lhassa" / "waybar.json")
    assert wb.state_path({}).endswith("waybar.json")


def test_writer_and_script_agree_on_the_path(tmp_path):
    from lhassa.views import waybar_state

    for env in ({}, {"XDG_RUNTIME_DIR": str(tmp_path)}, {"XDG_RUNTIME_DIR": "/nope/missing"},
                {"LHASSA_WAYBAR_STATE_FILE": "/x/y"}):
        assert str(waybar_state.state_path(env)) == wb.state_path(env)


def test_dashboard_url_only_local(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"dashboard_url": "http://127.0.0.1:9000"}))
    assert wb.dashboard_url(str(path)) == "http://127.0.0.1:9000"
    path.write_text(json.dumps({"dashboard_url": "https://evil.example/x"}))
    assert wb.dashboard_url(str(path)) == wb.DEFAULT_URL
    path.write_text("junk")
    assert wb.dashboard_url(str(path)) == wb.DEFAULT_URL
    assert wb.dashboard_url(str(tmp_path / "nope")) == wb.DEFAULT_URL


def test_end_to_end_subprocess_is_fast_and_valid(tmp_path):
    path = tmp_path / "w.json"
    path.write_text(json.dumps(state(written_at=time.time(), approvals=1)))
    env = {"PATH": "/usr/bin:/bin", "LHASSA_WAYBAR_STATE_FILE": str(path), "LHASSA_WAYBAR_LANG": "en"}
    t0 = time.perf_counter()
    done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
    elapsed = time.perf_counter() - t0
    assert done.returncode == 0
    assert json.loads(done.stdout)["class"] == "warn"
    assert elapsed < 1.0  # generous CI bound; typically ~30 ms


def test_subprocess_without_state_dir_still_prints_json(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "LHASSA_WAYBAR_STATE_FILE": str(tmp_path / "none" / "w.json")}
    done = subprocess.run([sys.executable, str(SCRIPT), "status"], env=env, capture_output=True, text=True, timeout=10)
    assert done.returncode == 0 and json.loads(done.stdout)["class"] == "idle"


def test_unknown_command_exits_2():
    done = subprocess.run([sys.executable, str(SCRIPT), "wipe"], capture_output=True, text=True, timeout=10)
    assert done.returncode == 2


def test_script_is_stdlib_only_and_executable():
    text = SCRIPT.read_text()
    assert "import lhassa" not in text and "from lhassa" not in text
    assert SCRIPT.stat().st_mode & 0o111
    assert chr(0x2014) not in text
