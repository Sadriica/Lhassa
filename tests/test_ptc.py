"""Tests for lhassa.agents.ptc — Programmatic Tool Calling (Fase 3).

All offline, no LLM. A dummy action is registered directly against the real
``core.actions`` registry (and a matching allowlist entry) for the duration of
each test and cleaned up afterwards — nothing here touches the real brain.db or
the owner's real signing key (``tmp_keys`` redirects ``signing.KEY_PATH``).

The end-to-end tests exercise the REAL Unix-domain-socket RPC path: a generated
script actually connects out to the parent over ``LHASSA_PTC_SOCKET``. The
default test-suite env (``LHASSA_QUARANTINE_ISOLATION=none``, set in
conftest.py) exercises the plain-subprocess fallback; one dedicated test forces
the real ``unshare`` network-namespace isolation (skipped if unavailable on the
host) to prove the UDS socket still works when the sandboxed process has no
route to the outside world.
"""

from __future__ import annotations

import shutil
import time

import pytest

from lhassa.agents import ptc
from lhassa.core import actions as _actions
from lhassa.security import allowlist as _allowlist
from lhassa.security import signing
from lhassa.security.trust import TrustLevel

_DUMMY = "ptc_test_echo"


def _dummy_echo(params: dict) -> str:
    """Echo back whatever msg was given (a test-only action)."""
    return f"echo:{(params or {}).get('msg', '')}"


@pytest.fixture
def tmp_keys(tmp_path, monkeypatch):
    """Point signing at a throwaway key directory for each test."""
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")


@pytest.fixture
def dummy_action(tmp_keys):
    """Register a harmless dummy action + allowlist entry; clean up after."""
    _actions.register_action(_DUMMY, _dummy_echo, override=True)
    _allowlist.ALLOWLIST[_DUMMY] = _allowlist.ActionSpec(
        objetivos=_allowlist.WILDCARD,
        min_trust=TrustLevel.INTERNO,
        requires_approval=False,
    )
    try:
        yield _DUMMY
    finally:
        _actions._REGISTRY.unregister(_DUMMY)
        _allowlist.ALLOWLIST.pop(_DUMMY, None)


# ── stub generation ───────────────────────────────────────────────────────────


def test_generate_stub_includes_dummy_action(dummy_action):
    stub = ptc.generate_stub()
    assert "def ptc_test_echo(**params):" in stub
    assert "Echo back whatever msg was given" in stub
    assert "_rpc(" in stub
    assert "LHASSA_PTC_SOCKET" in stub


def test_generate_stub_sanitizes_and_documents_scope():
    stub = ptc.generate_stub()
    assert "ONLY capability" in stub
    # Every registered action must produce a syntactically valid function def.
    compile(stub, "<lhassa_actions>", "exec")


# ── end-to-end RPC path ───────────────────────────────────────────────────────


async def test_end_to_end_two_calls_round_trip(dummy_action):
    script = (
        "import lhassa_actions as a\n"
        "r1 = a.ptc_test_echo(msg='one')\n"
        "r2 = a.ptc_test_echo(msg='two')\n"
        "print(r1)\n"
        "print(r2)\n"
    )
    res = await ptc.run_script(script, instance_id="test-inst", timeout=20)
    assert res["ok"] is True, res
    assert "echo:one" in res["stdout"]
    assert "echo:two" in res["stdout"]
    assert len(res["calls"]) == 2
    assert all(c["ok"] for c in res["calls"])
    assert all(c["accion"] == _DUMMY for c in res["calls"])
    assert res["error"] is None
    assert res["elapsed_s"] >= 0


async def test_unknown_action_is_refused_never_executed(dummy_action):
    script = (
        "import lhassa_actions as a\n"
        "try:\n"
        "    a._rpc('does_not_exist_anywhere', {})\n"
        "    print('SHOULD-NOT-REACH')\n"
        "except RuntimeError as exc:\n"
        "    print('refused:', exc)\n"
    )
    res = await ptc.run_script(script, instance_id="test-inst", timeout=20)
    assert res["ok"] is True, res
    assert "refused:" in res["stdout"]
    assert "SHOULD-NOT-REACH" not in res["stdout"]
    assert len(res["calls"]) == 1
    assert res["calls"][0]["ok"] is False


async def test_action_outside_allowlist_is_refused(dummy_action, monkeypatch):
    # Register the action in ACTIONS but withhold the allowlist entry — the
    # allowlist gate, not the registry, is what must refuse it.
    name = "ptc_test_not_allowlisted"
    _actions.register_action(name, _dummy_echo, override=True)
    try:
        script = f"import lhassa_actions as a\na.{name}(msg='x')\n"
        res = await ptc.run_script(script, instance_id="test-inst", timeout=20)
        assert res["ok"] is False
        assert len(res["calls"]) == 1
        assert res["calls"][0]["ok"] is False
    finally:
        _actions._REGISTRY.unregister(name)


async def test_max_calls_enforced(dummy_action):
    script = (
        "import lhassa_actions as a\n"
        "for i in range(100):\n"
        "    try:\n"
        "        a.ptc_test_echo(msg=str(i))\n"
        "    except RuntimeError:\n"
        "        pass\n"
    )
    res = await ptc.run_script(script, instance_id="test-inst", timeout=30, max_calls=50)
    assert len(res["calls"]) == 50
    assert all(c["ok"] for c in res["calls"])


async def test_timeout_enforced_no_hang(dummy_action):
    script = "import time\ntime.sleep(30)\n"
    start = time.monotonic()
    res = await ptc.run_script(script, instance_id="test-inst", timeout=2)
    elapsed = time.monotonic() - start
    assert res["ok"] is False
    assert "timed out" in (res["error"] or "")
    assert elapsed < 10


async def test_stdout_cap_enforced(dummy_action):
    script = "print('x' * 200000)\n"
    res = await ptc.run_script(script, instance_id="test-inst", timeout=20)
    assert len(res["stdout"]) < 60_000
    assert "chars)" in res["stdout"]


async def test_run_script_never_raises_on_syntax_error(dummy_action):
    res = await ptc.run_script("def broken(:\n", instance_id="test-inst", timeout=20)
    assert res["ok"] is False
    assert isinstance(res["stderr"], str)


# ── real network-namespace isolation (skips if unshare is unavailable) ───────


async def test_uds_survives_real_network_namespace(dummy_action, monkeypatch):
    if not shutil.which("unshare"):
        pytest.skip("unshare not available on this host")
    # conftest.py forces LHASSA_QUARANTINE_ISOLATION=none for the whole suite;
    # lift that override here to prove the UDS socket works even when the
    # child is launched inside a real, routeless network namespace.
    monkeypatch.delenv("LHASSA_QUARANTINE_ISOLATION", raising=False)
    script = (
        "import lhassa_actions as a\n"
        "print(a.ptc_test_echo(msg='namespaced'))\n"
    )
    res = await ptc.run_script(script, instance_id="test-inst", timeout=20)
    assert res["ok"] is True, res
    assert "echo:namespaced" in res["stdout"]
    assert len(res["calls"]) == 1


# ── the actionpack action wrapper ─────────────────────────────────────────────


async def test_actionpack_run_script_action_wraps_ptc(dummy_action):
    from lhassa.actionpack import _script

    script = "import lhassa_actions as a\nprint(a.ptc_test_echo(msg='hi'))\n"
    out = await _script.run_script({"script": script, "instance_id": "test-inst"})
    assert out.startswith("run_script: ok")
    assert "calls=1" in out


async def test_actionpack_run_script_action_empty_script():
    from lhassa.actionpack import _script

    out = await _script.run_script({})
    assert out == "run_script: no script provided"
