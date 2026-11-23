# SPDX-License-Identifier: AGPL-3.0-or-later
"""The never-keep policy: fail-closed, built-in rules cannot be removed."""

import os

import pytest

from lhassa.record import never_keep
from lhassa.record.never_keep import first_withheld, withheld_rule


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LHASSA_NEVER_KEEP_FILE", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    "rel",
    [
        ".ssh/id_ed25519",
        ".gnupg/pubring.kbx",
        ".lhassa/brain.db",
        ".password-store/mail.gpg",
        ".config/keepassxc/keepassxc.ini",
        "work/project/.env",
        "work/project/.env.production",
        "vault/main.kdbx",
        "Code/x/products/lhassa/personal/audit.json",
    ],
)
def test_sensitive_paths_are_withheld(home, rel):
    assert withheld_rule(str(home / rel)) is not None


@pytest.mark.parametrize("rel", ["Code/app/src/main.py", "Downloads/report.pdf", "notes/personal.md"])
def test_ordinary_paths_are_kept(home, rel):
    assert withheld_rule(str(home / rel)) is None


def test_tilde_and_relative_forms_are_expanded(home):
    assert withheld_rule("~/.ssh/config") is not None


def test_a_symlink_into_a_private_folder_is_withheld(home):
    private = home / "elsewhere" / "lhassa" / "personal"
    private.mkdir(parents=True)
    (home / "link").symlink_to(private)
    assert withheld_rule(str(home / "link" / "audit.json")) is not None


def test_dot_lhassa_symlink_target_is_withheld(home):
    data = home / "vault"
    data.mkdir()
    (home / ".lhassa").symlink_to(data)
    assert withheld_rule(str(data / "brain.db")) is not None


def test_empty_and_non_strings_are_not_paths(home):
    assert withheld_rule("") is None
    assert first_withheld([None, 3, ""]) is None


def test_evaluation_failure_withholds(home, monkeypatch):
    def boom(raw):
        raise OSError("disk gone")

    monkeypatch.setattr(never_keep, "_resolved", boom)
    assert withheld_rule("/anything") == "unevaluable"


def test_user_rules_add_but_never_remove(home, monkeypatch):
    rules = home / "rules.txt"
    rules.write_text("# comment\n~/secrets-extra\n")
    monkeypatch.setenv("LHASSA_NEVER_KEEP_FILE", str(rules))
    assert withheld_rule(str(home / "secrets-extra" / "a")) is not None
    assert withheld_rule(str(home / ".ssh" / "id")) is not None  # built-ins stay
    rules.write_text("")
    assert withheld_rule(str(home / ".ssh" / "id")) is not None
    assert os.path.exists(rules)


def test_a_path_inside_free_text_is_found(home):
    from lhassa.record.never_keep import withheld_rule_in_text

    assert withheld_rule_in_text(f"cat {home}/.ssh/id_rsa | head") is not None
    assert withheld_rule_in_text("source ~/.gnupg/gpg.conf") is not None
    assert withheld_rule_in_text("export $(cat .env)") is not None
    assert withheld_rule_in_text("run the tests in src/app/main.py") is None
    assert withheld_rule_in_text("") is None


def test_the_source_package_named_personal_is_code_not_data(home):
    assert withheld_rule(str(home / "Code/lhassa/app/lhassa/personal/agenda.py")) is None
    assert withheld_rule(str(home / "Code/x/packages/p/src/lhassa/personal/a.py")) is None
    assert withheld_rule(str(home / "Code/lhassa/personal/audit.json")) is not None
