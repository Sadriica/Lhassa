# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa review``: rules, the git corpus, the hooks, and fail-open behaviour.

All identities are invented; every secret-shaped or em-dash fixture is built at
run time so the tree itself stays clean (see tests/test_no_em_dashes.py).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from lhassa.review import cli as review_cli
from lhassa.review import hooks
from lhassa.review.log import log_path
from lhassa.review.rules import ReviewConfig, check_identity, check_text, load_config

EM = chr(0x2014)
ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "scripts" / "hooks"
FAKE_GH = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # review:allow-secret
FAKE_LONG = "Zq8" + "Xv2Lm9" * 6 + "Pa7"  # review:allow-secret (32+ chars, mixed case and digits)
ASSISTANT_TRAILER = "Co-Authored-By: " + "Claude" + " <noreply@" + "anthropic.com>"


@pytest.fixture(autouse=True)
def _isolated_review(tmp_path, monkeypatch):
    d = tmp_path / "review-home"
    monkeypatch.setenv("LHASSA_REVIEW_DIR", str(d))
    monkeypatch.delenv("LHASSA_REVIEW_CONFIG", raising=False)
    monkeypatch.delenv("LHASSA_REVIEW_STRICT", raising=False)
    return d


def write_config(tmp_path, monkeypatch, **cfg):
    p = tmp_path / "review-config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("LHASSA_REVIEW_CONFIG", str(p))


def log_entries():
    p = log_path()
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def rules_of(findings):
    return {f.rule for f in findings}


# ── pure rules ───────────────────────────────────────────────────────────────
def test_em_dash_flagged_with_line_number():
    out = check_text("ok\nbad " + EM + " here\n", subject="m", cfg=ReviewConfig())
    assert [(f.rule, f.line) for f in out] == [("emdash", 2)]


def test_ai_trailers_and_phrases_in_messages_only():
    cfg = ReviewConfig()
    msg = "feat: x\n\n" + ASSISTANT_TRAILER + "\n"
    assert rules_of(check_text(msg, subject="m", cfg=cfg, kind="message")) == {"ai.trailer"}
    phrase = "Generated with [" + "Claude Code](https://claude.com/claude-code)"
    assert "ai.mention" in rules_of(check_text(phrase, subject="m", cfg=cfg, kind="message"))
    # The same words in a document body are not attribution.
    assert check_text(phrase, subject="d", cfg=cfg, kind="content") == []


def test_no_false_positives_on_ordinary_messages():
    cfg = ReviewConfig()
    for msg in (
        "llm: add a Claude provider and a Gemini router",
        "docs: explain how an AI assistant uses the brain",
        "fix: handle sha " + "a" * 40 + " and " + "b" * 64,
        "tests: test_something_with_a_very_long_snake_case_identifier_name_2",
        "Co-Authored-By: Jane Roe <jane@example.org>",
        "feat: use pk-less keys and the task_queue",
    ):
        assert check_text(msg, subject="m", cfg=cfg, kind="message") == [], msg


def test_secrets_prefixed_critical_long_token_warn_and_values_never_echoed():
    out = check_text(f"token = {FAKE_GH}\nother = {FAKE_LONG}\n", subject="s", cfg=ReviewConfig())
    by_rule = {f.rule: f for f in out}
    assert by_rule["secret.prefixed"].severity == "critical"
    assert by_rule["secret.long-token"].severity == "warn"
    assert all(FAKE_GH not in f.detail and FAKE_LONG not in f.detail for f in out)
    cfg = ReviewConfig(secret_ignore=(r"^ghp_",))
    assert "secret.prefixed" not in rules_of(check_text(FAKE_GH, subject="s", cfg=cfg))


def test_the_allow_secret_marker_skips_only_secrets_on_that_line():
    cfg = ReviewConfig()
    line = f'x = "{FAKE_GH}"  # review:allow-secret'
    assert check_text(line, subject="s", cfg=cfg) == []
    other = f'x = "{FAKE_GH}"'
    assert check_text(other, subject="s", cfg=cfg)  # without the marker it still fires
    dashed = line + EM
    assert [f.rule for f in check_text(dashed, subject="s", cfg=cfg)] == ["emdash"]


def test_private_key_header():
    text = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
    assert "secret.private-key" in rules_of(check_text(text, subject="s", cfg=ReviewConfig()))


def test_forbidden_names_come_from_config_only():
    assert check_text("hello Zorbax", subject="s", cfg=ReviewConfig()) == []
    cfg = ReviewConfig(forbidden_names=("Zorbax",))
    assert rules_of(check_text("hello zorbax!", subject="s", cfg=cfg)) == {"name.forbidden"}
    assert check_text("Zorbaxian", subject="s", cfg=cfg) == []  # whole word only


def test_identity_rules():
    cfg = ReviewConfig(allowed_authors=("owner@example.org",))
    assert check_identity("author", "Owner", "owner@example.org", cfg, "c") == []
    assert rules_of(check_identity("author", "X", "x@example.org", cfg, "c")) == {"author.unexpected"}
    cfg = ReviewConfig(allowed_authors=cfg.allowed_authors, unwanted_emails=("noreply@bots.*",))
    assert rules_of(check_identity("committer", "B", "noreply@bots.example", cfg, "c")) == {
        "author.unwanted-email", "author.unexpected"}
    # GitHub's private-email form is not in the default unwanted set.
    for ok in ("1+o@users.noreply.github.com", "noreply@github.com"):
        assert check_identity("committer", "O", ok, ReviewConfig(), "c") == []
    assert rules_of(check_identity("author", "C", ASSISTANT_TRAILER.split("<")[1][:-1],
                                   ReviewConfig(), "c")) == {"author.unwanted-email"}


def test_config_broken_file_degrades_to_defaults(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    monkeypatch.setenv("LHASSA_REVIEW_CONFIG", str(p))
    cfg = load_config()
    assert cfg.forbidden_names == () and cfg.notes


# ── the git corpus ───────────────────────────────────────────────────────────
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Owner", "GIT_AUTHOR_EMAIL": "owner@example.org",
    "GIT_COMMITTER_NAME": "Owner", "GIT_COMMITTER_EMAIL": "owner@example.org",
}


def sh(cwd, *cmd, env=None, input=None, check=True):
    e = {**os.environ, **GIT_ENV, **(env or {})}
    return subprocess.run(cmd, cwd=cwd, env=e, input=input, capture_output=True, text=True,
                          check=check)


def commit(repo, files, message, env=None):
    for name, body in files.items():
        (repo / name).write_text(body, encoding="utf-8")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-F", "-", input=message, env=env)
    return sh(repo, "git", "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def corpus(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sh(repo, "git", "init", "-q", "-b", "main")
    base = commit(repo, {"a.txt": "hello\n"}, "init: start")
    good1 = commit(repo, {"a.txt": "hello\nworld " + "a" * 40 + "\n"}, "feat: plain change")
    good2 = commit(repo, {"b.md": "# Notes\nUse a Claude provider.\n"},
                   "docs: mention the provider\n\nCo-Authored-By: Jane Roe <jane@example.org>")
    bad_msg = commit(repo, {"c.txt": "x\n"}, "fix: this " + EM + " that\n\n" + ASSISTANT_TRAILER)
    bad_secret = commit(repo, {"d.py": "x = 1\nKEY = '" + FAKE_GH + "'\n"}, "chore: add config")
    bad_dash = commit(repo, {"e.md": "line one\nline " + EM + " two\n"}, "docs: e")
    bad_name = commit(repo, {"f.txt": "ping Zorbax\n"}, "docs: f")
    bad_author = commit(repo, {"g.txt": "g\n"}, "feat: g", env={
        "GIT_AUTHOR_EMAIL": "stranger@example.net", "GIT_AUTHOR_NAME": "Stranger"})
    return repo, dict(base=base, good1=good1, good2=good2, bad_msg=bad_msg,
                      bad_secret=bad_secret, bad_dash=bad_dash, bad_name=bad_name,
                      bad_author=bad_author)


def test_corpus_detections_and_no_false_positives(corpus, tmp_path, monkeypatch):
    repo, sha = corpus
    write_config(tmp_path, monkeypatch, allowed_authors=["owner@example.org"],
                 forbidden_names=["Zorbax"])
    from lhassa.review import gitscan
    findings = gitscan.review_commits(str(repo), [f"{sha['base']}..HEAD"], load_config())
    by_commit: dict[str, set[str]] = {}
    for f in findings:
        by_commit.setdefault(f.subject, set()).add(f.rule)
    k = lambda name: sha[name][:10]  # noqa: E731
    assert k("good1") not in by_commit and k("good2") not in by_commit
    assert by_commit[k("bad_msg")] == {"emdash", "ai.trailer"}
    assert by_commit[k("bad_secret")] == {"secret.prefixed"}
    assert by_commit[k("bad_dash")] == {"emdash"}
    assert by_commit[k("bad_name")] == {"name.forbidden"}
    assert by_commit[k("bad_author")] == {"author.unexpected"}
    dash = next(f for f in findings if f.subject == k("bad_dash") and f.file == "e.md")
    assert dash.line == 2
    secret = next(f for f in findings if f.rule == "secret.prefixed")
    assert (secret.file, secret.line) == ("d.py", 2)


def test_cli_commit_range_logs_and_strict_exit(corpus, capsys):
    repo, sha = corpus
    assert review_cli.main(["commit-range", f"{sha['base']}..HEAD", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "secret.prefixed" in out and FAKE_GH not in out
    assert review_cli.main(["commit-range", f"{sha['base']}..HEAD", "--repo", str(repo),
                            "--strict"]) == 1
    assert review_cli.main(["commit-range", f"{sha['good1']}..{sha['good2']}", "--repo",
                            str(repo), "--strict"]) == 0
    assert review_cli.main(["commit-range", "nope..nada", "--repo", str(repo)]) == 2
    assert [e["event"] for e in log_entries()].count("commit-range") == 3


def test_cli_message_and_text(tmp_path, capsys):
    msg = tmp_path / "MSG"
    msg.write_text("feat: ok\n# a comment " + EM + " is ignored\n\n" + ASSISTANT_TRAILER + "\n")
    assert review_cli.main(["message", str(msg), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["rule"] for d in data] == ["ai.trailer"]
    txt = tmp_path / "t.md"
    txt.write_text("fine\nnot " + EM + " fine\n")
    assert review_cli.main(["text", str(txt), "--strict", "--fail-on", "critical"]) == 0
    assert review_cli.main(["text", str(txt), "--strict"]) == 1


def test_log_is_append_only_0600_and_redacts_urls(tmp_path):
    hooks.pre_push_hook("origin", "https://user:secret" + "tok@example.org/r.git",
                        f"refs/heads/x {'1' * 40} refs/heads/x {'0' * 40}\n", str(tmp_path))
    hooks.pre_push_hook("origin", "", "", str(tmp_path))
    p = log_path()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    rows = log_entries()
    assert len(rows) == 2 and "secrettok" not in p.read_text()
    assert rows[0]["url"] == "https://example.org/r.git"


# ── PreToolUse adapter ───────────────────────────────────────────────────────
def pre(payload, capsys):
    rc = hooks.pretooluse_main(payload if isinstance(payload, str) else json.dumps(payload))
    out = capsys.readouterr().out.strip()
    return rc, (json.loads(out) if out else None)


def test_pretooluse_warns_without_granting_permission(corpus, capsys):
    repo, _ = corpus
    cmd = "git commit -m 'fix: a " + EM + " b'"
    rc, out = pre({"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": cmd}}, capsys)
    assert rc == 0
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and "emdash" in spec["additionalContext"]
    assert "permissionDecision" not in spec  # "allow" would skip the user's prompt
    assert log_entries()[-1]["outcome"] == "warn"


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit -m 'fix: a " + EM + " b'",
        "git commit -am 'fix: a " + EM + " b'",
        'git commit --message="fix: a ' + EM + ' b"',
        "git commit -m \"$(cat <<'EOF'\nfix: a " + EM + " b\nEOF\n)\"",
    ],
)
def test_the_message_forms_of_a_commit_command_are_scanned(corpus, capsys, cmd):
    repo, _ = corpus
    rc, out = pre({"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": cmd}}, capsys)
    assert rc == 0 and "emdash" in out["hookSpecificOutput"]["additionalContext"]


def test_a_dash_elsewhere_in_a_commit_command_is_not_a_finding(corpus, capsys):
    """The em dash is in a grep pattern, not in the commit message."""
    repo, _ = corpus
    cmd = "grep -rn '" + EM + "' docs && git commit -m 'docs: tidy'"
    assert pre({"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": cmd}}, capsys) == (0, None)


def test_pretooluse_ignores_unrelated_calls_and_clean_commits(corpus, capsys):
    repo, _ = corpus
    for payload in (
        {"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": "ls -la"}},
        {"tool_name": "Bash", "cwd": str(repo),
         "tool_input": {"command": "git commit -m 'feat: tidy things'"}},
        {"tool_name": "Read", "tool_input": {"file_path": "/x"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/x.md", "content": "plain"}},
    ):
        assert pre(payload, capsys) == (0, None)


def test_pretooluse_write_secret_warns_and_block_on_denies(tmp_path, monkeypatch, capsys):
    payload = {"tool_name": "Write", "tool_input": {"file_path": "/x/cfg.py",
                                                    "content": f"K='{FAKE_GH}'"}}
    rc, out = pre(payload, capsys)
    assert rc == 0 and "permissionDecision" not in out["hookSpecificOutput"]
    write_config(tmp_path, monkeypatch, block_on=["secret.prefixed"])
    rc, out = pre(payload, capsys)
    assert rc == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert FAKE_GH not in json.dumps(out)
    assert log_entries()[-1]["outcome"] == "deny"


def test_pretooluse_edit_and_multiedit_content(capsys):
    edit = {"tool_name": "Edit", "tool_input": {"file_path": "/x.md", "old_string": "a",
                                                "new_string": "b " + EM + " c"}}
    assert pre(edit, capsys)[1] is not None
    multi = {"tool_name": "MultiEdit", "tool_input": {"file_path": "/x.md", "edits": [
        {"old_string": "a", "new_string": "ok"}, {"old_string": "b", "new_string": EM}]}}
    assert pre(multi, capsys)[1] is not None


@pytest.mark.parametrize("bad", ["", "{not json", "[]", "null", '{"tool_name": "Bash", "tool_input": 3}'])
def test_pretooluse_fails_open_on_broken_input(bad, capsys):
    assert pre(bad, capsys) == (0, None)


def test_pretooluse_fails_open_on_internal_error(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(hooks, "check_text", boom)
    payload = {"tool_name": "Write", "tool_input": {"file_path": "/x", "content": "y"}}
    assert pre(payload, capsys) == (0, None)
    last = log_entries()[-1]
    assert last["outcome"] == "allow-failopen" and last["error"] == "RuntimeError"


def test_pretooluse_script_fails_open_on_json_garbage(tmp_path):
    env = {**os.environ, "LHASSA_REVIEW_DIR": str(tmp_path / "rv")}
    r = subprocess.run([str(HOOKS / "claude-pretooluse")], input="{{{{", env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == ""
    r = subprocess.run([str(HOOKS / "claude-pretooluse")], input="{}",
                       env={**env, "LHASSA_REVIEW_CMD": "/nonexistent/binary"},
                       capture_output=True, text=True)
    assert r.returncode == 0


# ── the git hook scripts, through core.hooksPath (repo-local, never global) ──
def hooked_repo(tmp_path, extra_env=None):
    repo = tmp_path / "hr"
    repo.mkdir()
    sh(repo, "git", "init", "-q", "-b", "main")
    sh(repo, "git", "config", "core.hooksPath", str(HOOKS))
    marker = tmp_path / "repo-hook-ran"
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text(f"#!/bin/sh\necho ran >> {marker}\nexit 0\n")
    hook.chmod(0o755)
    pc = repo / ".git" / "hooks" / "pre-commit"
    pc.write_text(f"#!/bin/sh\necho pc >> {marker}\nexit 0\n")
    pc.chmod(0o755)
    return repo, marker


@pytest.mark.skipif(sys.platform == "win32", reason="posix hooks")
def test_commit_msg_hook_warns_logs_and_delegates_to_repo_hook(tmp_path):
    repo, marker = hooked_repo(tmp_path)
    env = {"LHASSA_REVIEW_DIR": str(tmp_path / "rv")}
    (repo / "f").write_text("x")
    sh(repo, "git", "add", "f")
    r = sh(repo, "git", "commit", "-q", "-m", "fix: a " + EM + " b", env=env)
    assert r.returncode == 0 and "emdash" in r.stdout + r.stderr
    assert marker.read_text().split() == ["pc", "ran"]  # pass-through and delegate both ran
    rows = [json.loads(x) for x in (tmp_path / "rv" / "review.jsonl").read_text().splitlines()]
    assert rows[-1]["source"] == "git-hook" and rows[-1]["event"] == "commit-msg"


@pytest.mark.skipif(sys.platform == "win32", reason="posix hooks")
def test_commit_msg_hook_strict_blocks_only_when_asked(tmp_path):
    repo, _ = hooked_repo(tmp_path)
    env = {"LHASSA_REVIEW_DIR": str(tmp_path / "rv"), "LHASSA_REVIEW_STRICT": "1"}
    (repo / "f").write_text("x")
    sh(repo, "git", "add", "f")
    bad = sh(repo, "git", "commit", "-q", "-m", "fix: a " + EM + " b", env=env, check=False)
    assert bad.returncode != 0
    good = sh(repo, "git", "commit", "-q", "-m", "fix: plain", env=env, check=False)
    assert good.returncode == 0


@pytest.mark.skipif(sys.platform == "win32", reason="posix hooks")
def test_pre_push_hook_logs_the_push_and_never_blocks(tmp_path):
    repo, _ = hooked_repo(tmp_path)
    remote = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "-q", "--bare", str(remote))
    sh(repo, "git", "remote", "add", "origin", str(remote))
    env = {"LHASSA_REVIEW_DIR": str(tmp_path / "rv")}
    (repo / "f").write_text("x")
    sh(repo, "git", "add", "f")
    sh(repo, "git", "commit", "-q", "-m", "fix: a " + EM + " b", env=env)
    r = sh(repo, "git", "push", "-q", "origin", "main", env=env)
    assert r.returncode == 0
    rows = [json.loads(x) for x in (tmp_path / "rv" / "review.jsonl").read_text().splitlines()]
    push = [x for x in rows if x["event"] == "pre-push"][-1]
    assert push["refs"][0]["new_branch"] is True and push["counts"]["warn"] >= 1
    assert sh(remote, "git", "rev-parse", "main").returncode == 0
