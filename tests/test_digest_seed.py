"""Tests for the ``digest`` seed (lhassa.seeds.digest) — Etapa 2's scheduled digest.

Fully offline: the brain DB and signing key are redirected to tmp paths (the same
seam ``test_approval.py`` uses), so nothing touches the owner's real ``~/.lhassa``.
Asserts the two recurring ``send_digest`` tasks are minted with a UTC ``due_at`` and
an 86400s (daily) recurrence, and that re-applying is idempotent (no duplicate
schedule piles up).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lhassa import seeds
from lhassa.seeds import digest as digest_seed


class _Cfg(SimpleNamespace):
    pass


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    import lhassa.core.db as db
    import lhassa.security.signing as signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    return _Cfg(instance_id="inst_personal")


def test_registry_exposes_digest_seed():
    names = seeds.SEEDS.names()
    assert "digest" in names
    listed = {s["name"]: s["description"] for s in seeds.list_seeds()}
    assert "digest" in listed and listed["digest"]


def test_apply_mints_two_recurring_tasks(cfg):
    summary = seeds.apply_seed("digest", cfg)

    assert summary["errors"] == []
    assert summary["skipped"] is False
    assert len(summary["minted"]) == 2
    for entry in summary["minted"]:
        assert entry["task_id"]
        # due_at must be a valid, UTC-offset ISO timestamp (string-sortable).
        due = datetime.fromisoformat(entry["due_at"])
        assert due.utcoffset() == timedelta(0)


def test_minted_tasks_are_send_digest_with_daily_recurrence(cfg):
    seeds.apply_seed("digest", cfg)

    import asyncio

    import lhassa.core.db as db

    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente"))
    assert len(tasks) == 2
    for task in tasks:
        assert task["accion"] == "send_digest"
        assert task["recurrence"] == "86400"
        assert task["due_at"] is not None


def test_apply_is_idempotent_no_duplicate_schedule(cfg):
    first = seeds.apply_seed("digest", cfg)
    assert first["skipped"] is False

    second = seeds.apply_seed("digest", cfg)
    assert second["skipped"] is True
    assert second["minted"] == []

    import asyncio

    import lhassa.core.db as db

    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente"))
    send_digest_tasks = [t for t in tasks if t["accion"] == "send_digest"]
    assert len(send_digest_tasks) == 2  # still exactly two, no pile-up


def test_next_local_occurrence_rolls_to_tomorrow_if_past():
    now_local = datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc).astimezone()
    target = digest_seed._next_local_occurrence(8, 0, now_local=now_local)
    # 08:00 already passed today (it's 22:00) → rolls to tomorrow.
    assert target.astimezone(now_local.tzinfo).date() == (now_local.date() + timedelta(days=1))


def test_next_local_occurrence_stays_today_if_future():
    now_local = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc).astimezone()
    target = digest_seed._next_local_occurrence(8, 0, now_local=now_local)
    assert target.astimezone(now_local.tzinfo).date() == now_local.date()


def test_schedule_honors_config_overrides(cfg):
    cfg.digest_hour_morning = 7
    cfg.digest_hour_evening = 22
    assert digest_seed._schedule(cfg) == ((7, 0), (22, 0))


def test_schedule_falls_back_to_defaults_on_bad_override(cfg):
    cfg.digest_hour_morning = 99  # out of range → falls back to default
    cfg.digest_hour_evening = "not-an-int"
    assert digest_seed._schedule(cfg) == ((8, 0), (21, 0))
