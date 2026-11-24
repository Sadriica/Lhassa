# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Waybar collectors: ledger, review log, systemd, power, CI. All synthetic and offline."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from lhassa.views import waybar_collect as wc

NOW = 1_800_000_000.0


class Out:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


def make_ledger(path: Path, rows: list[dict]) -> Path:
    """A real ledger schema (built by the ledger pack), filled with raw rows."""
    from agent_ledger.store import Store

    Store(path).close()
    conn = sqlite3.connect(path)
    for i, r in enumerate(rows):
        conn.execute(
            "INSERT INTO events (id, ts, host, kind, session, cost_usd, outcome, detail) VALUES (?,?,?,?,?,?,?,?)",
            (f"e{i}", r.get("ts", NOW - 10), r.get("host", "claude-code"), r.get("kind", "tool_call"),
             r.get("session", "s1"), r.get("cost"), r.get("outcome", "ok"), json.dumps(r.get("detail", {}))),
        )
    conn.commit()
    conn.close()
    return path


def repo(tmp_path: Path, name: str) -> str:
    d = tmp_path / name
    (d / ".git").mkdir(parents=True)
    return str(d)


# -- names and privacy ----------------------------------------------------------


def test_repo_name_is_the_git_root_basename_not_a_path(tmp_path):
    root = repo(tmp_path, "witara")
    sub = Path(root) / "web" / "src"
    sub.mkdir(parents=True)
    assert wc.repo_name(str(sub)) == "witara"
    assert wc.repo_name(root) == "witara"


def test_a_generic_directory_name_borrows_its_parent(tmp_path):
    assert wc.repo_name(repo(tmp_path / "lhassa", "app")) == "lhassa"


def test_a_linked_worktree_reports_the_main_repo(tmp_path):
    main = Path(repo(tmp_path, "lhassa-main"))
    (main / ".git" / "worktrees" / "wt1").mkdir(parents=True)
    wt = tmp_path / "elsewhere" / "wt1"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/wt1\n")
    assert wc.repo_name(str(wt)) == "lhassa-main"


@pytest.mark.parametrize("where", ["personal", "lhassa/personal/notes", "x/personal", "~/.ssh/keys", "~/.lhassa/review"])
def test_private_working_directories_give_no_name(tmp_path, where):
    cwd = os.path.expanduser(where) if where.startswith("~") else str(tmp_path / where)
    if not where.startswith("~"):
        os.makedirs(cwd, exist_ok=True)
    assert wc.repo_name(cwd) == ""


def test_a_private_subfolder_of_a_normal_repo_is_dropped_too(tmp_path):
    root = repo(tmp_path, "witara")
    p = Path(root) / "products" / "lhassa" / "personal"
    p.mkdir(parents=True)
    assert wc.repo_name(str(p)) == ""


def test_junk_cwd_gives_no_name():
    for junk in ("", None, 5, "/"):
        assert wc.repo_name(junk) == ""


# -- the ledger -----------------------------------------------------------------


def test_active_sessions_per_host_within_the_window(tmp_path):
    a, b = repo(tmp_path, "alpha"), repo(tmp_path, "beta")
    db = make_ledger(tmp_path / "l.db", [
        {"session": "s1", "detail": {"cwd": a}},
        {"session": "s2", "detail": {"cwd": b}},
        {"session": "s2", "detail": {"cwd": b}, "ts": NOW - 20},
        {"session": "s3", "host": "codex", "detail": {"cwd": a}},
        {"session": "old", "ts": NOW - 4000, "detail": {"cwd": a}},      # outside the window
        {"session": "own", "host": "lhassa", "kind": "llm_call"},         # Lhassa itself is not an agent
        {"session": "mystery", "host": "unknownhost"},                    # not a known agent host
    ])
    got = wc.collect_ledger(db, now=NOW)["agents"]
    assert got["total"] == 3 and got["hosts"] == {"claude-code": 2, "codex": 1}
    assert {(s["host"], s["repo"]) for s in got["sessions"]} == {
        ("claude-code", "alpha"), ("claude-code", "beta"), ("codex", "alpha")}


def test_window_focus_and_resource_samples_are_not_agent_work(tmp_path):
    db = make_ledger(tmp_path / "l.db", [
        {"host": "orca", "kind": "note", "session": "w", "detail": {"type": "window_focus", "title": "SECRET TITLE"}},
        {"host": "orca", "kind": "note", "session": "r", "detail": {"type": "resource_sample"}},
    ])
    got = wc.collect_ledger(db, now=NOW)
    assert got["agents"]["total"] == 0 and got["today"]["events"] == 0
    assert "SECRET" not in json.dumps(got)


def test_a_private_cwd_never_reaches_the_output_or_the_ci_candidates(tmp_path):
    ok = repo(tmp_path, "alpha")
    secret = tmp_path / "lhassa" / "personal" / "diary"
    secret.mkdir(parents=True)
    ssh = os.path.expanduser("~/.ssh")
    db = make_ledger(tmp_path / "l.db", [
        {"session": "s1", "detail": {"cwd": ok}},
        {"session": "s2", "detail": {"cwd": str(secret)}},
        {"session": "s3", "detail": {"cwd": ssh}},
    ])
    got = wc.collect_ledger(db, now=NOW)
    text = json.dumps(got)
    assert "diary" not in text and ".ssh" not in text and "personal" not in text
    assert got["cwds"] == [ok]
    assert got["agents"]["total"] == 3  # still counted, just nameless
    assert sorted(s["repo"] for s in got["agents"]["sessions"]) == ["", "", "alpha"]


def test_prompt_text_in_the_ledger_never_leaves(tmp_path):
    db = make_ledger(tmp_path / "l.db", [{"detail": {"cwd": repo(tmp_path, "alpha"), "prompt": "my secret plan"},
                                          "ts": NOW - 5}])
    assert "secret plan" not in json.dumps(wc.collect_ledger(db, now=NOW))


def test_today_totals_and_the_most_active_repo(tmp_path):
    a, b = repo(tmp_path, "alpha"), repo(tmp_path, "beta")
    midnight = wc._local_midnight(NOW)
    db = make_ledger(tmp_path / "l.db", (
        [{"detail": {"cwd": a}, "ts": midnight + 60, "cost": 0.5}] * 1
        + [{"detail": {"cwd": b}, "ts": midnight + 61, "cost": 0.25, "session": f"x{i}"} for i in range(3)]
        + [{"detail": {"cwd": a}, "ts": midnight - 100}]  # yesterday
    ))
    today = wc.collect_ledger(db, now=NOW)["today"]
    assert today["events"] == 4 and today["api_usd"] == 1.25 and today["top_repo"] == "beta"


def test_missing_corrupt_or_foreign_ledgers_degrade_to_empty(tmp_path):
    empty = wc.collect_ledger(tmp_path / "nope.db", now=NOW)
    assert empty["agents"]["total"] == 0 and empty["today"]["events"] == 0 and empty["cwds"] == []
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 50)
    assert wc.collect_ledger(junk, now=NOW)["agents"]["total"] == 0
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE x (a)").connection.close()
    assert wc.collect_ledger(other, now=NOW)["agents"]["total"] == 0
    assert wc.collect_pending(other, now=NOW) == []


def test_reading_the_ledger_never_writes_it(tmp_path):
    db = make_ledger(tmp_path / "l.db", [{"detail": {"cwd": repo(tmp_path, "alpha")}}])
    def snapshot():  # SQLite may create empty -wal/-shm bookkeeping files; the data file must not change
        return (db.read_bytes(), db.stat().st_mtime_ns)

    before = snapshot()
    wc.collect_ledger(db, now=NOW)
    wc.collect_pending(db, now=NOW)
    assert snapshot() == before


def test_pending_decisions_that_nobody_answered(tmp_path):
    db = make_ledger(tmp_path / "l.db", [
        {"host": "orca", "kind": "decision", "outcome": "pending"},
        {"host": "claude-code", "kind": "decision", "outcome": "pending", "ts": NOW - 5},
    ])
    conn = sqlite3.connect(db)
    answered = conn.execute("SELECT id FROM events WHERE host='claude-code'").fetchone()[0]
    conn.execute("INSERT INTO events (id, ts, host, kind, outcome, detail) VALUES ('r1', ?, 'claude-code', 'decision', 'ok', ?)",
                 (NOW - 1, json.dumps({"resolves": answered})))
    conn.commit()
    conn.close()
    got = wc.collect_pending(db, now=NOW)
    assert [g["label"] for g in got] == ["orca"] and got[0]["kind"] == "ledger"


# -- the review log -------------------------------------------------------------


def stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


def write_log(path: Path, records: list) -> Path:
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in records) + "\n")
    return path


def rec(ts, event, repo_path="", crit=0, warn=0, **extra):
    return {"ts": stamp(ts), "source": "git-hook", "event": event, "repo": repo_path,
            "counts": {"info": 0, "warn": warn, "critical": crit}, **extra}


def test_review_critical_findings_and_pushes_with_findings(tmp_path):
    a, b = repo(tmp_path, "alpha"), repo(tmp_path, "beta")
    midnight = wc._local_midnight(NOW)
    log = write_log(tmp_path / "review.jsonl", [
        rec(midnight - 90_000, "commit-msg", a, crit=1),          # older than a day: gone
        "not json at all",
        rec(NOW - 600, "commit-msg", a),
        rec(NOW - 500, "commit-msg", a, crit=2),
        rec(NOW - 400, "pre-push", b, warn=1),
        rec(NOW - 300, "pre-push", a),                            # clean push: only counted
        {"ts": "garbage", "event": "pre-push"},
        rec(NOW - 200, "check", a, crit=1),
    ])
    got = wc.collect_review(log, now=NOW)
    assert got["commits"] == 2 and got["push_count"] == 2
    assert {(c["label"], c["n"]) for c in got["critical"]} == {("alpha", 2), ("alpha", 1)}
    assert got["pushes"] == [{"kind": "push", "label": "beta", "n": 1, "source": "pre-push review"}]


def test_a_finding_without_a_repo_is_labelled_by_its_source(tmp_path):
    log = write_log(tmp_path / "r.jsonl", [{**rec(NOW - 5, "check", "", crit=1), "source": "claude-pretooluse"}])
    assert wc.collect_review(log, now=NOW)["critical"][0]["label"] == "claude-pretooluse"


def test_review_of_a_private_repo_is_not_surfaced_by_name(tmp_path):
    secret = tmp_path / "lhassa" / "personal" / "x"
    secret.mkdir(parents=True)
    log = write_log(tmp_path / "review.jsonl", [rec(NOW - 100, "pre-push", str(secret), crit=3)])
    got = wc.collect_review(log, now=NOW)
    assert got["critical"] == [] and got["pushes"] == []
    assert "personal" not in json.dumps(got)


def test_review_log_missing_or_huge_is_safe(tmp_path):
    assert wc.collect_review(tmp_path / "none.jsonl", now=NOW) == {
        "critical": [], "pushes": [], "commits": 0, "push_count": 0}
    a = repo(tmp_path, "alpha")
    pad = [rec(NOW - 80_000, "commit-msg", a, crit=1, pad="x" * 2000) for _ in range(400)]
    log = write_log(tmp_path / "big.jsonl", pad + [rec(NOW - 5, "pre-push", a, warn=1)])
    assert log.stat().st_size > wc.REVIEW_TAIL_BYTES
    assert wc.collect_review(log, now=NOW)["pushes"][0]["label"] == "alpha"


def test_review_errors_are_not_counted_as_commits(tmp_path):
    log = write_log(tmp_path / "r.jsonl", [rec(NOW - 5, "commit-msg", "", error="OSError")])
    assert wc.collect_review(log, now=NOW)["commits"] == 0


# -- systemd, power, machine ----------------------------------------------------


def test_failed_lhassa_units_only():
    out = ("lhassa-monitor.service loaded failed failed Monitor\n"
           "pipewire.service loaded failed failed Audio\n"
           "lhassa-health.service loaded failed failed Health\n")
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return Out(out)

    got = wc.failed_services(run)
    assert [g["label"] for g in got] == ["lhassa-monitor", "lhassa-health"]
    assert seen == [["systemctl", "--user", "--failed", "--no-legend", "--plain", "--no-pager"]]


def test_systemctl_missing_or_hanging_gives_nothing():
    def boom(cmd, **kw):
        raise FileNotFoundError

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)

    assert wc.failed_services(boom) == [] and wc.failed_services(hang) == []


def test_power_mode_reads_the_tlp_marker(tmp_path):
    conf = tmp_path / "99.conf"
    conf.write_text('# Generated by power-mode.\n# mode: "performance"\nX=1\n')
    assert wc.power_mode(str(conf)) == "performance"
    conf.write_text('# mode: "ahorro"\n')
    assert wc.power_mode(str(conf)) == "saver"
    assert wc.power_mode(str(tmp_path / "missing")) == ""


def test_temperature_battery_and_cpu_from_fake_sysfs(tmp_path):
    hw = tmp_path / "hwmon"
    (hw / "hwmon0").mkdir(parents=True)
    (hw / "hwmon0" / "name").write_text("nvme\n")
    (hw / "hwmon0" / "temp1_input").write_text("30000\n")
    (hw / "hwmon3").mkdir()
    (hw / "hwmon3" / "name").write_text("coretemp\n")
    (hw / "hwmon3" / "temp1_input").write_text("62500\n")
    assert wc.package_temp(str(hw)) == 62.5
    assert wc.package_temp(str(tmp_path / "none")) is None
    ps = tmp_path / "ps"
    (ps / "BAT0").mkdir(parents=True)
    (ps / "AC0").mkdir()
    (ps / "BAT0" / "capacity").write_text("81\n")
    (ps / "BAT0" / "status").write_text("Charging\n")
    assert wc.battery(str(ps)) == (81, True)
    assert wc.battery(str(tmp_path / "none")) == (None, False)
    stat = tmp_path / "stat"
    meter = wc.CpuMeter(str(stat))
    stat.write_text("cpu 100 0 100 800 0 0 0 0\n")
    assert meter.sample() is None  # needs two samples
    stat.write_text("cpu 150 0 150 900 0 0 0 0\n")
    assert meter.sample() == 50
    assert wc.CpuMeter(str(tmp_path / "nope")).sample() is None


# -- CI -------------------------------------------------------------------------


def test_github_remotes_are_parsed_and_credentials_dropped():
    assert wc.parse_remote("git@github.com:me/proj.git") == "me/proj"
    assert wc.parse_remote("https://user:ghp_TOKEN@github.com/me/proj.git") == "me/proj"
    assert wc.parse_remote("https://github.com/me/proj") == "me/proj"
    assert wc.parse_remote("https://gitlab.com/me/proj.git") == ""
    assert wc.parse_remote("") == ""


def test_configured_repos_come_from_the_variable_and_are_capped():
    env = {"LHASSA_WAYBAR_CI_REPOS": "a/b, c/d ,bad slug,../etc,a/b," + ",".join(f"o/r{i}" for i in range(20))}
    got = wc.configured_repos(env)
    assert got[:2] == ["a/b", "c/d"] and len(got) == wc.CI_MAX_REPOS


def test_repos_fall_back_to_recent_sessions_with_a_github_remote(tmp_path):
    gh, other, plain = repo(tmp_path, "gh"), repo(tmp_path, "other"), repo(tmp_path, "plain")

    def run(cmd, **kw):
        root = cmd[2]
        return {gh: Out("git@github.com:me/gh.git\n"), other: Out("https://gitlab.com/me/o.git\n")}.get(
            root, Out("", 1))

    got = wc.configured_repos({}, cwds=[gh, other, plain, gh], run=run)
    assert got == ["me/gh"]
    assert wc.configured_repos({"LHASSA_WAYBAR_CI_AUTO": "0"}, cwds=[gh], run=run) == []
    # an explicit list wins over discovery
    assert wc.configured_repos({"LHASSA_WAYBAR_CI_REPOS": "x/y"}, cwds=[gh], run=run) == ["x/y"]


def test_ci_red_reads_the_latest_completed_run():
    def run_with(payload, code=0):
        return lambda cmd, **kw: Out(payload, code)

    assert wc.ci_red("o/r", "gh", run_with('[{"conclusion":"failure"}]')) is True
    assert wc.ci_red("o/r", "gh", run_with('[{"conclusion":"success"}]')) is False
    assert wc.ci_red("o/r", "gh", run_with("[]")) is False
    assert wc.ci_red("o/r", "gh", run_with("", 1)) is None
    assert wc.ci_red("o/r", "gh", run_with("garbage")) is None

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 15)

    assert wc.ci_red("o/r", "gh", boom) is None


def test_ci_command_is_a_plain_list_with_the_repo(monkeypatch):
    seen = []
    wc.ci_red("me/proj", "/x/gh", lambda cmd, **kw: seen.append((cmd, kw)) or Out("[]"))
    assert seen[0][0][:5] == ["/x/gh", "run", "list", "--repo", "me/proj"]
    assert seen[0][1]["timeout"] <= 15


async def test_ci_is_cached_for_the_ttl_and_red_repos_become_items(monkeypatch):
    polls = []

    def run(cmd, **kw):
        polls.append(cmd[4])
        return Out('[{"conclusion":"failure"}]' if cmd[4] == "me/bad" else '[{"conclusion":"success"}]')

    monkeypatch.setattr(wc, "gh_binary", lambda: "/x/gh")
    clock = [0.0]
    watcher = wc.CiWatcher(ttl=300, clock=lambda: clock[0], run=run)
    repos = ["me/bad", "me/good"]
    assert watcher.stale()
    assert await watcher.refresh(repos) == ["me/bad"]
    assert watcher.failing(repos) == [{"kind": "ci", "label": "bad", "source": "GitHub Actions"}]
    clock[0] = 299
    assert not watcher.stale()
    watcher.failing(repos)
    await asyncio.sleep(0)
    assert len(polls) == 2  # still the first poll: no refresh inside the TTL
    clock[0] = 301
    assert watcher.stale()
    watcher.failing(repos)  # starts a background refresh and answers from the cache at once
    await watcher._task
    assert len(polls) == 4


async def test_a_repo_that_left_the_list_leaves_the_items(monkeypatch):
    monkeypatch.setattr(wc, "gh_binary", lambda: "/x/gh")
    watcher = wc.CiWatcher(run=lambda cmd, **kw: Out('[{"conclusion":"failure"}]'))
    await watcher.refresh(["me/a", "me/b"])
    assert [i["label"] for i in watcher.failing(["me/b"])] == ["b"]


async def test_no_gh_or_no_network_means_no_ci_items(monkeypatch):
    monkeypatch.setattr(wc, "gh_binary", lambda: None)
    watcher = wc.CiWatcher(run=lambda *a, **k: pytest.fail("gh must not run"))
    assert await watcher.refresh(["me/a"]) == [] and watcher.failing(["me/a"]) == []
    monkeypatch.setattr(wc, "gh_binary", lambda: "/x/gh")

    def offline(cmd, **kw):
        raise OSError("network down")

    assert await wc.CiWatcher(run=offline).refresh(["me/a"]) == []
    assert await wc.CiWatcher(run=lambda c, **k: Out("", 1)).refresh(["me/a"]) == []


def test_gh_binary_falls_back_to_the_local_bin(monkeypatch, tmp_path):
    monkeypatch.setattr(wc.shutil, "which", lambda name: None)
    fake_home = tmp_path / "home"
    (fake_home / ".local" / "bin").mkdir(parents=True)
    (fake_home / ".local" / "bin" / "gh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(wc.Path, "home", classmethod(lambda cls: fake_home))
    assert wc.gh_binary() == str(fake_home / ".local" / "bin" / "gh")
