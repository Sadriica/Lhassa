# SPDX-License-Identifier: AGPL-3.0-or-later
"""Subcommands must leave a trace the utility tribunal can read.

``agents/utility.py`` decides whether a capability is still earning its place by
grouping the episode log on ``accion``. Typing ``lhassa memory add`` writes a
fact and used to leave nothing there, so the owner's most direct use of a
capability was also the one the tribunal could not see; it read every capability
as never once owner-driven and began proposing to retire them.
"""

from __future__ import annotations

import asyncio

import pytest


def test_only_real_capabilities_are_mapped():
    """A typo here would log a capability the tribunal can never survey."""
    from lhassa import actionpack
    from lhassa.cli import capability_log
    from lhassa.core.actions import ACTIONS

    actionpack.register_all()
    unknown = sorted(set(capability_log.COMMAND_CAPABILITY.values()) - set(ACTIONS))
    assert not unknown, f"mapped to actions that do not exist: {unknown}"


def test_an_unmapped_command_records_nothing():
    """Reads exercise no capability; inventing a row would corrupt the ledger."""
    from lhassa.cli import capability_log

    assert capability_log.capability_for("status") is None
    assert capability_log.capability_for("") is None
    assert capability_log.capability_for("memory add") == "add_fact"


def test_record_appends_one_owner_bucketed_episode(monkeypatch, tmp_path):
    from lhassa.agents import utility
    from lhassa.cli import capability_log
    from lhassa.core.episode import Episode

    appended: list = []

    async def _fake_append(self):
        appended.append(self)
        return self.id

    monkeypatch.setattr(Episode, "append", _fake_append)
    asyncio.run(capability_log.record("add_fact", "memory add", instance_id="inst_x"))

    assert len(appended) == 1
    ep = appended[0]
    assert ep.accion == "add_fact"          # the column the tribunal groups on
    assert ep.costo_usd == 0.0              # accounting must never cost anything
    # The whole point: this bucket has to read as the owner, not as plumbing.
    assert utility.classify_wake(ep.despertar_por) == utility.WAKE_OWNER


def test_a_logging_failure_never_breaks_the_command(monkeypatch):
    from lhassa.cli import capability_log
    from lhassa.core.episode import Episode

    async def _boom(self):
        raise RuntimeError("db is locked")

    monkeypatch.setattr(Episode, "append", _boom)
    asyncio.run(capability_log.record("add_fact", "memory add"))  # must not raise


def test_record_sync_is_a_no_op_inside_a_running_loop(monkeypatch):
    """The async doors log their own uses and must not be re-entered."""
    from lhassa.cli import capability_log
    from lhassa.core.episode import Episode

    def _boom(self):
        raise AssertionError("must not log from inside a running loop")

    monkeypatch.setattr(Episode, "append", _boom)

    async def _inside():
        capability_log.record_sync("memory add")

    asyncio.run(_inside())


@pytest.mark.parametrize(
    "module,command",
    [
        ("lhassa.cli.memory_cmds", "memory add"),
        ("lhassa.cli.task_cmds", "task add"),
        ("lhassa.cli.knowledge_cmds", "knowledge ingest"),
        ("lhassa.cli.project_cmds", "project create"),
        ("lhassa.cli.config_cmds", "config set"),
    ],
)
def test_the_write_commands_are_actually_wired(module, command):
    """The map is inert unless each command calls it; assert the call site."""
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(module))
    assert f'record_sync("{command}")' in source, f"{module} never records {command!r}"
