"""Tests for lhassa.org.monitor; the environment monitor & improvement supervisor.

Offline + $0: signals are read from a tmp brain DB / tmp topology / tmp skills, and
the live reasoning path (LHASSA_ORG_LIVE) is left OFF so NO model is ever called.
"""

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio  # noqa: F401


async def _init_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


def _cfg(tmp_path):
    """A stub config pointing every source at a tmp dir (real stores, no model)."""
    return SimpleNamespace(
        instance_id="inst_personal",
        org_topology_file=str(tmp_path / "org.json"),
        flows_file=str(tmp_path / "flows.json"),
        skills_dir=str(tmp_path / "skills"),
        health_dir=str(tmp_path / "health"),
        root_admin_id="lhassa",
        context_token_budget=2000,
        memory_layer4_enabled=False,
    )


# ── gather_signals: keys + tolerance of empty/missing sources ─────────────────────


@pytest.mark.asyncio
async def test_gather_signals_keys_and_tolerates_empty(tmp_path, monkeypatch):
    await _init_db(tmp_path, monkeypatch)
    from lhassa.org.monitor import gather_signals

    signals = gather_signals(_cfg(tmp_path), instance_id="inst_personal")

    for key in ("episodes", "health", "flows", "approvals", "failures", "instance_id"):
        assert key in signals
    # empty environment → every collection is empty, health is a clean info digest
    assert signals["episodes"] == []
    assert signals["approvals"] == []
    assert signals["flows"] == []
    assert signals["failures"] == []
    assert signals["health"]["worst_level"] == "info"
    assert signals["health"]["ok"] is True


def test_gather_signals_no_db_degrades(tmp_path, monkeypatch):
    """A missing brain DB (never initialised) degrades to empty, never raises."""
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "does-not-exist.db")
    from lhassa.org.monitor import gather_signals

    signals = gather_signals(_cfg(tmp_path))
    assert signals["episodes"] == []
    assert signals["approvals"] == []


@pytest.mark.asyncio
async def test_gather_signals_picks_up_episodes_failures_and_approvals(
    tmp_path, monkeypatch
):
    db = await _init_db(tmp_path, monkeypatch)
    from lhassa.core.episode import Episode
    from lhassa.org.monitor import gather_signals

    ok = Episode.begin("org", "inst_personal").finish(
        decision="ok", accion="dept:finance think", resultado="all good"
    )
    await ok.append()
    bad = Episode.begin("org", "inst_personal").finish(
        decision="org: error", accion="dept:optimization error", resultado="it errored"
    )
    await bad.append()
    await db.add_approval("inst_personal", "deploy", {"accion": "deploy"})

    signals = gather_signals(_cfg(tmp_path), instance_id="inst_personal")
    assert len(signals["episodes"]) == 2
    assert len(signals["approvals"]) == 1
    # the failing episode is detected and its department extracted from "dept:X"
    assert len(signals["failures"]) == 1
    assert signals["failures"][0]["department"] == "optimization"


@pytest.mark.asyncio
async def test_failures_ignore_the_monitors_own_episodes(tmp_path, monkeypatch):
    """The watcher's own episodes quote failure counts; counting them would have
    the monitor alerting on its own reflection, forever."""
    await _init_db(tmp_path, monkeypatch)
    from lhassa.core.episode import Episode
    from lhassa.org.monitor import gather_signals

    watch = Episode.begin("monitor", "inst_personal").finish(
        decision="monitor: watch", resultado="1 event(s): 2 new failure(s) (total 5)"
    )
    await watch.append()
    offline = Episode.begin("monitor", "inst_personal").finish(
        decision="monitor: offline", resultado="2 risk(s), 2 improvement(s)"
    )
    await offline.append()

    signals = gather_signals(_cfg(tmp_path), instance_id="inst_personal")
    assert len(signals["episodes"]) == 2
    assert signals["failures"] == []


@pytest.mark.asyncio
async def test_failures_ignore_prose_in_a_completed_think(tmp_path, monkeypatch):
    """A successful think's resultado is model prose; "error"/"fail" in the TEXT is
    content, not outcome. A think that failed marks it in the decision itself."""
    await _init_db(tmp_path, monkeypatch)
    from lhassa.core.episode import Episode
    from lhassa.org.monitor import gather_signals

    prose = Episode.begin("org", "inst_personal").finish(
        decision="think: provider=default model=m",
        resultado="Rejection risks + fixes: the CV may fail ATS keyword error checks…",
    )
    await prose.append()
    failed = Episode.begin("telegram", "inst_personal").finish(
        decision="telegram: error", resultado="error: AttributeError: …"
    )
    await failed.append()

    signals = gather_signals(_cfg(tmp_path), instance_id="inst_personal")
    assert len(signals["failures"]) == 1
    assert signals["failures"][0]["decision"] == "telegram: error"


@pytest.mark.asyncio
async def test_failures_only_count_while_fresh(tmp_path, monkeypatch):
    """A failure older than monitor_failure_window_h ages OUT of the signal; the
    alert's "total N" means "N current", not "N ever seen in the window"."""
    from datetime import datetime, timedelta, timezone

    db = await _init_db(tmp_path, monkeypatch)
    from lhassa.core.episode import Episode
    from lhassa.org.monitor import gather_signals

    stale = Episode.begin("org", "inst_personal").finish(
        decision="org: error", resultado="it errored days ago"
    )
    stale.creado_en = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat()
    await stale.append()
    fresh = Episode.begin("org", "inst_personal").finish(
        decision="org: error", resultado="it errored just now"
    )
    await fresh.append()

    cfg = _cfg(tmp_path)
    cfg.monitor_failure_window_h = 24.0
    signals = gather_signals(cfg, instance_id="inst_personal")
    assert len(signals["failures"]) == 1
    assert signals["failures"][0]["resultado"] == "it errored just now"

    # window 0 → no age cutoff: both count again
    cfg.monitor_failure_window_h = 0.0
    signals = gather_signals(cfg, instance_id="inst_personal")
    assert len(signals["failures"]) == 2
    _ = db  # keep the tmp DB alive through the reads


@pytest.mark.asyncio
async def test_gather_signals_reads_health_and_flows(tmp_path, monkeypatch):
    await _init_db(tmp_path, monkeypatch)
    from lhassa.org.monitor import gather_signals

    # a health digest snapshot with a failing check
    health_dir = tmp_path / "health"
    health_dir.mkdir(parents=True)
    (health_dir / "digest.json").write_text(
        json.dumps(
            {
                "worst_level": "warn",
                "ok": False,
                "results": {"disk": {"level": "warn"}, "mem_pressure": {"level": "info"}},
                "alerted": ["disk"],
            }
        )
    )
    # one stored, valid flow: trigger → output
    from lhassa.flows.model import Flow, FlowEdge, FlowNode
    from lhassa.flows.store import FlowStore

    cfg = _cfg(tmp_path)
    flow = Flow(
        id="flow-x",
        name="demo",
        nodes=[FlowNode(id="t", kind="trigger"), FlowNode(id="o", kind="output")],
        edges=[FlowEdge(from_node="t", to_node="o")],
    )
    FlowStore(cfg).save(flow)

    signals = gather_signals(cfg, instance_id="inst_personal")
    assert signals["health"]["worst_level"] == "warn"
    assert signals["health"]["failing"] == ["disk"]
    assert any(f["id"] == "flow-x" for f in signals["flows"])


# ── monitor_pass: offline structured report, episode, no model call ───────────────


@pytest.mark.asyncio
async def test_monitor_pass_offline_structured_report_no_model(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)  # offline

    # prove the model path is NOT taken: make brain_executor explode if called
    import lhassa.org.executor as ex

    async def _boom(*a, **k):  # pragma: no cover - must never run offline
        raise AssertionError("brain_executor must not be called on an offline pass")

    monkeypatch.setattr(ex, "brain_executor", _boom)

    from lhassa.org.monitor import monitor_pass

    report = await monitor_pass(department="interns", config=_cfg(tmp_path))

    # structured shape
    for key in ("observations", "risks", "improvements", "delegations"):
        assert key in report and isinstance(report[key], list)
    assert report["department"] == "interns"
    assert report["live"] is False
    assert report["model"] == "offline"
    assert report["cost_usd"] == 0.0

    # a monitor episode was logged
    eps = await db.load_recent_episodes("inst_personal")
    assert any(e["despertar_por"] == "monitor" for e in eps)


@pytest.mark.asyncio
async def test_monitor_pass_offline_surfaces_risks_from_signals(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)
    from lhassa.core.episode import Episode
    from lhassa.org.monitor import monitor_pass

    bad = Episode.begin("org", "inst_personal").finish(
        decision="org: error", accion="dept:finance error", resultado="boom failed"
    )
    await bad.append()
    await db.add_approval("inst_personal", "deploy", {"accion": "deploy"})

    report = await monitor_pass(department="interns", config=_cfg(tmp_path))
    assert report["risks"], "failures + approvals should surface at least one risk"
    assert report["improvements"]
    # each surfaced risk maps to at least one delegation routing the fix somewhere
    assert report["delegations"]
    depts = {d["department"] for d in report["delegations"]}
    assert "finance" in depts  # the failing department is routed the fix


@pytest.mark.asyncio
async def test_monitor_pass_never_raises_on_error(tmp_path, monkeypatch):
    await _init_db(tmp_path, monkeypatch)
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)
    import lhassa.org.monitor as mon

    # force gather_signals to explode mid-pass; monitor_pass must degrade, not raise
    def _boom(*a, **k):
        raise RuntimeError("signal source blew up")

    monkeypatch.setattr(mon, "gather_signals", _boom)

    report = await mon.monitor_pass(department="interns", config=_cfg(tmp_path))
    assert "error" in report
    assert report["department"] == "interns"
    # still a structured report
    for key in ("observations", "risks", "improvements", "delegations"):
        assert key in report


# ── the monitor skill + reusable "make brain a monitor" wiring ────────────────────


def _seed_interns_topology(tmp_path):
    (tmp_path / "org.json").write_text(
        json.dumps(
            [
                {"id": "lhassa", "role": "administrator", "parent": None},
                {"id": "general", "role": "general", "parent": "lhassa"},
                {
                    "id": "interns",
                    "role": "department",
                    "department": "interns",
                    "parent": "general",
                    "skills": ["research"],
                },
            ]
        )
    )


def test_ensure_monitor_skill_creates_and_is_idempotent(tmp_path):
    from lhassa.org.monitor import MONITOR_SKILL_NAME, ensure_monitor_skill

    cfg = _cfg(tmp_path)
    skill = ensure_monitor_skill(cfg)
    assert skill.name == MONITOR_SKILL_NAME
    assert "supervisor" in skill.instructions.lower()
    # idempotent; a second call returns the same installed skill, no error
    again = ensure_monitor_skill(cfg)
    assert again.name == MONITOR_SKILL_NAME
    assert again.error is None


def test_make_brain_monitor_assigns_skill_and_updates_role(tmp_path):
    _seed_interns_topology(tmp_path)
    from lhassa.org.monitor import MONITOR_ROLE, MONITOR_SKILL_NAME, make_brain_monitor
    from lhassa.org.topology import load_topology
    from lhassa.skills.store import is_installed

    cfg = _cfg(tmp_path)
    result = make_brain_monitor("interns", config=cfg)
    assert result["ok"] is True

    # the skill exists in the store and is assigned to interns
    assert is_installed(MONITOR_SKILL_NAME, cfg)
    node = load_topology(cfg).node("interns")
    assert MONITOR_SKILL_NAME in node.skills
    assert "research" in node.skills  # existing skill preserved (merge, not replace)
    assert node.role == MONITOR_ROLE
    assert node.limits.get("description")


def test_make_brain_monitor_is_reusable_for_any_brain(tmp_path):
    """Not hard-coded to interns; any existing brain can be made the monitor."""
    (tmp_path / "org.json").write_text(
        json.dumps(
            [
                {"id": "lhassa", "role": "administrator", "parent": None},
                {"id": "general", "role": "general", "parent": "lhassa"},
            ]
        )
    )
    from lhassa.org.monitor import MONITOR_SKILL_NAME, make_brain_monitor
    from lhassa.org.topology import load_topology

    cfg = _cfg(tmp_path)
    result = make_brain_monitor("general", config=cfg)
    assert result["ok"] is True
    assert MONITOR_SKILL_NAME in load_topology(cfg).node("general").skills


def test_make_brain_monitor_noops_for_missing_brain(tmp_path):
    _seed_interns_topology(tmp_path)
    from lhassa.org.monitor import make_brain_monitor
    from lhassa.org.topology import load_topology

    cfg = _cfg(tmp_path)
    result = make_brain_monitor("nope-not-here", config=cfg)
    assert result["ok"] is False
    assert "nope-not-here" in result["reason"]
    # nothing was created for the phantom brain
    assert not load_topology(cfg).has("nope-not-here")


# ── CLI: `lhassa org monitor` runs and prints ─────────────────────────────────────


def test_cli_org_monitor_runs_and_prints(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    import lhassa.core.db as db
    from typer.testing import CliRunner

    from lhassa.cli.app import app

    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(tmp_path / "org.json"))
    monkeypatch.setattr(config_mod.Config, "flows_file", str(tmp_path / "flows.json"))
    monkeypatch.setattr(config_mod.Config, "skills_dir", str(tmp_path / "skills"))
    monkeypatch.setattr(config_mod.Config, "health_dir", str(tmp_path / "health"), raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)

    result = CliRunner().invoke(app, ["org", "monitor"])
    assert result.exit_code == 0, result.output
    assert "Monitor" in result.output
    assert "LHASSA_ORG_LIVE" in result.output  # offline hint shown


def test_cli_org_monitor_json(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    import lhassa.core.db as db
    from typer.testing import CliRunner

    from lhassa.cli.app import app

    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(tmp_path / "org.json"))
    monkeypatch.setattr(config_mod.Config, "flows_file", str(tmp_path / "flows.json"))
    monkeypatch.setattr(config_mod.Config, "skills_dir", str(tmp_path / "skills"))
    monkeypatch.setattr(config_mod.Config, "health_dir", str(tmp_path / "health"), raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)

    result = CliRunner().invoke(app, ["org", "monitor", "--json", "--department", "interns"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["department"] == "interns"
    for key in ("observations", "risks", "improvements", "delegations"):
        assert key in payload
