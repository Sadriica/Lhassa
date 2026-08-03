"""The host platform layer — pure parsers plus injected platform selection.

Every test here runs identically on Linux and macOS: the parsers are pure, and
platform-dependent behaviour is exercised by INJECTING a platform (a registered
fake, or ``LHASSA_PLATFORM``), never by monkeypatching ``os.name`` /
``platform.system()``. That is the point of the layer — a Linux CI run really
does cover the macOS branches.
"""

import pytest

from lhassa import host


# ── pure parsers ──────────────────────────────────────────────────────────────


def test_parse_proc_uptime():
    assert host.parse_proc_uptime("15916.18 123456.00") == pytest.approx(15916.18)


@pytest.mark.parametrize("bad", ["", "   ", "not-a-number", "\n"])
def test_parse_proc_uptime_degrades_to_none(bad):
    """A bad reading is None — never a plausible zero."""
    assert host.parse_proc_uptime(bad) is None


def test_parse_boottime_uses_injected_now():
    text = "{ sec = 1000, usec = 500000 } Sun Aug  3 10:00:00 2026"
    assert host.parse_boottime(text, now=4600.0) == pytest.approx(3600.0)


def test_parse_boottime_rejects_a_future_boot():
    text = "{ sec = 9999, usec = 0 }"
    assert host.parse_boottime(text, now=1000.0) is None


def test_parse_boottime_degrades_on_garbage():
    assert host.parse_boottime("no seconds here", now=1.0) is None


def test_parse_vm_stat_counts_free_and_speculative_as_available():
    text = (
        "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
        "Pages free:                     100.\n"
        "Pages active:                   300.\n"
        "Pages inactive:                  50.\n"
        "Pages speculative:               50.\n"
        "Pages wired down:               100.\n"
        "Pages occupied by compressor:   400.\n"
    )
    # available = 100 + 50 = 150 of 1000 total → 85% in use
    assert host.parse_vm_stat(text) == pytest.approx(85.0)


def test_parse_vm_stat_degrades_on_unparseable_output():
    assert host.parse_vm_stat("command not found") is None
    assert host.parse_vm_stat("") is None


def test_parse_vm_stat_is_clamped_to_a_percentage():
    text = "Pages free: 0.\nPages active: 10.\n"
    value = host.parse_vm_stat(text)
    assert value is not None and 0.0 <= value <= 100.0


# ── platform selection is configuration, not surgery ─────────────────────────


def test_current_platform_takes_an_injected_system(monkeypatch):
    # The env override deliberately outranks the injected system (see the next
    # test), so it is cleared here — otherwise this test silently depends on
    # whatever LHASSA_PLATFORM the suite happens to run under.
    monkeypatch.delenv("LHASSA_PLATFORM", raising=False)
    assert host.current_platform_name(system="Darwin") == "darwin"
    assert host.current_platform_name(system="Linux") == "linux"


def test_env_override_wins_so_selection_is_configuration(monkeypatch):
    monkeypatch.setenv("LHASSA_PLATFORM", "darwin")
    assert host.current_platform_name(system="Linux") == "darwin"


def test_both_tiers_are_registered():
    assert host.PLATFORMS.get_or_none("linux") is not None
    assert host.PLATFORMS.get_or_none("darwin") is not None


def test_unknown_platform_returns_none_rather_than_a_wrong_stub(monkeypatch):
    monkeypatch.setenv("LHASSA_PLATFORM", "plan9")
    assert host.get_platform() is None


def test_a_platform_is_drop_in(monkeypatch):
    """Adding a platform must be a registration, never a core edit."""

    class _Fake:
        name = "fake"

        def uptime_seconds(self):
            return 42.0

        def memory(self):
            return None

        def failed_services(self):
            return []

        def notify_argv(self, title, message):
            return None

        def sandbox_prefix(self, workdir):
            return None

    host.register_platform("fake-tier3", _Fake, override=True)
    monkeypatch.setenv("LHASSA_PLATFORM", "fake-tier3")
    plat = host.get_platform()
    assert plat is not None and plat.uptime_seconds() == 42.0


# ── the Linux collector reads through an injected path ───────────────────────


def test_linux_uptime_reads_the_injected_path(tmp_path):
    p = tmp_path / "uptime"
    p.write_text("7200.00 1.00")
    plat = host.LinuxPlatform(uptime_path=str(p))
    assert plat.uptime_seconds() == pytest.approx(7200.0)


def test_linux_uptime_missing_file_is_none_not_zero(tmp_path):
    plat = host.LinuxPlatform(uptime_path=str(tmp_path / "nope"))
    assert plat.uptime_seconds() is None


# ── the macOS memory reading is qualified, never passed off as Linux's ───────


def test_macos_memory_reading_declares_it_is_not_comparable():
    reading = host.MemoryReading(
        used_pct=85.0, swap_pct=0.0, source="vm_stat", comparable=False
    )
    assert reading.comparable is False
    assert reading.source == "vm_stat"


def test_linux_memory_reading_defaults_to_comparable():
    assert host.MemoryReading(used_pct=10.0, swap_pct=0.0, source="meminfo").comparable
