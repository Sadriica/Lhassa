"""System observer: pure parsers and a collector over synthetic /proc and /sys trees."""

import threading

from lhassa.observe.system import (
    SystemObserver, SystemSampler, parse_loadavg, parse_meminfo, parse_proc_stat, read_power, read_temps,
)
from lhassa.observe.writer import ObserveWriter
from lhassa.record import read_log

STAT1 = "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4\n"
STAT2 = "cpu  200 0 200 1600 0 0 0 0 0 0\n"
MEMINFO = "MemTotal:       16000000 kB\nMemAvailable:    4000000 kB\nSwapTotal:       8000000 kB\nSwapFree:        6000000 kB\n"


def _tree(tmp_path, stat=STAT1):
    proc, sysd = tmp_path / "proc", tmp_path / "sys"
    proc.mkdir()
    (proc / "stat").write_text(stat)
    (proc / "meminfo").write_text(MEMINFO)
    (proc / "loadavg").write_text("0.52 0.40 0.30 1/500 1234\n")
    z = sysd / "class/thermal/thermal_zone0"
    z.mkdir(parents=True)
    (z / "type").write_text("x86_pkg_temp\n")
    (z / "temp").write_text("61000\n")
    hw = sysd / "class/hwmon/hwmon3"
    hw.mkdir(parents=True)
    (hw / "name").write_text("nvme\n")
    (hw / "temp1_input").write_text("41850\n")
    (hw / "temp2_input").write_text("45000\n")
    bat = sysd / "class/power_supply/BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "capacity").write_text("77\n")
    (bat / "status").write_text("Discharging\n")
    (bat / "power_now").write_text("8500000\n")
    ac = sysd / "class/power_supply/AC0"
    ac.mkdir(parents=True)
    (ac / "type").write_text("Mains\n")
    (ac / "online").write_text("0\n")
    return proc, sysd


def test_parse_proc_stat_and_garbage():
    assert parse_proc_stat(STAT1) == (200, 1000)
    assert parse_proc_stat("nothing here") is None
    assert parse_proc_stat("cpu  a b c d\n") is None


def test_parse_meminfo_and_loadavg():
    mem = parse_meminfo(MEMINFO)
    assert mem["MemTotal"] == 16000000 and mem["SwapFree"] == 6000000
    assert parse_loadavg("0.52 0.40 0.30 1/500 1234") == (0.52, 0.40, 0.30)
    assert parse_loadavg("") is None


def test_sampler_aggregates_numbers(tmp_path):
    proc, sysd = _tree(tmp_path)
    s = SystemSampler(proc, sysd)
    first = s.sample()
    assert "cpu_pct" not in first  # needs two samples
    assert first["mem_used_pct"] == 75.0 and first["mem_avail_mb"] == 3906
    assert first["swap_used_mb"] == 1953 and first["swap_pct"] == 25.0
    assert first["load1"] == 0.52
    assert first["temp_max_c"] == 61.0
    assert first["temps"] == {"zone:x86_pkg_temp": 61.0, "hwmon:nvme": 45.0}
    assert first["bat_pct"] == 77 and first["bat_status"] == "Discharging"
    assert first["power_w"] == 8.5 and first["ac"] is False
    (proc / "stat").write_text(STAT2)
    assert s.sample()["cpu_pct"] == 20.0  # +200 busy of +1000 total


def test_cpu_delta_is_busy_over_total(tmp_path):
    proc, sysd = _tree(tmp_path)
    s = SystemSampler(proc, sysd)
    s.sample()
    (proc / "stat").write_text("cpu  200 0 100 900 0 0 0 0 0 0\n")  # +100 busy of +200 total
    assert s.sample()["cpu_pct"] == 50.0


def test_missing_sources_degrade_to_absent_fields(tmp_path):
    s = SystemSampler(tmp_path / "no-proc", tmp_path / "no-sys")
    assert not s.available()
    assert s.sample() == {}
    assert read_temps(tmp_path / "nope") == {} and read_power(tmp_path / "nope") == {}


def test_insane_temperature_is_ignored(tmp_path):
    _, sysd = _tree(tmp_path)
    (sysd / "class/thermal/thermal_zone0/temp").write_text("-274000\n")
    assert "zone:x86_pkg_temp" not in read_temps(sysd)


def test_observer_writes_resource_samples_and_stops(tmp_path):
    proc, sysd = _tree(tmp_path)
    w = ObserveWriter("system", tmp_path / "rec", "inst")
    states = []
    obs = SystemObserver(w, interval=0.01, proc_root=proc, sys_root=sysd, on_state=lambda *a: states.append(a))
    stop = threading.Event()
    t = threading.Thread(target=obs.run, args=(stop,))
    t.start()
    for _ in range(200):
        if w.events >= 2:
            break
        stop.wait(0.01)
    stop.set()
    t.join(2)
    assert not t.is_alive()
    w.close()
    events, torn = read_log(w._log.path if w._log else next((tmp_path / "rec").rglob("*.jsonl")))
    assert w.events >= 2 and not torn
    assert events[0]["t"] == "resource_sample" and events[0]["source"] == "observe.system"
    assert states[0][0] == "active"


def test_observer_without_proc_is_inactive_not_an_error(tmp_path):
    w = ObserveWriter("system", tmp_path / "rec", "inst")
    states = []
    SystemObserver(w, proc_root=tmp_path / "x", sys_root=tmp_path / "y", on_state=lambda *a: states.append(a)).run(threading.Event())
    assert states[0][0] == "inactive" and w.events == 0
