"""Tests for the utility tribunal; lhassa.agents.utility + review_capabilities.

Offline: episodes are inserted directly via lhassa.core.db.append_episode
against a tmp brain DB, and the ACTIONS registry is monkeypatched to a small
known set. No LLM, no network, no writes outside tmp_path.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.agents import utility


@dataclass
class _FakeEpisode:
    """Minimal Episode double; just enough for db.append_episode."""

    id: str
    instance_id: str
    despertar_por: str
    accion: str
    decision: str = ""
    resultado: str = ""
    costo_usd: float = 0.0
    creado_en: str = ""
    memoria_cargada: list = field(default_factory=list)
    opciones: list = field(default_factory=list)
    dominio: str = ""


@pytest_asyncio.fixture
async def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


@pytest.fixture
def caps(monkeypatch):
    """Pin the capability registry to a small, known set."""
    names = ["alpha", "beta", "gamma"]
    monkeypatch.setattr(utility, "_registered_capabilities", lambda: sorted(names))
    return names


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


async def _seed(db, *, accion, despertar_por, when=None, instance_id="inst_test"):
    await db.append_episode(
        _FakeEpisode(
            id=db._new_id("ep"),
            instance_id=instance_id,
            despertar_por=despertar_por,
            accion=accion,
            creado_en=when or db._now(),
        )
    )


# ── the wake vocabulary ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("telegram", utility.WAKE_OWNER),
        ("telegram:12345", utility.WAKE_OWNER),
        ("cli", utility.WAKE_OWNER),
        ("TELEGRAM", utility.WAKE_OWNER),
        ("task:send_digest", utility.WAKE_SELF),
        ("monitor", utility.WAKE_SELF),
        ("org-deliver", utility.WAKE_SELF),
        ("temple-execute", utility.WAKE_SELF),
        ("health:health_alert", utility.WAKE_SELF),
        ("", utility.WAKE_UNKNOWN),
        (None, utility.WAKE_UNKNOWN),
        ("something-new", utility.WAKE_UNKNOWN),
    ],
)
def test_classify_wake(raw, expected):
    assert utility.classify_wake(raw) == expected


def test_owner_wins_over_self_prefix_collision():
    """'org' is self-driven but 'owner' is owner-driven; no prefix bleed."""
    assert utility.classify_wake("owner") == utility.WAKE_OWNER
    assert utility.classify_wake("org") == utility.WAKE_SELF


# ── survey ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_survey_separates_owner_from_self_driven(brain, caps, monkeypatch):
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_dormant_days", 30, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)

    # alpha: a human reached for it yesterday -> active
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(1))
    # beta: only Lhassa's own scheduler, and long ago -> self_driven
    for d in (40, 45, 50):
        await _seed(brain, accion="beta", despertar_por="task:beta", when=_iso(d))
    # gamma: never used at all -> unused

    rows = {r["capability"]: r for r in await survey_now()}

    assert rows["alpha"]["state"] == utility.STATE_ACTIVE
    assert rows["alpha"]["owner_uses"] == 1
    assert rows["beta"]["state"] == utility.STATE_SELF_DRIVEN
    assert rows["beta"]["self_uses"] == 3
    assert rows["beta"]["owner_uses"] == 0
    assert rows["gamma"]["state"] == utility.STATE_UNUSED
    assert rows["gamma"]["total_uses"] == 0


async def survey_now():
    return await utility.survey("inst_test")


@pytest.mark.asyncio
async def test_stale_owner_use_falls_out_of_the_window(brain, caps, monkeypatch):
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_dormant_days", 30, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)

    # A human used it; but 90 days ago. Used once ≠ still earning its place.
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(90))
    rows = {r["capability"]: r for r in await survey_now()}
    assert rows["alpha"]["state"] == utility.STATE_SELF_DRIVEN
    assert rows["alpha"]["owner_uses"] == 1
    assert rows["alpha"]["days_since_owner_use"] == pytest.approx(90, abs=1)


@pytest.mark.asyncio
async def test_survey_is_sorted_least_earned_first(brain, caps, monkeypatch):
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)
    for _ in range(5):
        await _seed(brain, accion="gamma", despertar_por="telegram")
    await _seed(brain, accion="beta", despertar_por="telegram")

    rows = await survey_now()
    assert [r["capability"] for r in rows] == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_survey_never_raises_on_a_broken_registry(brain, monkeypatch):
    def _boom():
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(utility, "_registered_capabilities", _boom)
    assert await utility.survey("inst_test") == []


# ── the task ledger (deferred execution the owner asked for) ─────────────────


@pytest.mark.asyncio
async def test_owner_minted_task_counts_even_when_execution_is_deferred(
    brain, caps, monkeypatch
):
    """/remind mints today and fires next week from task:*; still owner-driven."""
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_dormant_days", 30, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)

    # The episode alone looks self-driven…
    await _seed(brain, accion="alpha", despertar_por="task:alpha", when=_iso(2))
    # …but the owner minted the task from Telegram.
    async with __import__("lhassa._vendor", fromlist=["aiosqlite"]).aiosqlite.connect(
        brain.DB_PATH
    ) as conn:
        await conn.execute(
            "INSERT INTO tasks (id, instance_id, accion, objetivo, params, "
            "origen_trust, origen_actor, estado, firma, creado_en) "
            "VALUES ('t1','inst_test','alpha','','{}',2,'telegram','hecha','',?)",
            (_iso(2),),
        )
        await conn.commit()

    rows = {r["capability"]: r for r in await survey_now()}
    assert rows["alpha"]["state"] == utility.STATE_ACTIVE
    assert rows["alpha"]["owner_uses"] == 1


@pytest.mark.asyncio
async def test_overlapping_ledgers_are_maxed_not_summed(brain, caps, monkeypatch):
    """The same act in both ledgers must not inflate the count."""
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)

    # One act, recorded twice: an owner-woken episode AND an owner-minted task.
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(1))
    async with __import__("lhassa._vendor", fromlist=["aiosqlite"]).aiosqlite.connect(
        brain.DB_PATH
    ) as conn:
        await conn.execute(
            "INSERT INTO tasks (id, instance_id, accion, objetivo, params, "
            "origen_trust, origen_actor, estado, firma, creado_en) "
            "VALUES ('t1','inst_test','alpha','','{}',2,'telegram','hecha','',?)",
            (_iso(1),),
        )
        await conn.commit()

    rows = {r["capability"]: r for r in await survey_now()}
    assert rows["alpha"]["owner_uses"] == 1  # max(1, 1), never 2
    assert rows["alpha"]["total_uses"] == 1


@pytest.mark.asyncio
async def test_a_seed_minted_task_is_not_owner_use(brain, caps, monkeypatch):
    """seed:digest mints at HUMANO trust; trust is not the provenance signal."""
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)

    await _seed(brain, accion="alpha", despertar_por="task:alpha", when=_iso(1))
    async with __import__("lhassa._vendor", fromlist=["aiosqlite"]).aiosqlite.connect(
        brain.DB_PATH
    ) as conn:
        await conn.execute(
            "INSERT INTO tasks (id, instance_id, accion, objetivo, params, "
            "origen_trust, origen_actor, estado, firma, creado_en) "
            "VALUES ('t1','inst_test','alpha','','{}',2,'seed:digest','hecha','',?)",
            (_iso(1),),
        )
        await conn.commit()

    rows = {r["capability"]: r for r in await survey_now()}
    assert rows["alpha"]["state"] == utility.STATE_SELF_DRIVEN
    assert rows["alpha"]["owner_uses"] == 0


# ── the docket (find_dormant) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grace_period_shields_a_young_instance(brain, caps, monkeypatch):
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_grace_days", 14, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)

    # Only 3 days of history: nothing has had TIME to be used.
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(3))
    assert await utility.find_dormant("inst_test") == []


@pytest.mark.asyncio
async def test_docket_lists_unused_and_self_driven_once_past_grace(brain, caps, monkeypatch):
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_grace_days", 14, raising=False)
    monkeypatch.setattr(utility.Config, "utility_dormant_days", 30, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)

    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(60))
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(1))
    await _seed(brain, accion="beta", despertar_por="task:beta", when=_iso(50))

    docket = {r["capability"] for r in await utility.find_dormant("inst_test")}
    assert docket == {"beta", "gamma"}  # alpha is actively owner-driven


@pytest.mark.asyncio
async def test_pinned_capability_is_surveyed_but_never_on_the_docket(brain, caps, monkeypatch):
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_grace_days", 14, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "gamma", raising=False)

    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(60))

    rows = {r["capability"]: r for r in await survey_now()}
    assert rows["gamma"]["pinned"] is True
    docket = {r["capability"] for r in await utility.find_dormant("inst_test")}
    assert "gamma" not in docket
    assert "beta" in docket


# ── the action ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_capabilities_parks_one_approval_per_capability_once(
    brain, caps, monkeypatch
):
    from lhassa.actionpack import _utility as action_mod

    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_grace_days", 14, raising=False)
    monkeypatch.setattr(utility.Config, "utility_dormant_days", 30, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)
    monkeypatch.setattr(action_mod.Config, "instance_id", "inst_test", raising=False)

    parked: list = []

    async def _fake_approval(instance_id, *, tipo, payload):
        parked.append((tipo, payload))
        return "appr_1"

    notified: list = []

    async def _fake_notify(msg, level="info"):
        notified.append(msg)

    monkeypatch.setattr(action_mod._approval, "request_approval", _fake_approval)
    monkeypatch.setattr(action_mod, "_notify", _fake_notify)

    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(60))
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(1))

    out = await action_mod.review_capabilities({"instance_id": "inst_test"})
    assert "2 dormant" in out
    assert {p["capability"] for _, p in parked} == {"beta", "gamma"}
    assert all(t == "retire_capability" for t, _ in parked)
    assert len(notified) == 1

    # Second sweep: the docket is deduped via watch_state; nothing new parked.
    parked.clear()
    notified.clear()
    out2 = await action_mod.review_capabilities({"instance_id": "inst_test"})
    assert parked == []
    assert notified == []
    assert "already on the docket" in out2


@pytest.mark.asyncio
async def test_review_capabilities_caps_the_docket_and_reports_the_remainder(
    brain, monkeypatch
):
    from lhassa.actionpack import _utility as action_mod

    many = [f"cap{i}" for i in range(10)]
    monkeypatch.setattr(utility, "_registered_capabilities", lambda: sorted(many))
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_grace_days", 14, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)
    monkeypatch.setattr(action_mod.Config, "instance_id", "inst_test", raising=False)

    async def _fake_approval(instance_id, *, tipo, payload):
        return "appr"

    async def _fake_notify(msg, level="info"):
        return None

    monkeypatch.setattr(action_mod._approval, "request_approval", _fake_approval)
    monkeypatch.setattr(action_mod, "_notify", _fake_notify)

    # Old history so the grace period is satisfied.
    await _seed(brain, accion="cap0", despertar_por="telegram", when=_iso(60))

    out = await action_mod.review_capabilities({"instance_id": "inst_test", "limit": 3})
    assert "3 proposed" in out
    assert "deferred" in out  # truncation is REPORTED, never silent


@pytest.mark.asyncio
async def test_review_capabilities_never_raises(brain, monkeypatch):
    from lhassa.actionpack import _utility as action_mod

    async def _boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(action_mod._utility, "find_dormant", _boom)
    out = await action_mod.review_capabilities({"instance_id": "inst_test"})
    assert out.startswith("review_capabilities: error:")


# ── the approval renders as something a human can judge ───────────────────────


def test_describe_renders_retire_capability():
    from lhassa.security.approval import describe

    unused = describe(
        {"tipo": "retire_capability", "payload": {"capability": "beta", "state": "unused"}}
    )
    assert "beta" in unused["title"]
    assert "never used" in unused["detail"]

    self_driven = describe(
        {
            "tipo": "retire_capability",
            "payload": {
                "capability": "gamma",
                "state": "self_driven",
                "self_uses": 12,
                "days_since_owner_use": 47.0,
            },
        }
    )
    assert "gamma" in self_driven["title"]
    assert "47d" in self_driven["detail"]
    assert "12×" in self_driven["detail"]
