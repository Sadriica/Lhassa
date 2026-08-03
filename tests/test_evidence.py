"""Tests for the evidence vocabulary (lhassa/memory/evidence.py) — the pure,
derived "prepared / observed / verified" claim ladder. Offline, no I/O.
"""

from lhassa.memory.evidence import evidence_state


def test_prepared_for_in_flight_statuses():
    for status in ("queued", "running", "pendiente", "esperando_aprobacion", "waiting_approval"):
        assert evidence_state({"status": status}) == "prepared"
    assert evidence_state({"estado": "pendiente"}) == "prepared"


def test_observed_for_finished_statuses_without_corroboration():
    for status in ("completed", "finished", "hecha", "failed", "fallida", "cancelled"):
        assert evidence_state({"status": status}) == "observed"


def test_verified_via_row_embedded_verify_result():
    row = {"status": "completed", "verify": {"passed": True}}
    assert evidence_state(row) == "verified"

    class FakeVerifyResult:
        passed = True

    row2 = {"status": "completed", "raw": {"verification": FakeVerifyResult()}}
    assert evidence_state(row2) == "verified"


def test_observed_stays_observed_when_verify_failed():
    row = {"status": "completed", "verify": {"passed": False}}
    assert evidence_state(row) == "observed"


def test_verified_via_context_approval():
    row = {"id": "run-1", "status": "completed"}
    context = {
        "approvals": [
            {
                "estado": "aprobada",
                "payload": {"run_id": "run-1"},
            }
        ]
    }
    assert evidence_state(row, context) == "verified"


def test_observed_when_context_approval_is_for_a_different_run():
    row = {"id": "run-1", "status": "completed"}
    context = {
        "approvals": [
            {"estado": "aprobada", "payload": {"run_id": "run-2"}},
        ]
    }
    assert evidence_state(row, context) == "observed"


def test_verified_via_context_verification():
    row = {"id": "run-1", "status": "completed"}
    context = {"verifications": [{"run_id": "run-1", "passed": True}]}
    assert evidence_state(row, context) == "verified"


def test_unknown_shape_defaults_to_prepared_the_humblest_claim():
    assert evidence_state({}) == "prepared"
    assert evidence_state({"status": "some_weird_future_status"}) == "prepared"
    assert evidence_state(None) == "prepared"  # type: ignore[arg-type]
    assert evidence_state("not-a-dict") == "prepared"  # type: ignore[arg-type]
    assert evidence_state({"status": "completed"}, context={"approvals": None}) == "observed"


def test_never_raises_on_hostile_context():
    row = {"status": "completed"}
    context = {"approvals": [{"payload": None}], "verifications": [{"result": object()}]}
    assert evidence_state(row, context) in {"observed", "verified"}
