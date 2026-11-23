"""Writer (never-keep gate, daily files), configuration, coordinator, CLI and the architecture of the pack."""

import json
import os
import stat
import threading
import time

from typer.testing import CliRunner

from lhassa.observe.config import ObserveConfig, load_config, parse_config, with_overrides
from lhassa.observe.coordinator import Coordinator, read_status
from lhassa.observe.writer import ObserveWriter, gate, last_event_ts
from lhassa.record import read_log

DASH = chr(0x2014)


def test_gate_withholds_paths_and_text_and_drops_on_error():
    out = gate({"t": "x", "p": "/home/w/.ssh/id_rsa", "n": "cat ~/.ssh/id", "ok": "fine"}, paths=("p",))
    assert out["p"] == out["n"] == "<withheld>" and out["ok"] == "fine" and out["withheld"]

    class Bad(str):
        def __bool__(self):
            raise RuntimeError("boom")

    assert gate({"t": "x", "v": Bad("a")}) is None


def test_writer_layout_privacy_and_daily_roll(tmp_path):
    clock = [time.mktime((2026, 11, 22, 23, 59, 0, 0, 0, -1))]
    w = ObserveWriter("system", tmp_path, "inst_x", clock=lambda: clock[0])
    w.emit("resource_sample", {"cpu_pct": 1.0})
    clock[0] += 120
    w.emit("resource_sample", {"cpu_pct": 2.0})
    w.close()
    files = sorted((tmp_path / "inst_x" / "observe" / "system").glob("*.jsonl"))
    assert [f.name for f in files] == ["2026-11-22.jsonl", "2026-11-23.jsonl"]
    assert stat.S_IMODE(os.stat(files[0]).st_mode) == 0o600
    ev = read_log(files[1])[0][0]
    assert ev["source"] == "observe.system" and ev["t"] == "resource_sample" and ev["v"] == 1
    assert last_event_ts(tmp_path, "inst_x", "system") == ev["ts"]
    assert last_event_ts(tmp_path, "inst_x", "sway") is None


def test_writer_never_raises_on_an_unwritable_dir(tmp_path):
    (tmp_path / "f").write_text("x")
    w = ObserveWriter("system", tmp_path / "f" / "sub", "i")
    assert w.emit("resource_sample", {"a": 1}) is not None  # log disabled, event counted, no raise
    w.close()


def test_config_defaults_to_nothing(tmp_path):
    assert load_config(tmp_path / "missing.json") == ObserveConfig()
    (tmp_path / "bad.json").write_text("{nope")
    assert load_config(tmp_path / "bad.json").enabled == frozenset()
    assert load_config(tmp_path / "bad.json").problems
    assert parse_config([1, 2]).problems and parse_config({}).enabled == frozenset()


def test_config_parses_and_clamps():
    cfg = parse_config({
        "enabled": ["system", "files", "bogus"], "system": {"interval": 1},
        "sway": {"titles": True}, "shell": {"full_command": True},
        "files": {"folders": ["~/a", {"path": "/b", "recursive": True, "max_depth": 99}, {"x": 1}], "max_watches": 10**9},
    })
    assert cfg.enabled == {"system", "files"} and cfg.interval == 5.0
    assert cfg.sway_titles and cfg.shell_full_command
    assert [f.path for f in cfg.folders] == ["~/a", "/b"] and cfg.folders[1].max_depth == 8
    assert cfg.max_watches == 4096 and cfg.problems


def test_flags_enable_but_cannot_add_folders():
    cfg = with_overrides(ObserveConfig(), enable=frozenset({"files"}))
    assert cfg.enabled == {"files"} and cfg.folders == ()


def _coord(tmp_path, cfg, **kw):
    return Coordinator(cfg, records_dir=tmp_path / "rec", instance_id="inst", shell_dir=tmp_path / "rt",
                       proc_root=tmp_path / "np", sys_root=tmp_path / "ns", swaysock="", status_every=0.1, **kw)


def test_coordinator_with_nothing_enabled_runs_nothing(tmp_path):
    c = _coord(tmp_path, ObserveConfig())
    c.run(duration=0.1)
    assert c.writers == {} and not list((tmp_path / "rec").rglob("*.jsonl"))
    st = read_status(tmp_path / "rec", "inst")
    assert not st["running"] and all(r["state"] == "off" for r in st["observers"].values())


def test_coordinator_runs_enabled_and_reports_status(tmp_path):
    proc = tmp_path / "np"
    proc.mkdir()
    (proc / "stat").write_text("cpu  1 0 1 8 0 0 0 0\n")
    (proc / "meminfo").write_text("MemTotal: 100 kB\nMemAvailable: 50 kB\n")
    cfg = ObserveConfig(enabled=frozenset({"system", "sway", "files"}), interval=5.0)
    c = _coord(tmp_path, cfg)
    stop = threading.Event()
    t = threading.Thread(target=c.run, args=(stop,))
    t.start()
    time.sleep(1.0)
    live = read_status(tmp_path / "rec", "inst")
    stop.set()
    t.join(5)
    assert live["running"]
    assert live["observers"]["system"]["state"] == "active" and live["observers"]["system"]["events"] == 1
    assert live["observers"]["sway"]["state"] == "inactive"
    assert live["observers"]["files"]["state"] == "idle"
    assert live["observers"]["shell"]["state"] == "off"
    done = read_status(tmp_path / "rec", "inst")
    assert not done["running"] and done["observers"]["system"]["last_event_ts"]


def test_a_crashing_observer_is_contained(tmp_path):
    cfg = ObserveConfig(enabled=frozenset({"system"}))
    c = _coord(tmp_path, cfg)
    c.build()

    class Boom:
        def run(self, stop):
            raise RuntimeError("kaput")

    c._observers["system"] = Boom()
    stop = threading.Event()
    t = threading.Thread(target=c._supervise, args=("system", stop))
    t.start()
    time.sleep(0.2)
    assert c.state["system"]["state"] == "failed"
    stop.set()
    t.join(3)
    assert not t.is_alive()


def test_cli_nothing_enabled_and_status(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_RECORDS_DIR", str(tmp_path / "rec"))
    monkeypatch.setenv("LHASSA_OBSERVE_CONFIG", str(tmp_path / "none.json"))
    from lhassa.cli.app import app

    r = CliRunner().invoke(app, ["observe", "run"])
    assert r.exit_code == 0 and "Nothing is enabled" in r.output
    s = CliRunner().invoke(app, ["observe", "status", "--json"])
    assert s.exit_code == 0 and json.loads(s.output)["running"] is False


def test_cli_hook_and_example_config():
    from lhassa.cli.app import app

    for shell in ("bash", "zsh"):
        r = CliRunner().invoke(app, ["observe", "hook", shell])
        assert r.exit_code == 0 and "__lhassa_precmd" in r.output
    assert CliRunner().invoke(app, ["observe", "hook", "fish"]).exit_code == 2
    ex = CliRunner().invoke(app, ["observe", "example-config"])
    assert json.loads(ex.output)["enabled"]


def test_pack_sources_have_no_em_or_en_dash():
    from pathlib import Path
    import lhassa.observe

    for p in Path(lhassa.observe.__file__).parent.rglob("*"):
        if p.is_file() and p.suffix in (".py", ".bash", ".zsh"):
            text = p.read_text(encoding="utf-8")
            assert DASH not in text and chr(0x2013) not in text, p


def test_gate_cache_is_keyed_by_home_and_checked_fields_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "a"))
    assert gate({"t": "x", "v": "~/.ssh/id"})["v"] == "<withheld>"
    monkeypatch.setenv("HOME", str(tmp_path / "b"))
    assert gate({"t": "x", "v": str(tmp_path / "a" / ".ssh" / "id")})["v"] == str(tmp_path / "a" / ".ssh" / "id")
    assert gate({"t": "x", "v": "~/.ssh/id"}, checked=("v",))["v"] == "~/.ssh/id"
