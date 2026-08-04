"""The grown ``lhassa tui``: four new screens, a command palette, ``/`` filtering,
live refresh and the keyboard-help overlay.

Same virtues as ``tests/test_tui.py``: fully offline (tmp DB / tmp stores), every
screen must mount against an EMPTY brain without crashing, and the only mutations
cross the real seams (``security/approval.py:resolve``, ``org/delegate.py:
delegate_flow``).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import DataTable, Input

INSTANCE = "inst_personal"


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    monkeypatch.setattr(config_mod.Config, "flows_file", tmp_path / "flows.json")
    monkeypatch.setattr(config_mod.Config, "skills_dir", tmp_path / "skills")
    monkeypatch.setattr(
        config_mod.Config, "org_topology_file", tmp_path / "topology.json"
    )
    asyncio.run(db.init_db())
    return db


def _app():
    from lhassa.cli.tui.app import LhassaTui

    return LhassaTui(instance_id=INSTANCE)


# ── every screen mounts on an empty brain ────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_tab_mounts_on_an_empty_brain(brain):
    from textual.widgets import TabbedContent

    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        for tab_id, _label in app.TABS:
            app.query_one(TabbedContent).active = tab_id
            await pilot.pause()
        # No exception == every one of the eight screens composed cleanly.
        assert len(app.TABS) == 8


# ── the command palette ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_palette_offers_one_entry_per_tab_plus_the_app_actions(brain):
    from lhassa.cli.tui.palette import LhassaCommands

    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = LhassaCommands(app.screen)
        hits = [hit async for hit in provider.search("Go to")]
        assert len(hits) == len(app.TABS)
        discovered = [hit async for hit in provider.discover()]
        assert len(discovered) == len(app.TABS) + 3  # help / refresh / quit


@pytest.mark.asyncio
async def test_palette_navigation_switches_the_tab(brain):
    from textual.widgets import TabbedContent

    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.switch_tab("memory")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "memory"


@pytest.mark.asyncio
async def test_switch_tab_ignores_an_unknown_id(brain):
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.switch_tab("does-not-exist")  # must not raise
        await pilot.pause()


# ── the keyboard-help overlay ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_help_overlay_opens_and_closes(brain):
    from lhassa.cli.tui.screens.help import HelpScreen

    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_help_overlay_never_stacks(brain):
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        depth = len(app.screen_stack)
        app.action_help()
        await pilot.pause()
        app.action_help()
        await pilot.pause()
        assert len(app.screen_stack) == depth + 1


# ── the `/` filter ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_narrows_the_rows(brain):
    from textual.widgets import TabbedContent

    await brain.record_run(
        {"id": "run_alpha", "instance_id": INSTANCE, "kind": "agent", "status": "completed"}
    )
    await brain.record_run(
        {"id": "run_beta", "instance_id": INSTANCE, "kind": "flow", "status": "failed"}
    )

    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "runs"
        await pilot.pause()
        table = app.query_one("#runs-table", DataTable)
        assert table.row_count == 2

        from lhassa.cli.tui.screens.runs import RunsScreen

        screen = app.query_one(RunsScreen)
        screen.action_focus_filter()
        app.query_one("#runs-table-filter", Input).value = "alpha"
        await pilot.pause()
        assert table.row_count == 1

        await screen.action_clear_filter()
        await pilot.pause()
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_filter_is_case_insensitive(brain):
    from textual.widgets import TabbedContent

    await brain.record_run(
        {"id": "run_ALPHA", "instance_id": INSTANCE, "kind": "agent", "status": "completed"}
    )
    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "runs"
        await pilot.pause()
        app.query_one("#runs-table-filter", Input).value = "alpha"
        await pilot.pause()
        assert app.query_one("#runs-table", DataTable).row_count == 1


# ── live refresh ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_picks_up_a_row_added_behind_the_ui(brain):
    from textual.widgets import TabbedContent

    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "runs"
        await pilot.pause()
        table = app.query_one("#runs-table", DataTable)
        assert table.row_count == 0

        await brain.record_run(
            {"id": "run_late", "instance_id": INSTANCE, "kind": "agent", "status": "running"}
        )
        await app.action_refresh_current()
        await pilot.pause()
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_refresh_is_a_no_op_when_nothing_changed(brain):
    from textual.widgets import TabbedContent

    await brain.record_run(
        {"id": "run_stable", "instance_id": INSTANCE, "kind": "agent", "status": "completed"}
    )
    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "runs"
        await pilot.pause()
        from lhassa.cli.tui.screens.runs import RunsScreen

        screen = app.query_one(RunsScreen)
        before = screen._signature
        await screen.action_refresh()
        assert screen._signature is before  # same object → the redraw was skipped


# ── Flows screen ─────────────────────────────────────────────────────────────────


def _write_flow(tmp_path, flow_id="demo"):
    payload = {
        flow_id: {
            "id": flow_id,
            "name": "Demo flow",
            "description": "a demo",
            "nodes": [{"id": "trigger", "kind": "trigger", "label": "in"}],
            "edges": [],
        }
    }
    (tmp_path / "flows.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_flows_screen_lists_stored_flows(brain, tmp_path):
    from textual.widgets import TabbedContent

    _write_flow(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "flows"
        await pilot.pause()
        table = app.query_one("#flows-table", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "demo"


@pytest.mark.asyncio
async def test_flows_screen_runs_through_delegate_flow(brain, tmp_path, monkeypatch):
    from textual.widgets import TabbedContent

    _write_flow(tmp_path)
    seen = {}

    async def fake_delegate(flow, **kwargs):
        seen["flow_id"] = flow.id
        seen["durable"] = kwargs.get("durable")
        return {"id": "run_from_tui", "status": "completed"}

    async def explode(*args, **kwargs):
        raise AssertionError("the TUI must not call run_flow directly")

    monkeypatch.setattr("lhassa.org.delegate.delegate_flow", fake_delegate)
    monkeypatch.setattr("lhassa.flows.engine.run_flow", explode)

    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "flows"
        await pilot.pause()
        table = app.query_one("#flows-table", DataTable)
        table.focus()
        table.cursor_coordinate = (0, 0)
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()

    assert seen == {"flow_id": "demo", "durable": True}


# ── Skills screen ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skills_screen_lists_a_stored_skill(brain, tmp_path):
    from textual.widgets import TabbedContent

    from lhassa.skills.skillmd import Skill
    from lhassa.skills.store import SkillStore

    SkillStore().save(
        Skill(name="research-method", description="How to research", instructions="…")
    )

    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "skills"
        await pilot.pause()
        table = app.query_one("#skills-table", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "research-method"


# ── Memory screen ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_screen_lists_facts(brain):
    from textual.widgets import TabbedContent

    from lhassa.memory import facts

    await facts.add_fact(INSTANCE, "dato", "the owner drinks tea", tags=["habit"])

    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "memory"
        await pilot.pause()
        table = app.query_one("#memory-table", DataTable)
        assert table.row_count == 1
        assert "tea" in table.get_row_at(0)[1]


@pytest.mark.asyncio
async def test_memory_filter_also_drives_episodic_recall(brain):
    from textual.widgets import TabbedContent

    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "memory"
        await pilot.pause()
        app.query_one("#memory-table-filter", Input).value = "anything"
        await pilot.pause()
        # The recall table exists and stayed empty on an empty log — never crashed.
        assert app.query_one("#memory-recall-table", DataTable).row_count == 0


# ── Org screen ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_org_screen_renders_the_chart(brain, tmp_path):
    from textual.widgets import TabbedContent

    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "org"
        await pilot.pause()
        table = app.query_one("#org-table", DataTable)
        # A fresh brain still has the root administrator in its default chart.
        assert table.row_count >= 1
        assert "lhassa" in table.get_row_at(0)[0]


# ── approvals keep resolving through the real seam ───────────────────────────────


@pytest.mark.asyncio
async def test_approvals_still_resolve_through_the_approval_seam(brain):
    from textual.widgets import TabbedContent

    await brain.add_approval(
        INSTANCE,
        "mint_action",
        {"accion": "sync_repo", "actor_id": "lhassa", "candidate_accion": "sync_repo"},
        approval_id="ap_new1",
    )

    app = _app()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "approvals"
        await pilot.pause()
        table = app.query_one("#approvals-table", DataTable)
        table.focus()
        table.cursor_coordinate = (0, 0)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

    assert await brain.load_pending_approvals(INSTANCE) == []
