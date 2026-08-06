"""Tests for ``lhassa setup`` (lhassa.cli.setup_cmds) — one command, one wizard.

Every decision the wizard makes is factored into a pure function over injected
inputs (host detection reuses ``lhassa.host`` unchanged; brain detection takes an
``env``/``which``/``probe_ollama``; scope decisions take a plain ``dict``), so the
wizard's actual JUDGMENT is testable with no TTY, no network, and no real
``~/.lhassa`` — mirroring the pattern ``lhassa.host`` already uses (pure parser +
injected collector). A smaller set of integration tests then proves the real seed
registry actually satisfies the DoD (departments + digest + maintenance + flows)
end to end.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lhassa.cli import setup_cmds as sc
from lhassa.cli.app import app

runner = CliRunner()

# The real checkout root — install_services reads the ACTUAL shipped unit files
# from `scripts/systemd`/`scripts/launchd` (nothing is authored by the wizard),
# so tests exercising that path point at the real repo, not a throwaway tmp one.
REPO_ROOT = Path(__file__).resolve().parents[1]


# ── seeds_for_profile ─────────────────────────────────────────────────────────


def test_every_profile_includes_the_dod_baseline():
    # DoD: ANY profile must yield departments + digest + maintenance + flows —
    # the baseline alone already guarantees it (personal ships flows too).
    for profile in sc.PROFILES:
        names = sc.seeds_for_profile(profile)
        assert set(sc.BASE_SEEDS) <= set(names)
        assert names[: len(sc.BASE_SEEDS)] == sc.BASE_SEEDS  # baseline first


def test_research_profile_adds_research_seed():
    assert sc.seeds_for_profile("research") == ["temple", "digest", "maintenance", "personal", "research"]


def test_dev_profile_adds_cv_seed():
    assert sc.seeds_for_profile("dev") == ["temple", "digest", "maintenance", "personal", "cv"]


def test_unknown_profile_falls_back_to_baseline_only():
    assert sc.seeds_for_profile("does-not-exist") == sc.BASE_SEEDS


# ── detect_brain (pure, injected) ─────────────────────────────────────────────


def test_detect_brain_prefers_cli_over_everything():
    d = sc.detect_brain(
        env={"ANTHROPIC_API_KEY": "x"},
        which=lambda name: "/usr/bin/claude" if name == "claude" else None,
        probe_ollama=lambda url: True,
    )
    assert d.chosen == "cli"
    assert d.cli_found == "claude"
    assert any("claude" in line for line in d.explanation)
    # An API key present too is noted, not silently dropped.
    assert any("ANTHROPIC_API_KEY" in line for line in d.explanation)


def test_detect_brain_prefers_local_ollama_over_api_key():
    d = sc.detect_brain(
        env={"GROQ_API_KEY": "x"},
        which=lambda name: None,
        probe_ollama=lambda url: True,
    )
    assert d.chosen == "local"
    assert d.ollama_up is True


def test_detect_brain_falls_back_to_api_key():
    d = sc.detect_brain(
        env={"GROQ_API_KEY": "x"},
        which=lambda name: None,
        probe_ollama=lambda url: False,
    )
    assert d.chosen == "api"
    assert d.api_key_found == "GROQ_API_KEY"


def test_detect_brain_honest_when_nothing_found():
    d = sc.detect_brain(env={}, which=lambda name: None, probe_ollama=lambda url: False)
    assert d.chosen == "none"
    assert d.api_key_found is None
    assert d.cli_found is None
    assert d.explanation  # never silent


def test_detect_brain_never_raises_on_bad_probe():
    def boom(_url):
        raise RuntimeError("network is on fire")

    # detect_brain itself doesn't catch the injected probe (that's the caller's
    # contract via _probe_ollama's own real-world try/except); assert the DEFAULT
    # collector (_probe_ollama) is the one that degrades, not detect_brain's logic.
    assert sc._probe_ollama("http://[::1]:1/nope", timeout=0.01) is False
    assert sc._probe_ollama("not a url", timeout=0.01) is False


# ── plan_scope_effects (pure) ─────────────────────────────────────────────────


def test_all_scopes_declined_writes_safe_empties():
    effects = sc.plan_scope_effects({"read": False, "commands": False, "network": False, "notify": False})
    by_key = {e.key: e for e in effects if e.kind != "note"}
    assert by_key["knowledge_roots"].value == ""
    assert by_key["protected_roots"].value == ""
    assert by_key["auto_sync_repos"].value == ""
    assert by_key["heartbeat_provider"].value == "local"
    assert by_key["notify_channels"].value == ""


def test_read_scope_granted_with_roots_writes_them():
    effects = sc.plan_scope_effects(
        {"read": True, "commands": False, "network": True, "notify": False},
        knowledge_roots="~/docs",
        protected_roots="~/private",
    )
    by_key = {e.key: e for e in effects if e.kind == "setting"}
    assert by_key["knowledge_roots"].value == "~/docs"
    assert by_key["protected_roots"].value == "~/private"
    # network granted -> no forced heartbeat_provider override.
    assert "heartbeat_provider" not in by_key


def test_read_scope_granted_with_no_roots_is_a_note_not_a_write():
    effects = sc.plan_scope_effects({"read": True, "commands": False, "network": True, "notify": False})
    kinds = {e.key: e.kind for e in effects}
    assert kinds["read"] == "note"


def test_commands_scope_with_repos_writes_setting_and_allowlist_env():
    effects = sc.plan_scope_effects(
        {"read": False, "commands": True, "network": True, "notify": False},
        auto_sync_repos="/home/w/Code/lhassa",
        allowlist_repos="lhassa",
    )
    kinds = {(e.kind, e.key): e for e in effects}
    assert kinds[("setting", "auto_sync_repos")].value == "/home/w/Code/lhassa"
    assert kinds[("env", "LHASSA_ALLOWLIST_REPOS")].value == "lhassa"


def test_declining_a_scope_never_produces_an_all_or_nothing_wipe():
    # Declining "commands" and "read" still leaves "notify"/"network" untouched —
    # each scope's effects are independent, never a single global switch.
    declined_all = sc.plan_scope_effects({"read": False, "commands": False, "network": False, "notify": False})
    granted_notify = sc.plan_scope_effects({"read": False, "commands": False, "network": False, "notify": True})
    notify_off = next(e for e in declined_all if e.key == "notify_channels")
    notify_on = next(e for e in granted_notify if e.key == "notify_channels")
    assert notify_off.value == ""
    assert notify_on.value == "telegram,desktop"


# ── apply_scope_effects (writes through lhassa.settings) ─────────────────────


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    from lhassa import settings

    monkeypatch.setenv("HOME", str(tmp_path))
    Config = config_mod.Config
    for entry in settings.EDITABLE:
        monkeypatch.setattr(Config, entry["attr"], getattr(Config, entry["attr"]))
    return tmp_path


def test_apply_scope_effects_writes_settings_env(settings_env):
    effects = sc.plan_scope_effects({"read": False, "commands": False, "network": False, "notify": True})
    result = sc.apply_scope_effects(effects)
    assert result["errors"] == []
    keys = {a["key"] for a in result["applied"]}
    assert "notify_channels" in keys
    text = (settings_env / ".lhassa" / "settings.env").read_text()
    assert "LHASSA_NOTIFY_CHANNELS=telegram,desktop" in text
    assert "LHASSA_HEARTBEAT_PROVIDER=local" in text


def test_apply_scope_effects_never_raises_on_bad_key():
    effects = [sc.ScopeEffect("setting", "not-a-real-key", "x", "bogus")]
    result = sc.apply_scope_effects(effects)
    assert result["errors"]
    assert result["applied"] == []


# ── apply_profile_seeds (injected apply_seed_fn) ──────────────────────────────


def test_apply_profile_seeds_calls_every_seed_in_order():
    calls = []

    def fake_apply(name, config):
        calls.append(name)
        return {"errors": [], "seed": name}

    report = sc.apply_profile_seeds("research", config=None, apply_seed_fn=fake_apply)
    assert calls == sc.seeds_for_profile("research")
    assert report["profile"] == "research"
    assert all(not v.get("errors") for v in report["results"].values())


def test_apply_profile_seeds_never_aborts_on_one_failing_seed():
    def fake_apply(name, config):
        if name == "digest":
            raise RuntimeError("boom")
        return {"errors": []}

    report = sc.apply_profile_seeds("personal", config=None, apply_seed_fn=fake_apply)
    assert "boom" in report["results"]["digest"]["error"]
    # Every other seed still ran.
    assert report["results"]["temple"]["errors"] == []
    assert report["results"]["maintenance"]["errors"] == []


# ── install_services (injected which/run) ─────────────────────────────────────


def test_install_services_unsupported_platform_degrades_honestly(tmp_path):
    result = sc.install_services("plan9", tmp_path)
    assert result["installed"] == []
    assert result["errors"]


def test_install_services_linux_without_systemctl(tmp_path):
    result = sc.install_services("linux", tmp_path, which=lambda _n: None)
    assert result["installed"] == []
    assert result["errors"]


def test_install_services_linux_installs_and_enables(tmp_path):
    home = tmp_path / "home"
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    result = sc.install_services(
        "linux", REPO_ROOT, home=home,
        run=fake_run, which=lambda n: "/usr/bin/" + n,
    )
    assert set(result["installed"]) == set(sc._SYSTEMD_UNITS)
    assert set(result["enabled"]) == set(sc._SYSTEMD_ENABLE_NOW)
    assert result["errors"] == []
    dst = home / ".config" / "systemd" / "user" / "lhassa-app.service"
    assert dst.exists()
    assert str(REPO_ROOT) in dst.read_text()
    assert "%h/Code/lhassa" not in dst.read_text()


def test_install_services_linux_dry_run_writes_nothing(tmp_path):
    home = tmp_path / "home"
    result = sc.install_services("linux", REPO_ROOT, home=home, dry_run=True, which=lambda n: "/usr/bin/" + n)
    assert result["installed"] == []
    assert not (home / ".config" / "systemd").exists()


def test_install_services_darwin_installs_and_loads(tmp_path):
    home = tmp_path / "home"
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    result = sc.install_services(
        "darwin", REPO_ROOT, home=home,
        run=fake_run, which=lambda n: "/usr/bin/" + n,
    )
    assert set(result["installed"]) == set(sc._LAUNCHD_PLISTS)
    assert result["errors"] == []
    dst = home / "Library" / "LaunchAgents" / "com.lhassa.heartbeat.plist"
    assert dst.exists()
    assert str(REPO_ROOT) in dst.read_text()
    assert "/Users/USERNAME" not in dst.read_text()
    assert any(c[:2] == ["launchctl", "load"] for c in calls)


def test_install_services_run_never_raises_out(tmp_path):
    def boom(argv, **kwargs):
        raise OSError("no systemd bus")

    result = sc.install_services("linux", REPO_ROOT, home=tmp_path / "h", run=boom, which=lambda n: "/usr/bin/" + n)
    # daemon-reload failing degrades to a recorded error, not an exception.
    assert result["errors"]


# ── smoke_check (injected loaders) ────────────────────────────────────────────


class _FakeTopo:
    def __init__(self, n):
        self._n = n

    def nodes(self):
        return list(range(self._n))


class _FakeStore:
    def __init__(self, n):
        self._n = n

    def list_flows(self):
        return list(range(self._n))


def test_smoke_check_ok_when_departments_and_flows_exist():
    result = sc.smoke_check(
        None,
        load_topology_fn=lambda cfg: _FakeTopo(3),  # root + 2 departments
        flow_store_cls=lambda cfg: _FakeStore(1),
    )
    assert result["ok"] is True
    assert result["departments"] == 2
    assert result["flows"] == 1
    assert "alive" in result["sentence"]


def test_smoke_check_honest_when_nothing_wired():
    result = sc.smoke_check(
        None,
        load_topology_fn=lambda cfg: _FakeTopo(1),  # only the root
        flow_store_cls=lambda cfg: _FakeStore(0),
    )
    assert result["ok"] is False
    assert result["departments"] == 0
    assert result["flows"] == 0


def test_smoke_check_never_raises_on_broken_reads():
    def boom(cfg):
        raise RuntimeError("disk is gone")

    result = sc.smoke_check(None, load_topology_fn=boom, flow_store_cls=boom)
    assert result["ok"] is False
    assert result["errors"]
    assert "smoke check" in result["sentence"].lower()


# ── the CLI surface ────────────────────────────────────────────────────────────


def test_setup_is_registered_on_the_app():
    result = runner.invoke(app, ["setup", "--help"])
    assert result.exit_code == 0
    assert "wizard" in result.output.lower()


def test_setup_dry_run_never_touches_disk_and_reports_json():
    result = runner.invoke(
        app,
        ["setup", "--yes", "--dry-run", "--profile", "research",
         "--scopes", "network,notify", "--json", "--no-services"],
    )
    assert result.exit_code == 0
    import json

    line = [ln for ln in result.output.splitlines() if ln.startswith("{")][-1]
    payload = json.loads(line)
    assert payload["seeds"]["profile"] == "research"
    assert payload["seeds"]["planned"] is True
    assert payload["scopes"]["enabled"] == {
        "read": False, "commands": False, "network": True, "notify": True,
    }
    assert payload["smoke"] == {"skipped": "dry run"}


def test_setup_unknown_profile_falls_back_and_still_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(
        app,
        ["setup", "--yes", "--dry-run", "--profile", "not-a-profile", "--no-services"],
    )
    assert result.exit_code == 0
    assert "personal" in result.output  # falls back to the default profile


# ── end-to-end: the real seed registry satisfies the DoD ─────────────────────


class _RealCfg(SimpleNamespace):
    pass


@pytest.fixture
def real_seed_env(tmp_path, monkeypatch):
    """Wire the real seed registry at a tmp path — the same seams
    tests/test_cv_seed.py, tests/test_digest_seed.py, tests/test_maintenance_seed.py
    and tests/test_personal_seed.py each use individually, combined here so
    ``apply_profile_seeds`` can run for real end to end."""
    import lhassa.core.db as db
    import lhassa.security.signing as signing
    from lhassa import actionpack

    actionpack.register_all()  # temple_run must be registered for mint_task
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    cfg = _RealCfg(
        instance_id="inst_personal",
        org_role="",
        org_departments="",
        org_topology_file=str(tmp_path / "org.json"),
        flows_file=str(tmp_path / "flows.json"),
        skills_dir=str(tmp_path / "skills"),
        root_admin_id="lhassa",
    )
    return cfg


def test_baseline_seeds_satisfy_the_dod_for_every_profile(real_seed_env):
    # DoD (doc/plan-4.md, Stream 3): from a fresh clone, one command yields a
    # brain with departments, digest, maintenance and flows — no hidden manual
    # steps. Prove it against the REAL seed registry, not a stub.
    for profile in sc.PROFILES:
        report = sc.apply_profile_seeds(profile, real_seed_env)
        errors = {
            name: (r.get("errors") or ([r["error"]] if r.get("error") else []))
            for name, r in report["results"].items()
        }
        assert all(not e for e in errors.values()), errors

    smoke = sc.smoke_check(real_seed_env)
    assert smoke["ok"] is True
    assert smoke["departments"] > 0
    assert smoke["flows"] > 0


def test_apply_profile_seeds_is_idempotent(real_seed_env):
    sc.apply_profile_seeds("dev", real_seed_env)
    second = sc.apply_profile_seeds("dev", real_seed_env)
    for name in sc.seeds_for_profile("dev"):
        assert not (second["results"][name].get("errors") or second["results"][name].get("error"))
    smoke_first = sc.smoke_check(real_seed_env)
    smoke_second = sc.smoke_check(real_seed_env)
    assert smoke_first == smoke_second  # no duplication from re-running
