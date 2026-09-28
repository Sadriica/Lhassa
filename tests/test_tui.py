"""``lhassa tui``: the Textual dashboard. Fully offline: a tmp DB, direct
``lhassa.core.db`` reads (mirroring the CLI seam), and approve/deny resolved
through the SAME ``lhassa.security.approval.resolve`` seam the CLI/API use.

Uses Textual's official test harness (``app.run_test()``/pilot). Each screen
must mount cleanly against an EMPTY DB (no crash on no data); that's the
main risk surface for a read-model UI.
"""

from __future__ import annotations

import asyncio

import pytest

INSTANCE = "inst_personal"


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    asyncio.run(db.init_db())
    return db


@pytest.mark.asyncio
async def test_app_mounts_on_empty_db(brain):
    from lhassa.cli.tui.app import LhassaTui

    app = LhassaTui(instance_id=INSTANCE)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#ov-instance") is not None


@pytest.mark.asyncio
async def test_overview_switches_tabs_without_error(brain):
    from lhassa.cli.tui.app import LhassaTui

    app = LhassaTui(instance_id=INSTANCE)
    async with app.run_test() as pilot:
        await pilot.pause()
        for tab_id in ("projects", "runs", "approvals"):
            app.query_one("TabbedContent").active = tab_id
            await pilot.pause()
        # Reaching here without an exception is the assertion: every screen
        # composed and mounted cleanly against an empty DB.


@pytest.mark.asyncio
async def test_projects_screen_lists_project_row(brain, tmp_path):
    from lhassa.cli.tui.app import LhassaTui
    from lhassa.core import projects

    await projects.create_project(INSTANCE, "lhassa", kind="code", path=str(tmp_path))

    app = LhassaTui(instance_id=INSTANCE)
    async with app.run_test() as pilot:
        app.query_one("TabbedContent").active = "projects"
        await pilot.pause()
        from textual.widgets import DataTable

        table = app.query_one("#projects-table", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_runs_screen_lists_run_row(brain):
    from lhassa.cli.tui.app import LhassaTui

    await brain.record_run(
        {
            "id": "run_test1",
            "instance_id": INSTANCE,
            "kind": "agent",
            "entity_id": "project:demo",
            "status": "completed",
            "cost_usd": 0.01,
        }
    )

    app = LhassaTui(instance_id=INSTANCE)
    async with app.run_test() as pilot:
        app.query_one("TabbedContent").active = "runs"
        await pilot.pause()
        from textual.widgets import DataTable

        table = app.query_one("#runs-table", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_approvals_screen_renders_via_describe(brain):
    from lhassa.cli.tui.app import LhassaTui

    await brain.add_approval(
        INSTANCE,
        "mint_action",
        {"accion": "sync_repo", "actor_id": "lhassa", "candidate_accion": "sync_repo"},
        approval_id="ap_test1",
    )

    app = LhassaTui(instance_id=INSTANCE)
    async with app.run_test() as pilot:
        app.query_one("TabbedContent").active = "approvals"
        await pilot.pause()
        from textual.widgets import DataTable

        table = app.query_one("#approvals-table", DataTable)
        assert table.row_count == 1
        # describe() renders a real title, never a bare id.
        row = table.get_row_at(0)
        assert row[0] == "ap_test1"
        assert "sync_repo" in row[1] or "automatic" in row[1]


@pytest.mark.asyncio
async def test_no_loguru_sink_targets_stderr_while_mounted(brain):
    """Regression for the log-leak bug: while the TUI owns the screen, nothing
    may write to stderr; a live stderr sink paints warnings over the running
    UI. ``on_mount`` must swap every sink for one that writes to the app's own
    (off-screen) log, and ``on_unmount`` must restore a stderr sink afterward
    so a plain CLI session behaves normally again."""
    from loguru import logger as _loguru
    from lhassa.cli.tui.app import LhassaTui

    def _handler_ids() -> set:
        return set(getattr(_loguru._core, "handlers", {}).keys())

    def _targets_real_stream(handler) -> bool:
        """True if this handler writes straight to a stream object (as opposed
        to a callable sink resolving its stream at write time)."""
        return getattr(getattr(handler, "_sink", None), "_stream", None) is not None

    app = LhassaTui(instance_id=INSTANCE)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Mounted: no handler is bound to a live stream object at all; every
        # sink is either the app's own log or nothing.
        handlers = getattr(_loguru._core, "handlers", {})
        assert not any(_targets_real_stream(h) for h in handlers.values())

    # After the app tears down, a sink is restored (normal CLI behavior);
    # there is at least one handler again where there was none live before.
    assert _handler_ids() != set()


@pytest.mark.asyncio
async def test_approvals_screen_approve_key_resolves(brain):
    from lhassa.cli.tui.app import LhassaTui

    await brain.add_approval(
        INSTANCE,
        "mint_action",
        {"accion": "sync_repo", "actor_id": "lhassa", "candidate_accion": "sync_repo"},
        approval_id="ap_test2",
    )

    app = LhassaTui(instance_id=INSTANCE)
    async with app.run_test() as pilot:
        app.query_one("TabbedContent").active = "approvals"
        await pilot.pause()
        from textual.widgets import DataTable

        table = app.query_one("#approvals-table", DataTable)
        table.focus()
        table.cursor_coordinate = (0, 0)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

    pending = await brain.load_pending_approvals(INSTANCE)
    assert pending == []
    resolved = await brain.get_approval(INSTANCE, "ap_test2")
    assert resolved["estado"] == "aprobada"


async def test_overview_rows_actually_have_height():
    """A card's label/value rows must render, not collapse to zero rows.

    Textual subtracts padding from a declared height, so `height: 1` plus
    `padding-bottom: 1` left zero rows of content: the Overview cards showed
    their titles and nothing else. Spacing there must be margin. Automated
    tests passed while the screen was visibly empty; this is the assertion
    that would have caught it.
    """
    from lhassa.cli.tui.app import LhassaTui

    app = LhassaTui()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        labels = app.query(".ov-label")
        values = app.query(".ov-value")
        assert labels and values, "Overview rendered no label/value rows"
        assert all(w.size.height >= 1 for w in labels), "labels collapsed to zero height"
        assert all(w.size.height >= 1 for w in values), "values collapsed to zero height"
