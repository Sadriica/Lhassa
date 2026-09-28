"""Tests for the ``digest`` seed (lhassa.seeds.digest); Etapa 2's scheduled digest.

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


def test_idempotency_is_per_slot_not_count(cfg):
    """A DOUBLED morning slot must not mask a missing evening one; the seed is
    per-hour, so re-applying against two same-hour tasks still mints the absent
    evening (the real "two morning digests, no evening" bug)."""
    import asyncio

    import lhassa.core.db as db
    from lhassa.core.dispatch import mint_task
    from lhassa.security.trust import Origin, TrustLevel

    async def _seed_two_mornings():
        await db.init_db()
        origin = Origin(trust=TrustLevel.HUMANO, actor_id="seed:digest")
        due = digest_seed._next_local_occurrence(8, 0).isoformat()
        for _ in range(2):  # two tasks at the SAME (morning) hour
            await mint_task(
                "inst_personal", "send_digest", objetivo="operative digest",
                params={}, origin=origin, due_at=due, recurrence="86400",
            )

    asyncio.run(_seed_two_mornings())

    # Count-based idempotency (2 >= 2) would skip; per-slot must add the evening.
    summary = seeds.apply_seed("digest", cfg)
    assert summary["skipped"] is False
    assert len(summary["minted"]) == 1
    evening_hour = digest_seed._next_local_occurrence(21, 0).astimezone().hour
    minted_hour = datetime.fromisoformat(
        summary["minted"][0]["due_at"]
    ).astimezone().hour
    assert minted_hour == evening_hour

    # The seed is non-destructive, so the pre-existing duplicate morning stays
    # (that's for a cleanup to remove, not a seed); but BOTH scheduled hours are
    # now covered: the point is that the evening slot got healed.
    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente"))
    hours = {
        datetime.fromisoformat(t["due_at"]).astimezone().hour
        for t in tasks if t["accion"] == "send_digest"
    }
    morning_hour = digest_seed._next_local_occurrence(8, 0).astimezone().hour
    assert hours == {morning_hour, evening_hour}


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


# ── the optional midday slot ─────────────────────────────────────────────────


def test_midday_slot_is_off_unless_configured(cfg):
    """Everyone who has not asked for a third digest keeps getting two."""
    assert not hasattr(cfg, "digest_hour_midday")  # an old config double
    assert digest_seed._schedule(cfg) == ((8, 0), (21, 0))

    cfg.digest_hour_midday = -1
    assert digest_seed._schedule(cfg) == ((8, 0), (21, 0))


def test_midday_slot_is_added_in_day_order(cfg):
    cfg.digest_hour_midday = 13
    assert digest_seed._schedule(cfg) == ((8, 0), (13, 0), (21, 0))


def test_a_midday_slot_colliding_with_a_standing_one_is_not_doubled(cfg):
    """Two tasks at the same hour would be two digests one after the other."""
    cfg.digest_hour_midday = 8
    assert digest_seed._schedule(cfg) == ((8, 0), (21, 0))


def test_bad_midday_value_is_ignored_not_guessed(cfg):
    for bad in (24, 99, "noon", None):
        cfg.digest_hour_midday = bad
        assert digest_seed._schedule(cfg) == ((8, 0), (21, 0))


def test_applying_with_a_new_midday_slot_mints_only_the_missing_one(cfg):
    """The seed is self-healing per slot, so turning the third one on later
    adds it without disturbing (or duplicating) the two already scheduled."""
    import asyncio

    import lhassa.core.db as db

    asyncio.run(digest_seed.seed_digest_schedule(cfg))
    before = asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=50))
    assert len([t for t in before if t["accion"] == "send_digest"]) == 2

    cfg.digest_hour_midday = 13
    second = asyncio.run(digest_seed.seed_digest_schedule(cfg))
    assert second["errors"] == []

    after = [
        t
        for t in asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=50))
        if t["accion"] == "send_digest"
    ]
    assert len(after) == 3
    hours = sorted(
        datetime.fromisoformat(t["due_at"]).astimezone().hour for t in after
    )
    assert hours == [8, 13, 21]
