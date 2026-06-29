"""Tests for potts.health.checks — focus on the pure parsers + safe aggregation."""

import json

from potts.health import checks


# ── parse_failed_units ───────────────────────────────────────────────────────


def test_parse_failed_units_basic():
    text = (
        "nginx.service loaded failed failed A high performance web server\n"
        "docker.service loaded failed failed Docker Application Container Engine\n"
    )
    assert checks.parse_failed_units(text) == ["nginx.service", "docker.service"]


def test_parse_failed_units_strips_bullets_and_blanks():
    text = "\n● bluetooth.service loaded failed failed Bluetooth\n\n   \n"
    assert checks.parse_failed_units(text) == ["bluetooth.service"]


def test_parse_failed_units_empty():
    assert checks.parse_failed_units("") == []


# ── parse_meminfo / meminfo_usage ────────────────────────────────────────────


SAMPLE_MEMINFO = (
    "MemTotal:       16384000 kB\n"
    "MemFree:         1000000 kB\n"
    "MemAvailable:    8192000 kB\n"
    "Buffers:          200000 kB\n"
    "SwapTotal:       4000000 kB\n"
    "SwapFree:        3000000 kB\n"
)


def test_parse_meminfo_extracts_fields():
    parsed = checks.parse_meminfo(SAMPLE_MEMINFO)
    assert parsed["MemTotal"] == 16384000
    assert parsed["MemAvailable"] == 8192000
    assert parsed["SwapTotal"] == 4000000


def test_parse_meminfo_ignores_garbage_lines():
    parsed = checks.parse_meminfo("garbage\nMemTotal: 100 kB\nBadLine:\n")
    assert parsed == {"MemTotal": 100}


def test_meminfo_usage_percentages():
    usage = checks.meminfo_usage(checks.parse_meminfo(SAMPLE_MEMINFO))
    # (16384000 - 8192000) / 16384000 == 50%
    assert usage["mem_used_pct"] == 50.0
    # (4000000 - 3000000) / 4000000 == 25%
    assert usage["swap_used_pct"] == 25.0


def test_meminfo_usage_no_swap_division():
    usage = checks.meminfo_usage({"MemTotal": 0, "SwapTotal": 0})
    assert usage["mem_used_pct"] == 0.0
    assert usage["swap_used_pct"] == 0.0


# ── parse_git_status ─────────────────────────────────────────────────────────


def test_parse_git_status_dirty_and_tracking():
    text = (
        "## main...origin/main [ahead 2, behind 1]\n"
        " M potts/health/checks.py\n"
        "?? scratch.txt\n"
    )
    info = checks.parse_git_status(text)
    assert info == {
        "branch": "main",
        "uncommitted": 2,
        "ahead": 2,
        "behind": 1,
    }


def test_parse_git_status_clean():
    info = checks.parse_git_status("## main...origin/main\n")
    assert info["uncommitted"] == 0
    assert info["ahead"] == 0
    assert info["behind"] == 0
    assert info["branch"] == "main"


def test_parse_git_status_no_upstream():
    info = checks.parse_git_status("## feature-x\n M a.py\n")
    assert info["branch"] == "feature-x"
    assert info["uncommitted"] == 1
    assert info["ahead"] == 0


# ── disk threshold logic ─────────────────────────────────────────────────────


def test_disk_level_thresholds():
    assert checks.disk_level(50.0) == "info"
    assert checks.disk_level(89.9) == "info"
    assert checks.disk_level(90.0) == "warn"
    assert checks.disk_level(96.0) == "warn"
    assert checks.disk_level(97.0) == "alert"
    assert checks.disk_level(99.9) == "alert"


def test_disk_level_custom_thresholds():
    assert checks.disk_level(80.0, threshold=80.0, critical=95.0) == "warn"
    assert checks.disk_level(95.0, threshold=80.0, critical=95.0) == "alert"


def test_check_disk_real_mount_does_not_raise():
    result = checks.check_disk(mounts=["/"])
    assert result["name"] == "disk"
    assert result["level"] in checks._LEVELS
    assert isinstance(result["detail"], dict)


def test_check_disk_unreadable_mount():
    result = checks.check_disk(mounts=["/definitely/not/a/real/mount/xyz"])
    assert result["detail"] == {}
    assert result["level"] == "info"


# ── worst_level helper ───────────────────────────────────────────────────────


def test_worst_level_ordering():
    assert checks.worst_level(["info", "warn", "notice"]) == "warn"
    assert checks.worst_level(["info", "alert", "warn"]) == "alert"
    assert checks.worst_level([]) == "info"


# ── check_mem_pressure with a sample file ────────────────────────────────────


def test_check_mem_pressure_from_file(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(SAMPLE_MEMINFO)
    result = checks.check_mem_pressure(meminfo_path=meminfo)
    assert result["name"] == "mem_pressure"
    assert result["level"] == "info"  # 50% RAM, 25% swap — healthy
    assert result["detail"]["mem_used_pct"] == 50.0


def test_check_mem_pressure_high_ram_warns(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 1000 kB\nMemAvailable: 50 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
    )
    result = checks.check_mem_pressure(meminfo_path=meminfo)
    assert result["level"] == "warn"


def test_check_mem_pressure_missing_file(tmp_path):
    result = checks.check_mem_pressure(meminfo_path=tmp_path / "nope")
    assert result["level"] == "info"
    assert result["detail"] == {}


# ── git_repos_under ──────────────────────────────────────────────────────────


def test_git_repos_under_finds_children(tmp_path):
    repo = tmp_path / "proj"
    (repo / ".git").mkdir(parents=True)
    (tmp_path / "not-a-repo").mkdir()
    found = checks.git_repos_under(tmp_path)
    assert found == [repo]


def test_git_repos_under_self(tmp_path):
    (tmp_path / ".git").mkdir()
    assert checks.git_repos_under(tmp_path) == [tmp_path]


# ── run_all writes JSON to a tmp dir and never raises ────────────────────────


def test_run_all_writes_json_and_no_notify(tmp_path, monkeypatch):
    # Simulate every external binary being absent so nothing shells out.
    monkeypatch.setattr(checks.shutil, "which", lambda _name: None)

    digest = run_all_capture(tmp_path)

    # All four checks ran and snapshots exist.
    for name in ("disk", "failed_units", "git_status", "mem_pressure"):
        snapshot = tmp_path / f"{name}.json"
        assert snapshot.exists()
        data = json.loads(snapshot.read_text())
        assert data["name"] == name
        assert data["level"] in checks._LEVELS

    assert (tmp_path / "digest.json").exists()
    assert digest["worst_level"] in checks._LEVELS
    assert digest["alerted"] == []
    assert set(digest["results"]) == {
        "disk",
        "failed_units",
        "git_status",
        "mem_pressure",
    }


def run_all_capture(out_dir):
    """Helper: run_all with notifications disabled, returning the digest."""
    return checks.run_all(out_dir=out_dir, notify=False)


def test_run_all_survives_a_failing_check(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setitem(checks.CHECKS, "disk", boom)
    digest = checks.run_all(out_dir=tmp_path, notify=False)
    assert digest["results"]["disk"]["level"] == "info"
    assert "errored" in digest["results"]["disk"]["summary"]


def test_run_all_does_not_touch_real_home(tmp_path, monkeypatch):
    # If something tried to use the default dir, this would point it at tmp.
    monkeypatch.setattr(checks.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(checks.shutil, "which", lambda _name: None)
    digest = checks.run_all(notify=False)
    assert (tmp_path / ".potts" / "health" / "digest.json").exists()
    assert digest["ok"] in (True, False)


# ── format_digest renders without an LLM ─────────────────────────────────────


def test_format_digest_is_plain_text(tmp_path):
    digest = checks.run_all(out_dir=tmp_path, notify=False)
    text = checks.format_digest(digest)
    assert "Potts — PC state check" in text
    for name in checks.CHECKS:
        assert name in text
