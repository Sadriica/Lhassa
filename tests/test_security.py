"""Tests for the Phase-0 security primitives (§2.6) — offline, deterministic.

Covers: Ed25519 signing round-trip, the trust-degrades-never-promotes invariant,
the Task canonical-bytes bridge, and the deny-by-default task-level allowlist.
Keys are written to a tmp dir via monkeypatch so nothing touches ``~/.lhassa``.
"""

import pytest

from lhassa.core.task import Task
from lhassa.security import allowlist as al
from lhassa.security import signing
from lhassa.security.allowlist import ActionSpec, AllowDecision, allow, build_allowlist
from lhassa.security.trust import Origin, TrustLevel, degrade


# ── fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_keys(tmp_path, monkeypatch):
    """Point signing at a throwaway key directory for each test."""
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    return tmp_path


def _task(*, accion="sync_repo", objetivo="proyecto_X", trust=TrustLevel.INTERNO):
    return Task(
        id="t_001",
        instance_id="inst_personal",
        accion=accion,
        objetivo=objetivo,
        params={"branch": "main"},
        origen=Origin(trust=trust, actor_id="duenio"),
        estado="pendiente",
        creado_en="2026-06-29T00:00:00+00:00",
    )


# ── signing round-trip ──────────────────────────────────────────────────────────

def test_sign_verify_roundtrip(tmp_keys):
    data = b"hello brain"
    sig = signing.sign(data)
    assert sig.startswith("ed25519:")
    assert signing.verify(data, sig, signing.load_public_key()) is True


def test_verify_tampered_data_is_false(tmp_keys):
    sig = signing.sign(b"original")
    assert signing.verify(b"tampered", sig, signing.load_public_key()) is False


def test_verify_tampered_signature_is_false(tmp_keys):
    data = b"payload"
    sig = signing.sign(data)
    # Flip the last hex nibble of the signature.
    bad = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    assert signing.verify(data, bad, signing.load_public_key()) is False


def test_verify_accepts_hex_public_key_and_prefix(tmp_keys):
    data = b"payload"
    sig = signing.sign(data)
    pk_hex = signing.public_key_hex()
    assert signing.verify(data, sig, pk_hex) is True
    # The ed25519: prefix is tolerated on both signature and a prefixed pubkey.
    assert signing.verify(data, sig.removeprefix("ed25519:"), pk_hex) is True


@pytest.mark.parametrize(
    "data, sig, pk",
    [
        (b"x", "not-hex-at-all", "deadbeef"),
        (b"x", "ed25519:zzzz", None),
        (b"x", "", 12345),
        (b"x", "ed25519:abcd", object()),
    ],
)
def test_verify_never_raises_on_garbage(tmp_keys, data, sig, pk):
    assert signing.verify(data, sig, pk) is False


def test_sign_task_verify_task_roundtrip(tmp_keys):
    task = _task()
    task.firma = signing.sign_task(task)
    assert signing.verify_task(task, signing.load_public_key()) is True
    # Mutating any signable field breaks verification.
    task.objetivo = "otro_repo"
    assert signing.verify_task(task, signing.load_public_key()) is False


def test_key_files_persist_and_reload(tmp_keys):
    sig = signing.sign(b"data")
    # Public key file exists alongside the private key.
    assert (tmp_keys / "keys" / "ed25519").exists()
    assert (tmp_keys / "keys" / "ed25519.pub").exists()
    # Reloading the same key still verifies signatures made earlier.
    assert signing.verify(b"data", sig, signing.load_public_key()) is True


# ── trust: degrade returns the minimum, never promotes ──────────────────────────

def test_degrade_returns_minimum():
    assert degrade(TrustLevel.HUMANO, TrustLevel.MUNDO) == TrustLevel.MUNDO
    assert degrade(TrustLevel.HUMANO, TrustLevel.INTERNO) == TrustLevel.INTERNO


def test_degrade_never_promotes():
    # Combining a HUMANO with anything lower cannot yield more than the lowest.
    result = degrade(TrustLevel.HUMANO, TrustLevel.HUMANO, TrustLevel.MUNDO)
    assert result == TrustLevel.MUNDO


def test_degrade_accepts_origins_and_ints():
    assert degrade(Origin(TrustLevel.HUMANO), Origin(TrustLevel.INTERNO)) == TrustLevel.INTERNO
    assert degrade(2, 0, 1) == TrustLevel.MUNDO


def test_degrade_single_input_is_itself():
    assert degrade(TrustLevel.INTERNO) == TrustLevel.INTERNO


def test_degrade_empty_raises():
    with pytest.raises(ValueError):
        degrade()


# ── Task canonical-bytes + db bridge ────────────────────────────────────────────

def test_canonical_bytes_deterministic_and_excludes_firma():
    a = _task()
    b = _task()
    b.firma = "ed25519:whatever"  # firma is NOT part of the signed bytes
    assert a.canonical_bytes() == b.canonical_bytes()


def test_to_db_kwargs_and_from_db_row_roundtrip():
    task = _task()
    kwargs = task.to_db_kwargs()
    assert kwargs["origen_trust"] == int(TrustLevel.INTERNO)
    assert kwargs["origen_actor"] == "duenio"
    # Simulate a db row (as load_pending_tasks returns it, params decoded).
    row = {
        "id": task.id,
        "instance_id": task.instance_id,
        "accion": task.accion,
        "objetivo": task.objetivo,
        "params": task.params,
        "origen_trust": kwargs["origen_trust"],
        "origen_actor": kwargs["origen_actor"],
        "estado": task.estado,
        "firma": "",
        "creado_en": task.creado_en,
    }
    rebuilt = Task.from_db_row(row)
    assert rebuilt.origen.trust == TrustLevel.INTERNO
    assert rebuilt.origen.actor_id == "duenio"
    assert rebuilt.canonical_bytes() == task.canonical_bytes()


# ── allowlist: deny-by-default ──────────────────────────────────────────────────

@pytest.fixture
def wildcard_allowlist(monkeypatch):
    """Dev default: sync_repo accepts any objetivo."""
    monkeypatch.setattr(al, "ALLOWLIST", build_allowlist(repos="*"))


def _signed(task):
    task.firma = signing.sign_task(task)
    return task


def test_unknown_action_denied(tmp_keys, wildcard_allowlist):
    task = _signed(_task(accion="rm_rf_slash"))
    decision = allow(task)
    assert isinstance(decision, AllowDecision)
    assert decision.allowed is False
    assert "not in the allowlist" in decision.reason


def test_allowed_when_all_checks_pass(tmp_keys, wildcard_allowlist):
    task = _signed(_task())
    decision = allow(task)
    assert decision.allowed is True
    assert decision.requires_approval is False


def test_denied_on_low_trust(tmp_keys, wildcard_allowlist):
    task = _signed(_task(trust=TrustLevel.MUNDO))  # below INTERNO
    decision = allow(task)
    assert decision.allowed is False
    assert "trust" in decision.reason.lower()


def test_denied_on_disallowed_objetivo(tmp_keys, monkeypatch):
    monkeypatch.setattr(al, "ALLOWLIST", build_allowlist(repos={"proyecto_X"}))
    task = _signed(_task(objetivo="repo_prohibido"))
    decision = allow(task)
    assert decision.allowed is False
    assert "objetivo" in decision.reason
    # An allowed objetivo with the same restricted set passes.
    ok = _signed(_task(objetivo="proyecto_X"))
    assert allow(ok).allowed is True


def test_denied_on_bad_signature(tmp_keys, wildcard_allowlist):
    task = _task()
    task.firma = "ed25519:" + "00" * 64  # well-formed but wrong
    decision = allow(task)
    assert decision.allowed is False
    assert "signature" in decision.reason.lower()


def test_denied_on_missing_signature(tmp_keys, wildcard_allowlist):
    task = _task()  # firma == ""
    assert allow(task).allowed is False


def test_requires_approval_surfaced(tmp_keys, monkeypatch):
    monkeypatch.setattr(
        al,
        "ALLOWLIST",
        {
            "sync_repo": ActionSpec(
                objetivos="*", min_trust=TrustLevel.INTERNO, requires_approval=True
            )
        },
    )
    # Surfaced on allow...
    ok = allow(_signed(_task()))
    assert ok.allowed is True
    assert ok.requires_approval is True
    # ...and on a deny where the spec exists.
    low = allow(_signed(_task(trust=TrustLevel.MUNDO)))
    assert low.allowed is False
    assert low.requires_approval is True


def test_allow_with_explicit_public_key(tmp_keys, wildcard_allowlist):
    task = _signed(_task())
    assert allow(task, public_key=signing.public_key_hex()).allowed is True
