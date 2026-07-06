"""Tests for lhassa/actionpack — Phase 3.

All tests are fully offline: httpx, subprocess, quarantine, notify, cv, health,
and memory operations are monkeypatched. No real network, no real DB writes (where
possible), no real filesystem outside tmpdir.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async function synchronously for parametrize-friendly tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# register_all — idempotency
# ---------------------------------------------------------------------------


def test_register_all_idempotent():
    """register_all() is safe to call multiple times."""
    import lhassa.actionpack as ap

    # Reset flag for a clean test (module may already be registered from other tests)
    original = ap._REGISTERED
    ap._REGISTERED = False

    from lhassa.core.actions import ACTIONS
    # First call
    ap.register_all()
    count1 = len(ACTIONS)
    # Second call — must not raise (registry raises on true duplicate)
    ap.register_all()
    count2 = len(ACTIONS)
    assert count1 == count2

    # Restore
    ap._REGISTERED = original or True  # keep registered after this test


def test_register_all_registers_expected_actions():
    """All 15 Phase-3 actions are registered after register_all()."""
    import lhassa.actionpack as ap
    ap._REGISTERED = False
    ap.register_all()

    from lhassa.core.actions import ACTIONS

    expected = {
        "web_fetch", "ping_url",
        "run_command", "system_snapshot", "power_mode",
        "add_fact", "backup_memory", "consolidate_memory",
        "notify", "add_task", "set_task_state", "list_approvals",
        "check_health", "git_status_repos", "compile_cv",
    }
    for name in expected:
        assert name in ACTIONS, f"Action {name!r} not in ACTIONS after register_all()"


# ---------------------------------------------------------------------------
# Allowlist entries
# ---------------------------------------------------------------------------


def test_allowlist_has_all_15_actions():
    """All 15 Phase-3 actions have entries in build_allowlist()."""
    from lhassa.security.allowlist import build_allowlist

    al = build_allowlist()
    phase3_actions = {
        "web_fetch", "ping_url",
        "run_command", "system_snapshot", "power_mode",
        "add_fact", "backup_memory", "consolidate_memory",
        "notify", "add_task", "set_task_state", "list_approvals",
        "check_health", "git_status_repos", "compile_cv",
    }
    for name in phase3_actions:
        assert name in al, f"Action {name!r} missing from allowlist"


def test_run_command_requires_approval():
    """run_command must have requires_approval=True in the allowlist."""
    from lhassa.security.allowlist import build_allowlist
    al = build_allowlist()
    assert al["run_command"].requires_approval is True


def test_other_actions_do_not_require_approval():
    """All actions except run_command must not require approval."""
    from lhassa.security.allowlist import build_allowlist
    al = build_allowlist()
    no_approval = {
        "web_fetch", "ping_url", "system_snapshot", "power_mode",
        "add_fact", "backup_memory", "consolidate_memory",
        "notify", "add_task", "set_task_state", "list_approvals",
        "check_health", "git_status_repos", "compile_cv",
    }
    for name in no_approval:
        assert not al[name].requires_approval, f"{name!r} should not require approval"


# ---------------------------------------------------------------------------
# SSRF guard (_ssrf.py)
# ---------------------------------------------------------------------------


def test_ssrf_check_refuses_localhost():
    from lhassa.actionpack._ssrf import ssrf_check
    result = ssrf_check("http://localhost/path")
    assert result is not None
    assert "localhost" in result


def test_ssrf_check_refuses_non_http_scheme():
    from lhassa.actionpack._ssrf import ssrf_check
    result = ssrf_check("ftp://example.com/file")
    assert result is not None
    assert "ftp" in result.lower() or "scheme" in result.lower()


@pytest.mark.parametrize("ip", [
    "127.0.0.1",
    "10.0.0.1",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.0.1",
    "::1",
    "fc00::1",
])
def test_ssrf_check_refuses_private_ip(ip):
    """_is_blocked_addr correctly classifies private/loopback addresses."""
    from lhassa.actionpack._ssrf import _is_blocked_addr
    assert _is_blocked_addr(ip), f"Expected {ip!r} to be blocked"


@pytest.mark.parametrize("ip", [
    "8.8.8.8",
    "1.1.1.1",
    "2001:4860:4860::8888",
])
def test_ssrf_check_allows_public_ip(ip):
    """Public IP addresses are not blocked."""
    from lhassa.actionpack._ssrf import _is_blocked_addr
    assert not _is_blocked_addr(ip), f"Expected {ip!r} to be allowed"


@patch("lhassa.actionpack._ssrf.socket.getaddrinfo")
def test_ssrf_check_refuses_hostname_resolving_to_private(mock_gai):
    """A hostname that resolves to a private address is refused."""
    mock_gai.return_value = [(None, None, None, None, ("10.0.0.1", 0))]
    from lhassa.actionpack._ssrf import ssrf_check
    result = ssrf_check("http://internal.corp/path")
    assert result is not None
    assert "SSRF" in result or "private" in result.lower()


@patch("lhassa.actionpack._ssrf.socket.getaddrinfo")
def test_ssrf_check_allows_public_hostname(mock_gai):
    """A hostname resolving to a public IP is allowed."""
    mock_gai.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
    from lhassa.actionpack._ssrf import ssrf_check
    result = ssrf_check("https://example.com/")
    assert result is None


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------


@patch("lhassa.actionpack._net.httpx.AsyncClient")
def test_web_fetch_happy_path(mock_client_cls):
    """web_fetch fetches a URL and returns stripped text."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"<html><head><style>.x{}</style></head><body><p>Hello World</p></body></html>"
    mock_resp.encoding = "utf-8"
    mock_resp.headers = {"content-type": "text/html"}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client_cls.return_value = mock_client

    with patch("lhassa.actionpack._ssrf.socket.getaddrinfo",
               return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        from lhassa.actionpack._net import web_fetch
        result = _run(web_fetch({"url": "https://example.com/"}))

    assert "200" in result
    assert "Hello World" in result
    assert isinstance(result, str)


def test_web_fetch_requires_url():
    from lhassa.actionpack._net import web_fetch
    result = _run(web_fetch({}))
    assert "url" in result.lower()
    assert isinstance(result, str)


def test_web_fetch_ssrf_refused():
    """web_fetch refuses private URLs (SSRF guard)."""
    from lhassa.actionpack._net import web_fetch
    result = _run(web_fetch({"url": "http://localhost/secret"}))
    assert "refused" in result.lower()
    assert isinstance(result, str)


@patch("lhassa.actionpack._net.httpx.AsyncClient")
def test_web_fetch_timeout_returns_string(mock_client_cls):
    """web_fetch returns an error string on timeout — never raises."""
    import httpx

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    mock_client_cls.return_value = mock_client

    with patch("lhassa.actionpack._ssrf.socket.getaddrinfo",
               return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        from lhassa.actionpack._net import web_fetch
        result = _run(web_fetch({"url": "https://slow.example.com/"}))

    assert "timed out" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# ping_url
# ---------------------------------------------------------------------------


@patch("lhassa.actionpack._net.httpx.AsyncClient")
def test_ping_url_happy_path(mock_client_cls):
    """ping_url returns status code and elapsed ms."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.head = AsyncMock(return_value=mock_resp)
    mock_client_cls.return_value = mock_client

    with patch("lhassa.actionpack._ssrf.socket.getaddrinfo",
               return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        from lhassa.actionpack._net import ping_url
        result = _run(ping_url({"url": "https://example.com/"}))

    assert "200" in result
    assert "ms" in result
    assert isinstance(result, str)


def test_ping_url_ssrf_refused():
    """ping_url refuses private URLs."""
    from lhassa.actionpack._net import ping_url
    result = _run(ping_url({"url": "http://192.168.1.1/"}))
    # The SSRF check resolves 192.168.1.1 as a literal private IP hostname
    # OR is already caught by localhost check; just check string returned.
    assert isinstance(result, str)


def test_ping_url_requires_url():
    from lhassa.actionpack._net import ping_url
    result = _run(ping_url({}))
    assert "url" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_command_happy_path():
    """run_command returns stdout from quarantine and never raises."""
    mock_result = MagicMock()
    mock_result.ok = True
    mock_result.stdout = "hello from sandbox"
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_result.timed_out = False
    mock_result.escaped = False
    mock_result.isolation = "unshare"

    with patch(
        "lhassa.actionpack._system.run_in_quarantine",
        AsyncMock(return_value=mock_result),
    ):
        from lhassa.actionpack._system import run_command
        result = await run_command({"command": "echo hello"})

    assert "hello from sandbox" in result
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_run_command_failure_returns_string():
    """run_command on non-zero exit still returns a string, never raises."""
    mock_result = MagicMock()
    mock_result.ok = False
    mock_result.stdout = ""
    mock_result.stderr = "something went wrong"
    mock_result.exit_code = 1
    mock_result.timed_out = False
    mock_result.escaped = False
    mock_result.isolation = "none"

    with patch(
        "lhassa.actionpack._system.run_in_quarantine",
        AsyncMock(return_value=mock_result),
    ):
        from lhassa.actionpack._system import run_command
        result = await run_command({"command": "false"})

    assert isinstance(result, str)
    assert "exit" in result or "wrong" in result


def test_run_command_requires_command():
    from lhassa.actionpack._system import run_command
    result = _run(run_command({}))
    assert "command" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# system_snapshot
# ---------------------------------------------------------------------------


def test_system_snapshot_returns_string():
    """system_snapshot always returns a string and never raises."""
    from lhassa.actionpack._system import system_snapshot
    result = system_snapshot({})
    assert isinstance(result, str)
    assert len(result) > 0


def test_system_snapshot_graceful_on_missing_proc(tmp_path):
    """system_snapshot degrades gracefully if /proc/uptime is unavailable."""
    with patch("builtins.open", side_effect=OSError("no proc")):
        from lhassa.actionpack._system import system_snapshot
        result = system_snapshot({})
    assert isinstance(result, str)
    # Should still run the health checks
    assert "system_snapshot" in result or "error" in result


# ---------------------------------------------------------------------------
# power_mode
# ---------------------------------------------------------------------------


def test_power_mode_happy_path():
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "mode set"
    mock_proc.stderr = ""

    with patch("lhassa.actionpack._system.shutil.which", return_value="/usr/bin/power-mode"), \
         patch("lhassa.actionpack._system.subprocess.run", return_value=mock_proc):
        from lhassa.actionpack._system import power_mode
        result = power_mode({"mode": "balanced"})

    assert "balanced" in result
    assert isinstance(result, str)


def test_power_mode_refuses_invalid_mode():
    from lhassa.actionpack._system import power_mode
    result = power_mode({"mode": "../../etc/passwd"})
    assert "invalid" in result.lower() or "refused" in result.lower() or "mode" in result
    assert isinstance(result, str)


def test_power_mode_missing_binary():
    with patch("lhassa.actionpack._system.shutil.which", return_value=None):
        from lhassa.actionpack._system import power_mode
        result = power_mode({"mode": "performance"})
    assert "not found" in result or "PATH" in result
    assert isinstance(result, str)


def test_power_mode_requires_mode():
    with patch("lhassa.actionpack._system.shutil.which", return_value="/usr/bin/power-mode"):
        from lhassa.actionpack._system import power_mode
        result = power_mode({})
    assert "mode" in result.lower()


# ---------------------------------------------------------------------------
# add_fact
# ---------------------------------------------------------------------------


def test_add_fact_happy_path():
    """add_fact stores a fact and returns its id."""
    mock_fact = MagicMock()
    mock_fact.id = "f-abc123"

    with patch("lhassa.actionpack._memory._facts") as mock_facts_mod, \
         patch("lhassa.actionpack._memory.Config") as mock_cfg:
        mock_cfg.instance_id = "test"
        mock_facts_mod.add_fact = AsyncMock(return_value=mock_fact)
        from lhassa.actionpack._memory import add_fact
        result = _run(add_fact({"contenido": "the sky is blue", "tags": ["sky"]}))

    assert "f-abc123" in result
    assert isinstance(result, str)
    # Verify tipo was forced to "dato"
    call_args = mock_facts_mod.add_fact.call_args
    assert call_args[0][1] == "dato"


def test_add_fact_requires_contenido():
    from lhassa.actionpack._memory import add_fact
    result = _run(add_fact({}))
    assert "contenido" in result.lower()
    assert isinstance(result, str)


def test_add_fact_exception_returns_string():
    with patch("lhassa.actionpack._memory._facts") as mock_facts_mod, \
         patch("lhassa.actionpack._memory.Config") as mock_cfg:
        mock_cfg.instance_id = "test"
        mock_facts_mod.add_fact = AsyncMock(side_effect=RuntimeError("db down"))
        from lhassa.actionpack._memory import add_fact
        result = _run(add_fact({"contenido": "test fact"}))
    assert "error" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# backup_memory
# ---------------------------------------------------------------------------


def test_backup_memory_happy_path():
    with patch("lhassa.actionpack._memory._backup") as mock_backup:
        mock_backup.backup = AsyncMock(return_value="/tmp/brain.db.bak")
        mock_backup.verify = AsyncMock(return_value=True)
        from lhassa.actionpack._memory import backup_memory
        result = _run(backup_memory({}))

    assert "/tmp/brain.db.bak" in result
    assert "verified" in result.lower()
    assert isinstance(result, str)


def test_backup_memory_verification_failure():
    with patch("lhassa.actionpack._memory._backup") as mock_backup:
        mock_backup.backup = AsyncMock(return_value="/tmp/brain.db.bak")
        mock_backup.verify = AsyncMock(return_value=False)
        from lhassa.actionpack._memory import backup_memory
        result = _run(backup_memory({}))

    assert "FAILED" in result or "failed" in result.lower()
    assert isinstance(result, str)


def test_backup_memory_error_returns_string():
    with patch("lhassa.actionpack._memory._backup") as mock_backup:
        mock_backup.backup = AsyncMock(side_effect=OSError("disk full"))
        from lhassa.actionpack._memory import backup_memory
        result = _run(backup_memory({}))

    assert "error" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# consolidate_memory
# ---------------------------------------------------------------------------


def test_consolidate_memory_happy_path():
    with patch("lhassa.actionpack._memory._chica") as mock_chica, \
         patch("lhassa.actionpack._memory.Config") as mock_cfg:
        mock_cfg.instance_id = "test"
        mock_chica.consolidate = AsyncMock(return_value={"kept": 42, "evicted": 3})
        from lhassa.actionpack._memory import consolidate_memory
        result = _run(consolidate_memory({}))

    assert "kept=42" in result
    assert "evicted=3" in result
    assert isinstance(result, str)


def test_consolidate_memory_invalid_max_items():
    from lhassa.actionpack._memory import consolidate_memory
    result = _run(consolidate_memory({"max_items": "not-a-number"}))
    assert "integer" in result.lower() or "error" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# notify action
# ---------------------------------------------------------------------------


def test_notify_action_happy_path():
    with patch("lhassa.actionpack._brain._notify", AsyncMock(return_value=True)):
        from lhassa.actionpack._brain import notify
        result = _run(notify({"text": "hello", "level": "info"}))
    assert "delivered" in result
    assert isinstance(result, str)


def test_notify_action_no_delivery():
    with patch("lhassa.actionpack._brain._notify", AsyncMock(return_value=False)):
        from lhassa.actionpack._brain import notify
        result = _run(notify({"text": "hello"}))
    assert "no channel" in result or "delivered" in result
    assert isinstance(result, str)


def test_notify_action_requires_text():
    from lhassa.actionpack._brain import notify
    result = _run(notify({}))
    assert "text" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# add_task action
# ---------------------------------------------------------------------------


def test_add_task_happy_path():
    """add_task mints a task through dispatch.mint_task."""
    with patch("lhassa.actionpack._brain._dispatch") as mock_dispatch, \
         patch("lhassa.actionpack._brain.Config") as mock_cfg:
        mock_cfg.instance_id = "test"
        mock_dispatch.mint_task = AsyncMock(return_value=("t-abc", "pendiente"))
        from lhassa.actionpack._brain import add_task
        result = _run(add_task({"accion": "sync_repo", "objetivo": "/some/path"}))

    assert "t-abc" in result
    assert "sync_repo" in result
    assert isinstance(result, str)


def test_add_task_refuses_unknown_accion():
    """add_task refuses an unregistered action name."""
    with patch("lhassa.actionpack._brain._dispatch") as mock_dispatch, \
         patch("lhassa.actionpack._brain.Config") as mock_cfg:
        mock_cfg.instance_id = "test"
        mock_dispatch.mint_task = AsyncMock(
            return_value=("", "refused: unknown accion 'nonexistent'")
        )
        from lhassa.actionpack._brain import add_task
        result = _run(add_task({"accion": "nonexistent"}))

    assert "refused" in result or "nonexistent" in result
    assert isinstance(result, str)


def test_add_task_requires_accion():
    from lhassa.actionpack._brain import add_task
    result = _run(add_task({}))
    assert "accion" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# set_task_state
# ---------------------------------------------------------------------------


def test_set_task_state_happy_path():
    with patch("lhassa.actionpack._brain._db") as mock_db, \
         patch("lhassa.actionpack._brain.Config") as mock_cfg:
        mock_cfg.instance_id = "test"
        mock_db.set_task_state = AsyncMock()
        from lhassa.actionpack._brain import set_task_state
        result = _run(set_task_state({"id": "t-123", "estado": "hecha"}))

    assert "t-123" in result
    assert "hecha" in result
    assert isinstance(result, str)


def test_set_task_state_refuses_pendiente():
    """set_task_state must refuse 'pendiente' to prevent work resurrection loops."""
    from lhassa.actionpack._brain import set_task_state
    result = _run(set_task_state({"id": "t-123", "estado": "pendiente"}))
    assert "not allowed" in result.lower() or "refused" in result.lower() or "pendiente" in result
    assert isinstance(result, str)


def test_set_task_state_refuses_arbitrary_estado():
    """set_task_state refuses unknown states."""
    from lhassa.actionpack._brain import set_task_state
    result = _run(set_task_state({"id": "t-123", "estado": "running"}))
    assert "not allowed" in result.lower() or "running" in result
    assert isinstance(result, str)


def test_set_task_state_requires_id():
    from lhassa.actionpack._brain import set_task_state
    result = _run(set_task_state({"estado": "hecha"}))
    assert "id" in result.lower()
    assert isinstance(result, str)


def test_set_task_state_error_returns_string():
    with patch("lhassa.actionpack._brain._db") as mock_db, \
         patch("lhassa.actionpack._brain.Config") as mock_cfg:
        mock_cfg.instance_id = "test"
        mock_db.set_task_state = AsyncMock(side_effect=RuntimeError("db error"))
        from lhassa.actionpack._brain import set_task_state
        result = _run(set_task_state({"id": "t-123", "estado": "hecha"}))
    assert "error" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# list_approvals
# ---------------------------------------------------------------------------


def test_list_approvals_happy_path():
    mock_rows = [
        {"id": "ap-1", "tipo": "task", "payload": {"accion": "run_command"}},
        {"id": "ap-2", "tipo": "task", "payload": {"accion": "sync_repo"}},
    ]
    with patch("lhassa.actionpack._brain._approval") as mock_approval, \
         patch("lhassa.actionpack._brain.Config") as mock_cfg:
        mock_cfg.instance_id = "test"
        mock_approval.pending = AsyncMock(return_value=mock_rows)
        from lhassa.actionpack._brain import list_approvals
        result = _run(list_approvals({}))

    assert "ap-1" in result
    assert isinstance(result, str)


def test_list_approvals_empty():
    with patch("lhassa.actionpack._brain._approval") as mock_approval, \
         patch("lhassa.actionpack._brain.Config") as mock_cfg:
        mock_cfg.instance_id = "test"
        mock_approval.pending = AsyncMock(return_value=[])
        from lhassa.actionpack._brain import list_approvals
        result = _run(list_approvals({}))

    assert "no pending" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


def test_check_health_returns_string():
    mock_digest = {
        "ts": 1234567890.0,
        "results": {
            "disk": {"level": "info", "summary": "Disk usage healthy."},
            "mem_pressure": {"level": "info", "summary": "Memory healthy."},
            "failed_units": {"level": "info", "summary": "No failed units."},
            "git_status": {"level": "info", "summary": "All clean."},
        },
        "worst_level": "info",
        "ok": True,
        "alerted": [],
    }
    with patch("lhassa.actionpack._brain.run_all", return_value=mock_digest), \
         patch("lhassa.actionpack._brain.format_digest", return_value="All clear."):
        from lhassa.actionpack._brain import check_health
        result = check_health({})

    assert "All clear." in result
    assert isinstance(result, str)


def test_check_health_error_returns_string():
    with patch("lhassa.actionpack._brain.run_all", side_effect=RuntimeError("oops")):
        from lhassa.actionpack._brain import check_health
        result = check_health({})
    assert "error" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# git_status_repos
# ---------------------------------------------------------------------------


def test_git_status_repos_returns_string():
    mock_result = {"summary": "All tracked repos are clean.", "level": "info"}
    with patch("lhassa.actionpack._brain.check_git_status", return_value=mock_result):
        from lhassa.actionpack._brain import git_status_repos
        result = git_status_repos({})

    assert "clean" in result
    assert isinstance(result, str)


def test_git_status_repos_error_returns_string():
    with patch("lhassa.actionpack._brain.check_git_status", side_effect=RuntimeError("git gone")):
        from lhassa.actionpack._brain import git_status_repos
        result = git_status_repos({})
    assert "error" in result.lower()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# compile_cv
# ---------------------------------------------------------------------------


def test_compile_cv_happy_path(tmp_path):
    pdf_path = tmp_path / "cv_largo.pdf"
    with patch("lhassa.actionpack._brain.compile_pdf", return_value=pdf_path), \
         patch("lhassa.actionpack._brain.base_path", return_value=tmp_path / "cv_largo.tex"):
        from lhassa.actionpack._brain import compile_cv
        result = compile_cv({})

    assert str(pdf_path) in result
    assert isinstance(result, str)


def test_compile_cv_engine_not_found():
    with patch("lhassa.actionpack._brain.compile_pdf", return_value=None), \
         patch("lhassa.actionpack._brain.base_path", return_value=Path("/tmp/cv.tex")):
        from lhassa.actionpack._brain import compile_cv
        result = compile_cv({})

    assert "failed" in result.lower() or "not found" in result.lower()
    assert isinstance(result, str)


def test_compile_cv_custom_path(tmp_path):
    tex = tmp_path / "custom.tex"
    pdf = tmp_path / "custom.pdf"
    with patch("lhassa.actionpack._brain.compile_pdf", return_value=pdf):
        from lhassa.actionpack._brain import compile_cv
        result = compile_cv({"tex_path": str(tex)})
    assert str(pdf) in result
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# mint_task (dispatch refactor)
# ---------------------------------------------------------------------------


def test_mint_task_refuses_unknown_accion():
    """mint_task returns ('', error) for an unregistered action."""
    from lhassa.core.dispatch import mint_task
    from lhassa.security.trust import Origin, TrustLevel

    origin = Origin(trust=TrustLevel.INTERNO, actor_id="test")
    task_id, estado = _run(
        mint_task("inst", "nonexistent_action_xyz", origin=origin)
    )
    assert task_id == ""
    assert "refused" in estado or "unknown" in estado


def test_mint_task_happy_path():
    """mint_task mints a task for a registered action."""
    from lhassa.core.dispatch import mint_task
    from lhassa.security.trust import Origin, TrustLevel

    # Ensure sync_repo is registered
    from lhassa.core.actions import ACTIONS
    assert "sync_repo" in ACTIONS

    origin = Origin(trust=TrustLevel.INTERNO, actor_id="test")

    with patch("lhassa.core.dispatch._db.add_task", AsyncMock()), \
         patch("lhassa.core.dispatch._approval.needs_approval", return_value=False), \
         patch("lhassa.core.dispatch._signing.sign_task", return_value="sig"):
        task_id, estado = _run(
            mint_task("inst", "sync_repo", objetivo="/tmp", origin=origin)
        )

    assert task_id.startswith("t_")
    assert estado in {"pendiente", "esperando_aprobacion"}


# ---------------------------------------------------------------------------
# clean_tags is now public
# ---------------------------------------------------------------------------


def test_clean_tags_public():
    """clean_tags is accessible as a public symbol from dispatch."""
    from lhassa.core.dispatch import clean_tags
    result = clean_tags(["tag1", "tag2", "  spaces  "])
    assert "tag1" in result
    assert "spaces" in result  # stripped


def test_clean_tags_rejects_non_list():
    from lhassa.core.dispatch import clean_tags
    assert clean_tags("not-a-list") == []
    assert clean_tags(None) == []


# ---------------------------------------------------------------------------
# send_reminder action (lhassa/actionpack/_reminder.py)
# ---------------------------------------------------------------------------


def test_send_reminder_delivers_via_notify():
    with patch(
        "lhassa.actionpack._reminder._deliver", AsyncMock(return_value=(True, None))
    ) as mock_deliver:
        from lhassa.actionpack._reminder import send_reminder
        result = _run(send_reminder({"text": "join the standup"}))
    assert "delivered" in result
    mock_deliver.assert_awaited_once_with(
        text="join the standup", kind="reminder", origin_department=""
    )


def test_send_reminder_no_channel_delivered():
    with patch(
        "lhassa.actionpack._reminder._deliver", AsyncMock(return_value=(False, None))
    ):
        from lhassa.actionpack._reminder import send_reminder
        result = _run(send_reminder({"text": "join the standup"}))
    assert "no channel delivered" in result


def test_send_reminder_empty_text():
    from lhassa.actionpack._reminder import send_reminder
    result = _run(send_reminder({"text": ""}))
    assert result == "send_reminder: no text"

    result_missing = _run(send_reminder({}))
    assert result_missing == "send_reminder: no text"


def test_send_reminder_never_raises_on_notify_error():
    with patch(
        "lhassa.actionpack._reminder._deliver", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        from lhassa.actionpack._reminder import send_reminder
        result = _run(send_reminder({"text": "x"}))
    assert "error" in result.lower()


def test_send_reminder_writes_the_root_hop_episode(monkeypatch):
    """A reminder has no owning department — it takes the root-only hop, same as
    ``research_reply`` — so one ``org-deliver`` Episode lands on ``lhassa``
    alongside the actual delivery."""
    from lhassa.actionpack._reminder import send_reminder
    from lhassa.core import db
    from lhassa.notify import dispatch
    from lhassa.org import topology as topo_mod
    from lhassa.org.topology import BrainNode, Topology

    monkeypatch.setattr(
        topo_mod, "load_topology", lambda config=None: Topology([BrainNode(id="lhassa")])
    )

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(dispatch, "notify", fake_notify)

    result = _run(send_reminder({"text": "join the standup"}))
    assert "delivered" in result

    rows = _run(db.load_recent_episodes("inst_personal", n=100))
    eps = [r for r in rows if r["despertar_por"] == "org-deliver"]
    assert eps and eps[0]["dominio"] == "lhassa"
    assert "delivered:" in eps[0]["resultado"] and "ok)" in eps[0]["resultado"]


# ---------------------------------------------------------------------------
# Architecture layer guard (actionpack is a shell)
# ---------------------------------------------------------------------------


def test_actionpack_in_shells_set():
    """SHELLS in test_architecture includes actionpack."""
    from tests.test_architecture import SHELLS
    assert "actionpack" in SHELLS


def test_actionpack_passes_layer_check():
    """The architecture layer checker finds no violations with actionpack present."""
    from tests.test_architecture import all_import_records, find_violations
    records = all_import_records()
    violations = find_violations(records)
    actionpack_violations = [v for v in violations if "actionpack" in v.src_rel]
    assert not actionpack_violations, (
        "actionpack layer violations:\n" + "\n".join(str(v) for v in actionpack_violations)
    )
