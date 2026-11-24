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
    # The fake names are not in the real allowlist/door maps, so declare that they
    # have an instrumented door; and switch the per-capability grace off by default
    # (tests of that grace set it back explicitly).
    monkeypatch.setattr(utility, "_door_names", lambda: set(names))
    monkeypatch.setattr(utility.Config, "utility_capability_grace_days", 0, raising=False)
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
    # It is NOT "self_driven" (Lhassa never used it alone): the owner did, so it
    # is the distinct state dormant_owner.
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(90))
    rows = {r["capability"]: r for r in await survey_now()}
    assert rows["alpha"]["state"] == utility.STATE_DORMANT_OWNER
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
    monkeypatch.setattr(utility, "_door_names", lambda: set(many))
    monkeypatch.setattr(utility.Config, "utility_capability_grace_days", 0, raising=False)
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
    assert "×12" in self_driven["detail"]
    assert "approving records it as a retirement candidate; nothing is removed" in self_driven["detail"]


# ══ tribunal redesign: states, exclusions, per-capability grace, closures, pins ══


async def _sweep_env(monkeypatch, action_mod, names, *, real_approvals=True):
    """Wire a deterministic sweep: fixed registry, doors for all, no grace."""
    monkeypatch.setattr(utility, "_registered_capabilities", lambda: sorted(names))
    monkeypatch.setattr(utility, "_door_names", lambda: set(names))
    for attr, val in (
        ("instance_id", "inst_test"),
        ("utility_grace_days", 14),
        ("utility_dormant_days", 30),
        ("utility_capability_grace_days", 0),
        ("utility_pinned", ""),
        ("approval_pending_cap", 20),
    ):
        monkeypatch.setattr(utility.Config, attr, val, raising=False)
        monkeypatch.setattr(action_mod.Config, attr, val, raising=False)

    async def _notify(msg, level="info"):
        return None

    monkeypatch.setattr(action_mod, "_notify", _notify)

    async def _no_brief(*a, **k):
        return None

    monkeypatch.setattr(action_mod._approval, "_stamp_english_brief", _no_brief)


@pytest.mark.asyncio
async def test_four_states_distinguish_owner_dormant_from_self_driven(brain, caps, monkeypatch):
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_dormant_days", 30, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(80))  # owner, old
    await _seed(brain, accion="beta", despertar_por="task:beta", when=_iso(2))  # only Lhassa
    rows = {r["capability"]: r for r in await survey_now()}
    assert rows["alpha"]["state"] == utility.STATE_DORMANT_OWNER
    assert rows["beta"]["state"] == utility.STATE_SELF_DRIVEN
    assert rows["gamma"]["state"] == utility.STATE_UNUSED


@pytest.mark.asyncio
async def test_seeded_and_infrastructure_actions_are_never_on_the_docket(brain, monkeypatch):
    names = ["temple_run", "send_digest", "notify", "distill_memory", "review_capabilities",
             "set_task_state", "real_cap"]
    monkeypatch.setattr(utility, "_registered_capabilities", lambda: sorted(names))
    monkeypatch.setattr(utility, "_door_names", lambda: set(names))
    for attr, val in (("instance_id", "inst_test"), ("utility_grace_days", 14),
                      ("utility_capability_grace_days", 0), ("utility_pinned", "")):
        monkeypatch.setattr(utility.Config, attr, val, raising=False)
    await _seed(brain, accion="x", despertar_por="telegram", when=_iso(60))
    docket = {r["capability"] for r in await utility.find_dormant("inst_test")}
    assert docket == {"real_cap"}
    # ...but they are still SURVEYED, tagged with why they are exempt.
    rows = {r["capability"]: r for r in await survey_now()}
    assert rows["temple_run"]["excluded"] == "infrastructure"
    assert rows["distill_memory"]["excluded"] == "seeded"
    assert rows["real_cap"]["excluded"] is None


def test_exclusions_are_derived_from_the_real_seed_and_temple_sets():
    import lhassa.actionpack._utility  # noqa: F401  (registers the shell-side sources)
    from lhassa.actionpack._temple import TEMPLE_ACTIONS
    from lhassa.seeds.maintenance import _RECURRING_ACTIONS

    for a, _ in _RECURRING_ACTIONS:
        assert utility.excluded_reason(a) == "seeded"
    for a in TEMPLE_ACTIONS:
        assert utility.excluded_reason(a) is not None
    for a in ("add_fact", "add_task", "org_run", "run_script", "set_task_state", "sync_repo"):
        assert utility.excluded_reason(a) is not None  # the measured instrumentation gaps


@pytest.mark.asyncio
async def test_unused_without_an_instrumented_door_is_never_judged(brain, monkeypatch):
    monkeypatch.setattr(utility, "_registered_capabilities", lambda: ["with_door", "no_door"])
    monkeypatch.setattr(utility, "_door_names", lambda: {"with_door"})
    for attr, val in (("instance_id", "inst_test"), ("utility_grace_days", 14),
                      ("utility_capability_grace_days", 0), ("utility_pinned", "")):
        monkeypatch.setattr(utility.Config, attr, val, raising=False)
    await _seed(brain, accion="x", despertar_por="telegram", when=_iso(60))
    docket = {r["capability"] for r in await utility.find_dormant("inst_test")}
    assert docket == {"with_door"}


def test_the_door_set_is_derived_from_the_real_maps():
    import lhassa.actionpack._utility  # noqa: F401  (registers the shell-side sources)
    doors = utility._door_names()
    from lhassa.cli.capability_log import COMMAND_CAPABILITY

    assert set(COMMAND_CAPABILITY.values()) <= doors
    assert "run_command" in doors  # chat/code door, though it requires approval


@pytest.mark.asyncio
async def test_per_capability_grace_shields_a_newly_seen_capability(brain, caps, monkeypatch):
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_grace_days", 14, raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "", raising=False)
    monkeypatch.setattr(utility.Config, "utility_capability_grace_days", 30, raising=False)
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(60))  # old instance
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(0))  # alpha is active

    now = datetime.now(timezone.utc)
    # First sight: the unused ones get a mark and are NOT judged, however old the instance.
    assert await utility.find_dormant("inst_test", now=now) == []
    assert await brain.get_watch_state("inst_test", "utility:seen:gamma")
    # 29 days later still shielded; at 31 days they are judged.
    assert await utility.find_dormant("inst_test", now=now + timedelta(days=29)) == []
    later = await utility.find_dormant("inst_test", now=now + timedelta(days=31))
    # (alpha's own use has aged past the 30d dormant window by then: also judged.)
    assert {r["capability"] for r in later} == {"alpha", "beta", "gamma"}


def test_capability_grace_days_defaults_to_30():
    # (Not reloaded with the env var: reloading lhassa.config swaps the Config
    # class under every module that already imported it and corrupts other tests.)
    from lhassa.config import Config

    assert Config.utility_capability_grace_days == 30


@pytest.mark.asyncio
async def test_titles_follow_the_real_state_and_counts(brain, caps, monkeypatch):
    from lhassa.actionpack import _utility as action_mod

    await _sweep_env(monkeypatch, action_mod, ["alpha", "beta", "gamma"])
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(80))
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(70))
    await _seed(brain, accion="beta", despertar_por="task:beta", when=_iso(5))
    await action_mod.review_capabilities({"instance_id": "inst_test"})
    rows = {r["payload"]["capability"]: r["payload"]
            for r in await brain.load_pending_approvals("inst_test")}
    assert "only Lhassa" not in rows["alpha"]["title"]  # the owner used it 2x
    assert "you used it 2x" in rows["alpha"]["title"]
    assert "only Lhassa itself used it" in rows["beta"]["title"]
    assert rows["gamma"]["title"] == "retire 'gamma': never used by anyone"


@pytest.mark.asyncio
async def test_pending_cap_stops_the_tribunal(brain, monkeypatch):
    from lhassa.actionpack import _utility as action_mod

    names = [f"cap{i}" for i in range(8)]
    await _sweep_env(monkeypatch, action_mod, names)
    monkeypatch.setattr(action_mod.Config, "approval_pending_cap", 3, raising=False)
    await _seed(brain, accion="x", despertar_por="telegram", when=_iso(60))
    out = await action_mod.review_capabilities({"instance_id": "inst_test", "limit": 5})
    assert "3 proposed" in out  # room = cap - pending = 3
    out2 = await action_mod.review_capabilities({"instance_id": "inst_test", "limit": 5})
    assert "0 proposed" in out2 and "cap 3" in out2
    pend = await brain.load_pending_approvals("inst_test")
    assert len([a for a in pend if a["tipo"] == "retire_capability"]) == 3


@pytest.mark.asyncio
async def test_a_revived_capability_closes_its_approval_without_a_pin(brain, caps, monkeypatch):
    from lhassa.actionpack import _utility as action_mod

    await _sweep_env(monkeypatch, action_mod, ["alpha", "beta", "gamma"])
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(60))
    await action_mod.review_capabilities({"instance_id": "inst_test"})
    ap = [a for a in await brain.load_pending_approvals("inst_test")
          if a["payload"]["capability"] == "gamma"][0]
    # The owner uses gamma today.
    await _seed(brain, accion="gamma", despertar_por="telegram", when=_iso(0))
    out = await action_mod.review_capabilities({"instance_id": "inst_test"})
    assert "closed 1 revived" in out
    row = await brain.get_approval("inst_test", ap["id"])
    assert row["estado"] == "rechazada"
    assert row["payload"]["resolution"] == "rejected-by-revived"
    assert not await brain.get_watch_state("inst_test", "utility:pinned:gamma")
    assert "gamma" not in await utility.load_pinned("inst_test")


@pytest.mark.asyncio
async def test_a_capability_gone_from_the_registry_closes_its_approval(brain, monkeypatch):
    from lhassa.actionpack import _utility as action_mod

    await _sweep_env(monkeypatch, action_mod, ["alpha", "beta"])
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(60))
    await action_mod.review_capabilities({"instance_id": "inst_test"})
    ap = (await brain.load_pending_approvals("inst_test"))[0]
    assert ap["payload"]["capability"] == "beta"
    monkeypatch.setattr(utility, "_registered_capabilities", lambda: ["alpha"])
    out = await action_mod.review_capabilities({"instance_id": "inst_test"})
    assert "1 gone" in out
    row = await brain.get_approval("inst_test", ap["id"])
    assert row["payload"]["resolution"] == "rejected-by-gone"


@pytest.mark.asyncio
async def test_a_human_rejection_pins_and_queue_closures_do_not(brain, caps, monkeypatch):
    from lhassa.actionpack import _utility as action_mod
    from lhassa.security import approval

    await _sweep_env(monkeypatch, action_mod, ["alpha", "beta", "gamma"])
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(60))
    await action_mod.review_capabilities({"instance_id": "inst_test"})
    by_cap = {a["payload"]["capability"]: a["id"]
              for a in await brain.load_pending_approvals("inst_test")}

    # A queue closure (the cap/timeout path) leaves no pin.
    await brain.close_approval("inst_test", by_cap["beta"], "rejected-by-timeout")
    assert "beta" not in await utility.load_pinned("inst_test")
    # A human "no" pins it, and the tribunal never lists it again.
    await approval.resolve("inst_test", by_cap["gamma"], approved=False)
    assert "gamma" in await utility.load_pinned("inst_test")
    assert utility.pinned_capabilities(utility.Config, {"gamma"}) >= {"gamma"}
    docket = {r["capability"] for r in await utility.find_dormant("inst_test")}
    assert "gamma" not in docket


@pytest.mark.asyncio
async def test_env_pins_still_work_alongside_persisted_pins(brain, caps, monkeypatch):
    monkeypatch.setattr(utility.Config, "instance_id", "inst_test", raising=False)
    monkeypatch.setattr(utility.Config, "utility_pinned", "alpha", raising=False)
    await brain.set_watch_state("inst_test", "utility:pinned:beta", "1")
    assert await utility.load_pinned("inst_test") == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_approving_records_a_candidate_and_removes_nothing(brain, caps, monkeypatch):
    from lhassa.actionpack import _utility as action_mod
    from lhassa.security import approval

    await _sweep_env(monkeypatch, action_mod, ["alpha", "beta", "gamma"])
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(60))
    await _seed(brain, accion="alpha", despertar_por="telegram", when=_iso(0))
    await action_mod.review_capabilities({"instance_id": "inst_test"})
    ids = [a["id"] for a in await brain.load_pending_approvals("inst_test")]
    for ap_id in ids:
        await approval.resolve("inst_test", ap_id, approved=True)
    assert sorted(await utility.retired_candidates("inst_test")) == ["beta", "gamma"]
    # Approving twice never duplicates; and nothing was pinned.
    await utility.add_retired_candidate("inst_test", "beta")
    assert sorted(await utility.retired_candidates("inst_test")) == ["beta", "gamma"]
    assert await utility.load_pinned("inst_test") == set()


def test_utility_candidates_command_lists_the_recorded_candidates(tmp_path, monkeypatch):
    import asyncio
    import json

    from typer.testing import CliRunner

    import lhassa.core.db as db
    from lhassa.cli.utility_cmds import utility_app
    from lhassa.config import Config

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    asyncio.run(utility.add_retired_candidate(Config.instance_id, "zeta"))
    res = CliRunner().invoke(utility_app, ["candidates", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == ["zeta"]
    human = CliRunner().invoke(utility_app, ["candidates"])
    assert "zeta" in human.output and "nothing is removed" in human.output


# ── the brief is deterministic for self-proposals ─────────────────────────────


def test_retire_brief_is_rule_based_and_matches_the_data():
    from lhassa.security.approval import deterministic_brief

    unused = deterministic_brief("retire_capability", {"capability": "g", "state": "unused", "total_uses": 0})
    assert unused["recommendation"].startswith("Approve") and "no use by anyone" in unused["recommendation"]

    recent = deterministic_brief("retire_capability", {
        "capability": "a", "state": "dormant_owner", "owner_uses": 4, "self_uses": 0,
        "total_uses": 4, "days_since_owner_use": 40.0})
    assert recent["recommendation"].startswith("Reject") and "40 days ago" in recent["recommendation"]
    assert "4 by you" in recent["summary"]

    old = deterministic_brief("retire_capability", {
        "capability": "a", "state": "dormant_owner", "owner_uses": 1, "total_uses": 1,
        "days_since_owner_use": 200.0})
    assert old["recommendation"].startswith("Approve") and "200 days" in old["recommendation"]

    selfd = deterministic_brief("retire_capability", {
        "capability": "b", "state": "self_driven", "owner_uses": 0, "self_uses": 9, "total_uses": 9})
    assert "only Lhassa used it (9 times)" in selfd["recommendation"]


def test_mint_briefs_are_rule_based():
    from lhassa.security.approval import deterministic_brief

    good = deterministic_brief("mint_script", {"chain": ["a", "b"], "count": 6, "days_seen": 3})
    assert good["recommendation"].startswith("Approve")
    burst = deterministic_brief("mint_script", {"chain": ["a", "b"], "count": 6, "days_seen": 1})
    assert burst["recommendation"].startswith("Reject")
    mem = deterministic_brief("mint_memory", {"despertar_por": "telegram", "count": 5, "days_seen": 3, "answer": "x"})
    assert mem["recommendation"].startswith("Approve") and "Nothing is stored" in mem["summary"]
    assert deterministic_brief("mint_memory", {"count": 5, "days_seen": 3})["recommendation"].startswith("Reject")
    assert deterministic_brief("task", {}) is None


@pytest.mark.asyncio
async def test_request_approval_stamps_the_deterministic_brief_without_an_llm(brain, monkeypatch):
    import json
    import sqlite3

    from lhassa.llm.gateway import gateway
    from lhassa.security import approval

    async def _boom(*a, **k):
        raise AssertionError("the LLM must not be consulted for a retire_capability brief")

    monkeypatch.setattr(gateway, "complete", _boom)
    ap_id = await approval.request_approval(
        "inst_test", tipo="retire_capability",
        payload={"accion": "retire_capability", "capability": "g", "state": "unused", "total_uses": 0},
    )
    con = sqlite3.connect(brain.DB_PATH)
    raw = con.execute("SELECT brief FROM approvals WHERE id = ?", (ap_id,)).fetchone()[0]
    con.close()
    assert json.loads(raw)["recommendation"].startswith("Approve")


def test_describe_texts_no_longer_promise_what_approval_does_not_do():
    from lhassa.security.approval import describe

    for tipo, payload in (
        ("retire_capability", {"capability": "x", "state": "unused"}),
        ("mint_script", {"chain": ["a", "b"], "count": 3}),
        ("mint_memory", {"despertar_por": "t", "count": 3}),
    ):
        detail = describe({"tipo": tipo, "payload": payload})["detail"]
        assert "durable" not in detail and "stores a" not in detail
    assert "nothing is removed" in describe({"tipo": "retire_capability", "payload": {"capability": "x"}})["detail"]
    assert "no script is created or run" in describe({"tipo": "mint_script", "payload": {"chain": ["a", "b"]}})["detail"]
    assert "nothing is stored" in describe({"tipo": "mint_memory", "payload": {"despertar_por": "t"}})["detail"]


@pytest.mark.asyncio
async def test_the_briefs_route_fills_self_proposals_without_the_llm(brain, monkeypatch):
    """The dashboard's batch-brief endpoint used to send retire/mint rows to a
    cheap model that saw only the title; it now fills them by rules."""
    from lhassa.api import approval_routes
    from lhassa.config import Config
    from lhassa.llm.gateway import gateway

    async def _boom(*a, **k):
        raise AssertionError("no LLM call expected for self-proposals")

    monkeypatch.setattr(gateway, "complete", _boom)
    await brain.add_approval(
        Config.instance_id, "retire_capability",
        {"accion": "retire_capability", "capability": "g", "state": "unused", "total_uses": 0},
    )
    out = await approval_routes.get_approval_briefs()
    assert out[0]["recomendacion"].startswith("Approve")
    assert "retirement candidate" in out[0]["resumen"]
