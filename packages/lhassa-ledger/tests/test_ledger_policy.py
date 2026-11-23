# SPDX-License-Identifier: AGPL-3.0-or-later
"""The never-keep policy: every default rule, additivity, and fail-closed."""

from __future__ import annotations

import json

import pytest

from agent_ledger.event import KIND_TOOL_CALL, AgentEvent
from agent_ledger.policy import ENV_RULES_FILE, WITHHELD_TARGET, Policy
from agent_ledger.store import Store


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def policy(home):
    return Policy(home=home, use_env=False)


def _event(target="", **detail):
    return AgentEvent(host="claude-code", kind=KIND_TOOL_CALL, tool="Bash", target=target, detail=detail)


# -- the default rules -------------------------------------------------------

@pytest.mark.parametrize(
    "rel",
    [
        ".ssh/id_ed25519",
        ".ssh",
        ".gnupg/pubring.kbx",
        ".lhassa/brain.db",
        ".password-store/mail/me.gpg",
        ".local/share/gopass/stores/root/x",
        ".config/keepassxc/keepassxc.ini",
        ".config/Bitwarden/data.json",
        ".config/1Password/settings",
        ".local/share/keyrings/login.keyring",
    ],
)
def test_default_directory_rules_withhold(policy, home, rel):
    assert policy.evaluate(_event(str(home / rel))) is not None


def test_tilde_is_expanded_against_the_given_home(policy):
    assert policy.evaluate(_event("~/.ssh/config")) == "path:~/.ssh"


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production", "vault.kdbx", "x/y/.env"])
def test_default_name_rules_withhold_anywhere(policy, name):
    assert policy.evaluate(_event(f"/srv/app/{name}")) is not None


@pytest.mark.parametrize(
    "path",
    [
        "/work/lhassa/personal/notes.md",
        "/work/products/lhassa/personal",
    ],
)
def test_lhassa_personal_component_rule(policy, path):
    assert policy.evaluate(_event(path)) == "path:lhassa/personal"


@pytest.mark.parametrize(
    "path",
    ["/work/personal/notes.md", "/work/lhassa/app/main.py", "/home/x/.sshx/key", "/srv/environment/file", "/srv/.envrc2"],
)
def test_near_misses_are_kept(policy, path):
    assert policy.evaluate(_event(path)) is None


def test_symlinked_lhassa_home_is_protected_by_its_realpath(tmp_path):
    h = tmp_path / "h"
    private = tmp_path / "private-data"
    private.mkdir()
    h.mkdir()
    (h / ".lhassa").symlink_to(private)
    p = Policy(home=h, use_env=False)
    assert p.evaluate(_event(str(private / "brain.db"))) == "path:~/.lhassa"
    assert p.evaluate(_event(str(h / ".lhassa" / "brain.db"))) == "path:~/.lhassa"


def test_dotdot_traversal_cannot_dodge_a_rule(policy, home):
    assert policy.evaluate(_event(str(home / "work" / ".." / ".ssh" / "id_rsa"))) is not None


def test_a_symlink_into_a_protected_dir_is_caught(policy, home, tmp_path):
    (home / ".ssh").mkdir()
    link = tmp_path / "innocent"
    link.symlink_to(home / ".ssh")
    assert policy.evaluate(_event(str(link / "id_rsa"))) is not None


def test_paths_inside_shell_commands_are_found(policy, home):
    assert policy.evaluate(_event(f"cat {home}/.ssh/id_rsa | head")) is not None
    assert policy.evaluate(_event("scp -i ~/.ssh/id_ed25519 a b:c")) is not None
    assert policy.evaluate(_event('grep x "$HOME/.gnupg/secring"')) is not None
    assert policy.evaluate(_event("docker run -v ~/.ssh:/root/.ssh img")) is not None
    assert policy.evaluate(_event("source .env")) is not None
    assert policy.evaluate(_event("ls /tmp/ok")) is None


def test_paths_in_detail_are_checked_at_any_depth(policy, home):
    nested = {"a": [{"b": {"file": str(home / ".gnupg" / "x")}}]}
    assert policy.evaluate(_event("/tmp/ok", **nested)) is not None
    assert policy.evaluate(_event("/tmp/ok", cwd="/tmp", other=["fine", 3, None])) is None


def test_relative_paths_resolve_against_cwd(policy, home):
    assert policy.evaluate(_event("id_rsa", cwd=str(home / ".ssh"))) is not None
    assert policy.evaluate(_event("../.ssh/id_rsa", cwd=str(home / "work"))) is not None
    assert policy.evaluate(_event("notes.txt", cwd="/tmp")) is None


# -- the store applies it ----------------------------------------------------

def test_store_withholds_target_and_detail(tmp_path, home):
    store = Store(tmp_path / "l.db", policy=Policy(home=home, use_env=False))
    ev = _event(str(home / ".ssh" / "id_rsa"), cwd="/tmp", extra="x")
    assert store.record(ev)
    row = store.query("SELECT target, detail, tool, host FROM events")[0]
    assert row["target"] == WITHHELD_TARGET
    assert json.loads(row["detail"]) == {"withheld": True, "reason": "path:~/.ssh"}
    assert (row["tool"], row["host"]) == ("Bash", "claude-code")  # that it happened survives


def test_store_keeps_ordinary_events_whole(tmp_path, home):
    store = Store(tmp_path / "l.db", policy=Policy(home=home, use_env=False))
    store.record(_event("/tmp/ok.py", cwd="/tmp"))
    row = store.query("SELECT target, detail FROM events")[0]
    assert row["target"] == "/tmp/ok.py"
    assert json.loads(row["detail"]) == {"cwd": "/tmp"}


def test_a_failing_policy_withholds(tmp_path):
    class Broken:
        def evaluate(self, event):
            raise RuntimeError("boom")

    store = Store(tmp_path / "l.db", policy=Broken())
    assert store.record(_event("/tmp/ok.py", cwd="/tmp"))
    row = store.query("SELECT target, detail FROM events")[0]
    assert row["target"] == WITHHELD_TARGET
    assert json.loads(row["detail"]) == {"withheld": True, "reason": "policy-error"}


def test_a_pathological_detail_fails_closed(tmp_path, home):
    cyclic: dict = {}
    cyclic["self"] = cyclic
    store = Store(tmp_path / "l.db", policy=Policy(home=home, use_env=False))
    store.record(_event("/tmp/ok", **cyclic))
    row = store.query("SELECT target FROM events")[0]
    assert row["target"] == WITHHELD_TARGET


# -- user file: additive, never subtractive ----------------------------------

def test_user_rules_add_to_the_defaults(tmp_path, home):
    rules = tmp_path / "never.txt"
    rules.write_text(
        "# mine\npath: ~/work/secrets\nname: *.pem\n~/vault\n*.key\nhost: orca\nsession: s-9\n"
    )
    p = Policy(home=home, rules_file=rules, use_env=False)
    assert p.evaluate(_event(str(home / "work/secrets/a"))) is not None
    assert p.evaluate(_event("/x/cert.pem")) is not None
    assert p.evaluate(_event(str(home / "vault/a"))) is not None
    assert p.evaluate(_event("/x/a.key")) is not None
    assert p.evaluate(_event("/tmp/ok", )) is None
    # factory rules still on
    assert p.evaluate(_event(str(home / ".ssh/id"))) is not None
    # do-not-keep sources
    assert p.evaluate(AgentEvent(host="orca", kind=KIND_TOOL_CALL, target="/tmp/ok")) == "host:orca"
    assert p.evaluate(AgentEvent(host="h", kind=KIND_TOOL_CALL, session="s-9")) == "session:s-9"


def test_a_user_file_cannot_remove_factory_rules(tmp_path, home):
    rules = tmp_path / "never.txt"
    rules.write_text("allow: ~/.ssh\n!~/.ssh\npath:\n")
    p = Policy(home=home, rules_file=rules, use_env=False)
    assert p.evaluate(_event(str(home / ".ssh/id"))) is not None


def test_user_glob_path_rule(tmp_path, home):
    rules = tmp_path / "never.txt"
    rules.write_text("path: ~/proj/*/secrets\n")
    p = Policy(home=home, rules_file=rules, use_env=False)
    assert p.evaluate(_event(str(home / "proj/a/secrets/k"))) is not None
    assert p.evaluate(_event(str(home / "proj/a/public/k"))) is None


def test_rules_file_comes_from_the_environment(tmp_path, home, monkeypatch):
    rules = tmp_path / "never.txt"
    rules.write_text("name: *.pem\n")
    monkeypatch.setenv(ENV_RULES_FILE, str(rules))
    assert Policy(home=home).evaluate(_event("/x/a.pem")) is not None


def test_a_missing_rules_file_means_no_extra_rules(tmp_path, home):
    p = Policy(home=home, rules_file=tmp_path / "absent.txt", use_env=False)
    assert p.evaluate(_event("/tmp/ok")) is None


def test_an_unreadable_rules_file_withholds_everything(tmp_path, home):
    bad = tmp_path / "dir-not-file"
    bad.mkdir()  # reading a directory raises
    p = Policy(home=home, rules_file=bad, use_env=False)
    assert p.evaluate(_event("/tmp/ok")) == "user-rules-unreadable"


def test_do_not_keep_host_stores_a_bare_trace(tmp_path, home):
    rules = tmp_path / "never.txt"
    rules.write_text("host: claude-code\n")
    store = Store(tmp_path / "l.db", policy=Policy(home=home, rules_file=rules, use_env=False))
    store.record(_event("/tmp/ok.py", cwd="/tmp"))
    row = store.query("SELECT target, detail FROM events")[0]
    assert row["target"] == WITHHELD_TARGET
    assert json.loads(row["detail"])["reason"] == "host:claude-code"


@pytest.mark.parametrize(
    "path",
    [
        "/a/lhassa/app/lhassa/personal/secretary.py",
        "/a/packages/lhassa-x/src/lhassa/personal/agenda.py",
    ],
)
def test_the_source_package_named_personal_is_code_not_data(policy, path):
    assert policy.evaluate(_event(path)) is None
