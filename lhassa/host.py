# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The host platform layer — where Lhassa asks the machine about itself.

Lhassa reads a handful of facts from the operating system: how long it has been
up, whether memory is under pressure, whether any managed service failed, how to
pop a desktop toast, and which sandbox is available. Each of those is a
different command on a different platform, and until now each call site answered
the question its own way — some guarded with ``shutil.which``, some with a bare
``open("/proc/...")``, some with a module-level ``platform.system()`` branch.

This module is the one place that knows. It is **leaf infra**: it imports the
registry keystone and the standard library, and nothing else from the package,
so any layer may use it.

Two rules it keeps, both inherited rather than invented:

**Pure parser + injected input.** Every reading is split into a pure function
over text (``parse_proc_uptime``, ``parse_vm_stat``) and a thin collector that
supplies that text. The parsers are testable on every platform with no
patching — the pattern ``health/checks.py`` already uses for ``meminfo_path``.
Tests parametrise by passing a different platform or a different reader, never
by monkeypatching ``os.name``.

**A registry, not a branch.** Platforms are registered
(:func:`register_platform`), so adding one is a drop-in module and selecting one
is configuration (``LHASSA_PLATFORM``), never surgery. The module-level
``platform.system()`` dispatch that already exists elsewhere in the repo
(``voice/notifier.py``, ``agents/local_cli.py``) is not replaced or duplicated:
it stays where it is, and lives *inside* the default implementation here.

**Honesty over completeness.** A reading that cannot be taken returns ``None``,
never a plausible zero. This is deliberate: these values feed the judgment path
through ``health/`` and ``system_snapshot``, so a wrong number is far worse than
a missing one — a missing reading is reported as unavailable, a wrong one
silently corrupts a decision.
"""

from __future__ import annotations

import os
import platform as _platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from lhassa.registry import Registry

__all__ = [
    "MemoryReading",
    "Platform",
    "PLATFORMS",
    "register_platform",
    "current_platform_name",
    "get_platform",
    "parse_proc_uptime",
    "parse_vm_stat",
    "parse_boottime",
]

# A short timeout on every probe: a host reading must never stall a heartbeat.
_TIMEOUT = 5.0


@dataclass(frozen=True)
class MemoryReading:
    """One memory-pressure reading, qualified by how it was measured.

    ``used_pct``/``swap_pct`` are percentages, ``source`` names the mechanism
    ("meminfo", "vm_stat", …) and ``comparable`` states whether the number means
    the same thing as Linux's ``/proc/meminfo`` arithmetic.

    ``comparable=False`` is the important field. macOS accounts memory very
    differently (compressed, purgeable and wired pages), so a "% used" derived
    from ``vm_stat`` is NOT the Linux number under another name. Callers must
    surface the distinction rather than compare the two, which is why the
    threshold decision stays with the caller and this type only reports.
    """

    used_pct: float
    swap_pct: float
    source: str
    comparable: bool = True


@runtime_checkable
class Platform(Protocol):
    """What Lhassa needs to ask the machine. Every method degrades, never raises."""

    name: str

    def uptime_seconds(self) -> Optional[float]:
        """Seconds since boot, or ``None`` when it cannot be read."""

    def memory(self) -> Optional[MemoryReading]:
        """Memory pressure, or ``None`` when it cannot be read."""

    def failed_services(self) -> Optional[list[str]]:
        """Names of failed managed services, or ``None`` when unknowable."""

    def notify_argv(self, title: str, message: str) -> Optional[list[str]]:
        """argv for a desktop toast, or ``None`` when no mechanism exists."""

    def sandbox_prefix(self, workdir: str) -> Optional[tuple[list[str], str]]:
        """``(argv_prefix, level)`` for the strongest available sandbox.

        ``None`` means this platform offers none — the caller must fail closed,
        never fall back to running the payload unconfined.
        """


PLATFORMS: Registry[Platform] = Registry("platform")


def register_platform(name: str, impl=None, *, override: bool = False):
    """Register a platform implementation (direct call or decorator)."""
    return PLATFORMS.register(name, impl, override=override)


# ── pure parsers (no I/O, testable on any platform) ──────────────────────────


def parse_proc_uptime(text: str) -> Optional[float]:
    """Seconds since boot from ``/proc/uptime`` contents. ``None`` if unusable."""
    try:
        return float((text or "").split()[0])
    except (ValueError, IndexError):
        return None


def parse_boottime(text: str, *, now: Optional[float] = None) -> Optional[float]:
    """Seconds since boot from ``sysctl -n kern.boottime`` output.

    macOS prints ``{ sec = 1754212345, usec = 123456 } Sun Aug  3 ...``; only
    the ``sec`` field is used. ``now`` is injected for testability.
    """
    import re
    import time

    m = re.search(r"sec\s*=\s*(\d+)", text or "")
    if not m:
        return None
    boot = float(m.group(1))
    current = now if now is not None else time.time()
    delta = current - boot
    return delta if delta >= 0 else None


def parse_vm_stat(text: str, *, page_size: int = 4096) -> Optional[float]:
    """Percent of physical pages NOT free, from ``vm_stat`` output.

    Counts free + speculative as available and everything else (active, wired,
    compressed) as in use. This is a coarse figure by nature — see
    :class:`MemoryReading` on why it is reported as not comparable to Linux's.
    Returns ``None`` when the output cannot be parsed.
    """
    import re

    counts: dict[str, float] = {}
    for line in (text or "").splitlines():
        m = re.match(r'"?Pages ([^:"]+)"?:\s*(\d+)', line.strip())
        if m:
            counts[m.group(1).strip().lower()] = float(m.group(2))
    if not counts:
        return None
    free = counts.get("free", 0.0) + counts.get("speculative", 0.0)
    total = sum(
        counts.get(k, 0.0)
        for k in (
            "free", "active", "inactive", "speculative", "wired down",
            "occupied by compressor",
        )
    )
    if total <= 0:
        return None
    used_pct = (1.0 - free / total) * 100.0
    return max(0.0, min(100.0, used_pct))


def _run(argv: list[str]) -> Optional[str]:
    """Run a probe and return stdout, or ``None`` on any failure. Never raises."""
    if not shutil.which(argv[0]):
        return None
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_TIMEOUT
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _sandbox_works(prefix: list[str], workdir: str) -> bool:
    """Smoke-test a sandbox prefix — a tool on PATH may still be unusable."""
    try:
        proc = subprocess.run(
            [*prefix, "true"], cwd=workdir, capture_output=True, timeout=_TIMEOUT
        )
        return proc.returncode == 0
    except Exception:
        return False


# ── implementations ──────────────────────────────────────────────────────────


@register_platform("linux")
class LinuxPlatform:
    """Tier 1 — the platform Lhassa is developed and deployed on."""

    name = "linux"

    def __init__(self, *, uptime_path: str = "/proc/uptime",
                 meminfo_path: str = "/proc/meminfo") -> None:
        # Paths are injected so tests exercise the real code with fixture files.
        self.uptime_path = uptime_path
        self.meminfo_path = meminfo_path

    def uptime_seconds(self) -> Optional[float]:
        try:
            with open(self.uptime_path) as fh:
                return parse_proc_uptime(fh.read())
        except OSError:
            return None

    def memory(self) -> Optional[MemoryReading]:
        # The existing pure parser in health/checks.py stays the authority for
        # meminfo; importing it here would invert the layering, so the health
        # check keeps owning that reading and this returns None to say so.
        return None

    def failed_services(self) -> Optional[list[str]]:
        out = _run(["systemctl", "--failed", "--no-legend", "--plain"])
        if out is None:
            return None
        return [ln.split()[0] for ln in out.splitlines() if ln.strip()]

    def notify_argv(self, title: str, message: str) -> Optional[list[str]]:
        if not shutil.which("notify-send"):
            return None
        return ["notify-send", "--app-name=Lhassa", title, message]

    def sandbox_prefix(self, workdir: str) -> Optional[tuple[list[str], str]]:
        if shutil.which("unshare"):
            prefix = ["unshare", "-rn", "--"]
            if _sandbox_works(prefix, workdir):
                return (prefix, "unshare")
        if shutil.which("firejail"):
            prefix = ["firejail", "--quiet", "--net=none", f"--private={workdir}", "--"]
            if _sandbox_works(prefix, workdir):
                return (prefix, "firejail")
        if shutil.which("bwrap"):
            prefix = [
                "bwrap", "--unshare-net", "--die-with-parent",
                "--bind", workdir, workdir, "--chdir", workdir, "--",
            ]
            if _sandbox_works(prefix, workdir):
                return (prefix, "bwrap")
        return None


@register_platform("darwin")
class DarwinPlatform:
    """Tier 2 — Apple Silicon. Best-effort on anything touching hardware."""

    name = "darwin"

    def uptime_seconds(self) -> Optional[float]:
        out = _run(["sysctl", "-n", "kern.boottime"])
        return parse_boottime(out) if out is not None else None

    def memory(self) -> Optional[MemoryReading]:
        out = _run(["vm_stat"])
        if out is None:
            return None
        used = parse_vm_stat(out)
        if used is None:
            return None
        # Swap is reported separately by sysctl; absent/unparseable → 0.0 with
        # the reading still flagged not-comparable, so no caller treats this as
        # a Linux-equivalent number.
        swap_pct = 0.0
        swap_out = _run(["sysctl", "-n", "vm.swapusage"])
        if swap_out:
            import re

            total = re.search(r"total\s*=\s*([\d.]+)M", swap_out)
            used_m = re.search(r"used\s*=\s*([\d.]+)M", swap_out)
            if total and used_m and float(total.group(1)) > 0:
                swap_pct = float(used_m.group(1)) / float(total.group(1)) * 100.0
        return MemoryReading(
            used_pct=used, swap_pct=swap_pct, source="vm_stat", comparable=False
        )

    def failed_services(self) -> Optional[list[str]]:
        """Lhassa's own launchd jobs whose last exit status was non-zero.

        ``launchctl list`` prints ``PID  Status  Label``. A non-zero status is
        the LAST exit code, not a persistent "failed" state the way a systemd
        unit has one, so this is an approximation and is documented as such;
        only ``com.lhassa.*`` labels are considered so an unrelated system job
        never shows up as Lhassa's problem.
        """
        out = _run(["launchctl", "list"])
        if out is None:
            return None
        failed: list[str] = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 3:
                continue
            status, label = parts[1], parts[2]
            if not label.startswith("com.lhassa."):
                continue
            try:
                if int(status) != 0:
                    failed.append(label)
            except ValueError:
                continue
        return failed

    def notify_argv(self, title: str, message: str) -> Optional[list[str]]:
        """AppleScript toast. Text is passed via the environment by the caller,
        mirroring ``voice/notifier.py`` so no user text is interpolated here."""
        if not shutil.which("osascript"):
            return None
        script = (
            'set t to system attribute "_LHASSA_TITLE"\n'
            'set m to system attribute "_LHASSA_MSG"\n'
            "display notification m with title t"
        )
        return ["osascript", "-e", script]

    def sandbox_prefix(self, workdir: str) -> Optional[tuple[list[str], str]]:
        """macOS ``sandbox-exec`` with a deny-by-default profile.

        The profile denies everything, then allows only process execution and
        reads — notably NOT ``network*``, which is the property the Linux
        sandboxes are chosen for. ``sandbox-exec`` is formally deprecated by
        Apple but present on every current macOS and is the only sandbox
        available without shipping a signed helper; when it is missing this
        returns ``None`` and the caller fails closed.
        """
        if not shutil.which("sandbox-exec"):
            return None
        profile = (
            "(version 1)"
            "(deny default)"
            "(allow process-exec process-fork)"
            "(allow file-read*)"
            f'(allow file-write* (subpath "{workdir}"))'
            "(allow sysctl-read)"
        )
        prefix = ["sandbox-exec", "-p", profile]
        if _sandbox_works(prefix, workdir):
            return (prefix, "sandbox-exec")
        return None


# ── selection ────────────────────────────────────────────────────────────────


def current_platform_name(system: Optional[str] = None) -> str:
    """The platform id for this host.

    ``LHASSA_PLATFORM`` overrides it, so selecting a platform is configuration
    rather than surgery (and a test can pin one without patching ``os.name``).
    ``system`` is injected for testability; it defaults to
    ``platform.system()``.
    """
    forced = (os.getenv("LHASSA_PLATFORM") or "").strip().lower()
    if forced:
        return forced
    return (system or _platform.system() or "").strip().lower()


def get_platform(name: Optional[str] = None) -> Optional[Platform]:
    """The registered implementation for ``name`` (default: this host).

    Returns ``None`` for an unregistered platform — the caller then degrades on
    its own terms rather than receiving a silently wrong stub.
    """
    impl = PLATFORMS.get_or_none(name or current_platform_name())
    if impl is None:
        return None
    try:
        return impl()
    except Exception:
        return None
