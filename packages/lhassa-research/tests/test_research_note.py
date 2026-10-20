"""draft_research_note; the evidence machine. Fully offline.

Pins: the evidence pack carries per-datum provenance; a stubbed model draft is
written with the pack as a verbatim appendix; no model → pack-only file (still
valuable); the draft header demands review; never raises.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lhassa.research.note import (
    _DRAFT_HEADER,
    draft_research_note,
    gather_evidence,
)


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    return db


def _cfg(tmp_path):
    return SimpleNamespace(
        instance_id="inst_personal",
        notes_dir=str(tmp_path / "drafts"),
    )


def test_evidence_pack_carries_provenance(brain, tmp_path):
    db = brain
    asyncio.run(db.insert_bench_run({
        "id": "b1", "instance_id": "inst_personal", "fixture_id": "earned_autonomy",
        "subject": "autonomy-route", "passed": True, "metric": 1.0,
        "cost_usd": 0.0001, "created_at": datetime.now(timezone.utc).isoformat(),
    }))
    asyncio.run(db.insert_bench_run({
        "id": "b2", "instance_id": "inst_personal", "fixture_id": "unrelated",
        "subject": "weather", "passed": True, "metric": 1.0,
        "cost_usd": 0.0001, "created_at": datetime.now(timezone.utc).isoformat(),
    }))
    pack = asyncio.run(gather_evidence("earned autonomy", _cfg(tmp_path)))
    assert pack["topic"] == "earned autonomy"
    assert any("bench_runs" in s for s in pack["sources"])
    assert [row["id"] for row in pack["bench_runs"]] == ["b1"]
    assert pack["scope"]["episode_window_days"] == 7
    assert "episodes_window" in pack
    assert "ledger_today" in pack["operational_context"]


def test_draft_with_stub_model_writes_note_plus_appendix(brain, tmp_path):
    class _Prov:
        name = "stub"

        async def complete(self, messages, *, model=None, max_tokens=None, system=None):
            assert "EVIDENCE PACK" in messages[0]["content"]
            return SimpleNamespace(text="Thesis: autonomy is earned. (source: bench_runs table)")

    cfg = _cfg(tmp_path)
    out = asyncio.run(draft_research_note({
        "topic": "Earned Autonomy", "config": cfg, "provider": _Prov(),
    }))
    assert "written to" in out
    path = tmp_path / "drafts" / "earned-autonomy.md"
    text = path.read_text()
    assert text.startswith(_DRAFT_HEADER)
    assert "Thesis: autonomy is earned" in text
    assert "evidence pack (verbatim" in text
    assert '"topic": "Earned Autonomy"' in text


def test_no_model_degrades_to_pack_only(brain, tmp_path, monkeypatch):
    import lhassa.llm.router as router_mod

    monkeypatch.setattr(
        router_mod, "decide",
        lambda trigger, ctx, config: SimpleNamespace(provider=None, model=None, reason="none"),
    )
    cfg = _cfg(tmp_path)
    out = asyncio.run(draft_research_note({"topic": "cache", "config": cfg}))
    assert "evidence pack (no model)" in out
    text = (tmp_path / "drafts" / "cache.md").read_text()
    assert "evidence pack only" in text
    assert _DRAFT_HEADER in text


def test_never_raises_and_needs_topic(brain, tmp_path):
    assert "no topic" in asyncio.run(draft_research_note({"config": _cfg(tmp_path)}))
    assert isinstance(asyncio.run(draft_research_note(None)), str)
