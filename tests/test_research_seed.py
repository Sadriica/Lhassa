"""The research seed + the /idea door — the geshe's aid department. Offline."""

import asyncio

import pytest


@pytest.fixture
def org_env(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(tmp_path / "org.json"), raising=False)
    monkeypatch.setattr(config_mod.Config, "flows_file", str(tmp_path / "flows.json"), raising=False)
    return config_mod.Config


def test_seed_creates_aides_under_geshe_and_the_flow(org_env, tmp_path):
    from lhassa.flows.store import FlowStore
    from lhassa.org.temple import GESHE, GESHE_AIDES
    from lhassa.org.topology import load_topology
    from lhassa.seeds.research import seed_research

    out = seed_research(org_env)
    assert not out["errors"]
    assert set(out["aides_created"]) == set(GESHE_AIDES)
    topo = load_topology(org_env)
    for aide in GESHE_AIDES:
        assert topo.has(aide)
        assert aide in [c.id for c in topo.children_of(GESHE)]
    flow = FlowStore(org_env).get(out["flow_id"])
    assert flow is not None
    kinds = [n.kind.value if hasattr(n.kind, "value") else str(n.kind) for n in flow.nodes]
    assert kinds.count("task") == 4
    assert "gate" in kinds and "output" in kinds
    # Idempotent second pass.
    again = seed_research(org_env)
    assert set(again["aides_existing"]) == set(GESHE_AIDES)
    assert again["flow_created"] is False


def test_idea_command_acks_and_schedules_background_run(monkeypatch):
    from lhassa.notify.commands import COMMANDS

    ran = []

    async def fake_run(idea, *, config):
        ran.append(idea)

    import lhassa.notify.commands as commands_mod

    monkeypatch.setattr(commands_mod, "_run_idea_flow", fake_run)

    async def _go():
        reply = await COMMANDS.get("idea")("TOON for prompts")
        await asyncio.sleep(0)  # let the created task run
        return reply

    reply = asyncio.run(_go())
    assert "geshe" in reply.lower()
    assert ran == ["TOON for prompts"]


def test_idea_without_text_is_usage():
    from lhassa.notify.commands import COMMANDS

    reply = asyncio.run(COMMANDS.get("idea")(""))
    assert "Usage" in reply
