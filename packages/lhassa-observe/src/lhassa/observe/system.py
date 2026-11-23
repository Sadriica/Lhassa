# SPDX-License-Identifier: AGPL-3.0-or-later
"""System observer: resource samples from ``/proc`` and ``/sys`` (stdlib only).

One ``resource_sample`` event every ``interval`` seconds with aggregated numbers
only: CPU busy percent (delta between two samples), memory and swap use, load
average, temperatures (thermal zones and hwmon, reduced to a maximum plus a
small per-sensor map) and battery / AC state. Parsers are pure functions over
text; the collector reads files under an injectable ``proc_root`` / ``sys_root``,
so tests use synthetic trees and never the real machine.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from lhassa.observe.writer import ObserveWriter

MAX_SENSORS = 12


def parse_proc_stat(text: str) -> Optional[tuple[int, int]]:
    """``(busy, total)`` jiffies from the aggregate ``cpu`` line, or ``None``."""
    for line in text.splitlines():
        if line.startswith("cpu ") or line.startswith("cpu\t"):
            try:
                nums = [int(x) for x in line.split()[1:9]]
            except ValueError:
                return None
            if len(nums) < 4:
                return None
            total = sum(nums)
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            return total - idle, total
    return None


def parse_meminfo(text: str) -> dict[str, int]:
    """``{"MemTotal": kB, ...}`` for every ``Key: value kB`` line."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            try:
                out[key.strip()] = int(parts[0])
            except ValueError:
                continue
    return out


def parse_loadavg(text: str) -> Optional[tuple[float, float, float]]:
    parts = text.split()
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (IndexError, ValueError):
        return None


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def _read_int(path: Path) -> Optional[int]:
    text = _read(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def read_temps(sys_root: Path) -> dict[str, float]:
    """``{sensor: degrees C}`` from thermal zones and hwmon (max per sensor name)."""
    temps: dict[str, float] = {}

    def put(name: str, milli: Optional[int]) -> None:
        if milli is None or not -50_000 < milli < 200_000:
            return
        c = round(milli / 1000.0, 1)
        if name not in temps or c > temps[name]:
            temps[name] = c

    try:
        for zone in sorted((sys_root / "class" / "thermal").glob("thermal_zone*")):
            put(f"zone:{_read(zone / 'type') or zone.name}", _read_int(zone / "temp"))
        for hw in sorted((sys_root / "class" / "hwmon").glob("hwmon*")):
            name = _read(hw / "name") or hw.name
            for f in sorted(hw.glob("temp*_input")):
                put(f"hwmon:{name}", _read_int(f))
    except OSError:
        pass
    if len(temps) > MAX_SENSORS:
        keep = sorted(temps.items(), key=lambda kv: -kv[1])[:MAX_SENSORS]
        temps = dict(keep)
    return temps


def read_power(sys_root: Path) -> dict[str, object]:
    """Battery percent / status / watts and AC state from ``power_supply``."""
    out: dict[str, object] = {}
    try:
        supplies = sorted((sys_root / "class" / "power_supply").iterdir())
    except OSError:
        return out
    for sup in supplies:
        kind = _read(sup / "type")
        if kind == "Battery" and "bat_pct" not in out:
            cap = _read_int(sup / "capacity")
            if cap is not None:
                out["bat_pct"] = cap
            status = _read(sup / "status")
            if status:
                out["bat_status"] = status
            power = _read_int(sup / "power_now")
            if power is not None and power >= 0:
                out["power_w"] = round(power / 1_000_000.0, 2)
        elif kind == "Mains" and "ac" not in out:
            online = _read_int(sup / "online")
            if online is not None:
                out["ac"] = bool(online)
    return out


class SystemSampler:
    """Takes samples; keeps the previous CPU counters for the delta."""

    def __init__(self, proc_root: str | Path = "/proc", sys_root: str | Path = "/sys") -> None:
        self.proc = Path(proc_root)
        self.sys = Path(sys_root)
        self._prev: Optional[tuple[int, int]] = None

    def available(self) -> bool:
        return (self.proc / "stat").exists()

    def sample(self) -> dict[str, object]:
        out: dict[str, object] = {}
        stat = parse_proc_stat(_read(self.proc / "stat") or "")
        if stat is not None:
            if self._prev is not None and stat[1] > self._prev[1]:
                out["cpu_pct"] = round(100.0 * (stat[0] - self._prev[0]) / (stat[1] - self._prev[1]), 1)
            self._prev = stat
        mem = parse_meminfo(_read(self.proc / "meminfo") or "")
        total = mem.get("MemTotal")
        avail = mem.get("MemAvailable")
        if total and avail is not None:
            out["mem_used_pct"] = round(100.0 * (total - avail) / total, 1)
            out["mem_avail_mb"] = avail // 1024
        swap_total = mem.get("SwapTotal")
        if swap_total is not None:
            free = mem.get("SwapFree", swap_total)
            out["swap_used_mb"] = (swap_total - free) // 1024
            out["swap_pct"] = round(100.0 * (swap_total - free) / swap_total, 1) if swap_total else 0.0
        load = parse_loadavg(_read(self.proc / "loadavg") or "")
        if load is not None:
            out["load1"], out["load5"], out["load15"] = load
        temps = read_temps(self.sys)
        if temps:
            out["temp_max_c"] = max(temps.values())
            out["temps"] = temps
        out.update(read_power(self.sys))
        return out


class SystemObserver:
    name = "system"

    def __init__(
        self,
        writer: ObserveWriter,
        *,
        interval: float = 30.0,
        proc_root: str | Path = "/proc",
        sys_root: str | Path = "/sys",
        on_state: Callable[[str, str], None] = lambda state, note: None,
    ) -> None:
        self.writer = writer
        self.interval = interval
        self.sampler = SystemSampler(proc_root, sys_root)
        self._on_state = on_state

    def run(self, stop: threading.Event) -> None:
        if not self.sampler.available():
            self._on_state("inactive", "no /proc/stat on this host")
            return
        self._on_state("active", f"every {self.interval:g}s")
        while not stop.is_set():
            fields = self.sampler.sample()
            if fields:
                self.writer.emit("resource_sample", fields)
            stop.wait(self.interval)
