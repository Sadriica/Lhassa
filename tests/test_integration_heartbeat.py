# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Real, end-to-end integration tests for the heartbeat/step loop.

Why this file exists: the suite this ships alongside has thousands of unit
tests, and every one of them mocks the provider, the DB, or both (see
``tests/test_step.py``'s ``FakeProvider``). That is deliberate and correct for
unit coverage; but it means the actual wiring between ``core/heartbeat.py``,
``core/step.py``, the signed-task allowlist gate (``security/signing.py`` +
``security/allowlist.py``) and a REAL provider subprocess has never been
exercised together in CI. An Ed25519 signing incident (``doc/decisions.md``)
shipped and broke that exact seam without a single test failing, because every
signature-adjacent unit test injects a already-signed task or a fake verifier.

These tests close that gap. They are marked ``integration`` and excluded from
the default run (see ``pyproject.toml``'s ``addopts``) because test #1 spends
real tokens against a real, locally-installed CLI (``opencode`` / ``claude`` /
``codex``: whichever is first on ``PATH``) and can take tens of seconds. Run
them explicitly with::

    make test-integration
    # or
    uv run --extra dev python -m pytest -m integration -q

Both tests still isolate the DB (a fresh ``tmp_path`` file, monkeypatched onto
``lhassa.core.db.DB_PATH`` the same way ``tests/test_step.py`` does) and the
signing keypair (monkeypatched onto ``lhassa.security.signing.KEY_PATH``) so
nothing here touches the owner's real ``~/.lhassa``. What is NOT mocked is the
thing that actually broke: the signed-task envelope round-tripping through the
DB and the allowlist gate, and (test #1 only) a real provider process.

Test #1 (``test_heartbeat_judgment_beat_with_live_cli``) mints one cheap,
signed judgment task, runs exactly ONE real ``step()`` with that live CLI
injected as the provider, and asserts the tick did not crash, wrote an
episode with a non-empty ``resultado``, and recorded a cost (``costo_usd``,
deliberately ``0.0`` for a subscription CLI by design; see
``ClaudeCodeProvider.complete``'s docstring; but always a real float, never
``None``). Skips with an honest reason if no CLI is on ``PATH``.

Test #2 (``test_heartbeat_empty_beat_fresh_db``) is the $0 counterpart: a
brand-new process opening a brand-new tmp DB file, migrating it to the
current schema, and completing a full ``beat()`` with nothing on the
watchlist; the deterministic path every real heartbeat tick takes before
(and usually instead of) the judgment branch. No provider, no DB mock, no
policy mock; just the real migration + real ``step()`` on an empty brain.
"""

import shutil

import pytest

pytestmark = pytest.mark.integration

# Priority order: free/local-friendly CLI first, then the owner's subscription
# CLIs. Matches the fallback chain documented in doc/configuration.md
# ("opencode -> claude -> codex -> cloud").
_CLI_PRIORITY = ("opencode", "claude", "codex")


def _detect_live_cli() -> str | None:
    """First CLI from ``_CLI_PRIORITY`` found on PATH, else None."""
    for name in _CLI_PRIORITY:
        if shutil.which(name):
            return name
    return None


async def _isolated_db(tmp_path, monkeypatch):
    """Point the brain DB and the signing keypair at tmp_path, then init.

    Same isolation pattern as ``tests/test_step.py:_init`` (DB) and
    ``tests/test_action_preconditions.py`` (signing key); reused here rather
    than re-derived so this file drifts with the rest of the suite instead of
    against it.
    """
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "integration_brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    await db.init_db()
    return db


# ── test 1: one real judgment beat against a live CLI provider ──────────────


@pytest.mark.asyncio
async def test_heartbeat_judgment_beat_with_live_cli(tmp_path, monkeypatch):
    """One real ``step()`` tick, judgment branch, a real CLI provider.

    No provider mock: this is the one place in the suite that actually shells
    out. The task is deliberately trivial and cheap ("reply with exactly the
    word OK") to keep real spend near zero regardless of which CLI answers.
    """
    cli_name = _detect_live_cli()
    if cli_name is None:
        pytest.skip(
            "no live CLI (opencode/claude/codex) found on PATH; "
            "integration test needs at least one installed to run"
        )

    db = await _isolated_db(tmp_path, monkeypatch)
    from lhassa.config import Config
    from lhassa.core import step
    from lhassa.llm.provider import ClaudeCodeProvider, CodexProvider, OpenCodeProvider

    provider_classes = {
        "opencode": OpenCodeProvider,
        "claude": ClaudeCodeProvider,
        "codex": CodexProvider,
    }
    config = Config()
    provider = provider_classes[cli_name](config)

    instance_id = "inst_integration"

    # A signed, judgment-flagged task; the exact envelope shape that broke in
    # the real incident. ``db.add_task`` signs it for real (KEY_PATH above is
    # isolated but the sign/verify round trip is the real Ed25519 code path,
    # not a stub).
    task_id = await db.add_task(
        instance_id,
        "integration_probe",
        objetivo="Reply with exactly the single word OK and nothing else.",
        params={"_judgment": True},
        origen_trust=2,  # HUMANO
        origen_actor="integration-test",
    )
    assert task_id

    summary = await step.step(instance_id, config=config, provider=provider)

    # The tick must not have crashed, and must have actually run the model
    # (not deferred it; a deferral would mean the live CLI failed, which is
    # the scenario this test exists to catch, not silently tolerate).
    assert summary["triggers"] == 1
    assert summary["deferred"] == 0, (
        f"live CLI {cli_name!r} could not answer; step() summary={summary}"
    )
    assert summary["judged"] == 1

    episodes = await db.load_recent_episodes(instance_id)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["resultado"], "judgment episode must record a real answer"
    assert ep["decision"] != step.DEFERRED_DECISION
    # A subscription CLI deliberately records $0 marginal cost (see
    # ClaudeCodeProvider.complete); the assertion is that cost is TRACKED
    # (a real float, never left unset), not that it is nonzero.
    assert isinstance(ep["costo_usd"], (int, float))
    assert ep["costo_usd"] >= 0.0

    # The originating task must be resolved, not left dangling.
    assert await db.load_pending_tasks(instance_id) == []


# ── test 2: one real $0 beat, empty watchlist, fresh DB + migration ─────────


@pytest.mark.asyncio
async def test_heartbeat_empty_beat_fresh_db(tmp_path, monkeypatch):
    """A brand-new process opens a brand-new DB, migrates it, completes a beat.

    No provider, no mocked DB, no mocked policy; the deterministic $0 path
    every real heartbeat tick takes end to end, through ``core/heartbeat.py``
    (not a copy of it), against a real on-disk SQLite file.
    """
    db = await _isolated_db(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite
    from lhassa.core import heartbeat

    # The migration actually ran and landed on the current schema; this is
    # the "fresh process, fresh DB" half of the incident gap: a heartbeat that
    # never even gets past opening the DB is just as silent a failure as a
    # broken signature check.
    async with aiosqlite.connect(db.DB_PATH) as conn:
        version = await db.get_schema_version(conn)
    assert version == db.SCHEMA_VERSION

    summary = await heartbeat.beat("inst_integration_empty")

    assert summary == {"triggers": 0}
    assert await db.load_recent_episodes("inst_integration_empty") == []
