"""
Tests for the Phase 0 "hace y duerme" loop — step + watchlist + actions.

Fully offline (replay): a tmp brain DB via monkeypatched ``db.DB_PATH`` and a
monkeypatched git subprocess. No live network, LLM, or git.
"""

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.llm.provider import Completion, ProviderNotConfigured


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


def _fake_git(returncode=0, stdout="Already up to date.", stderr=""):
    """Build a fake ``actions._run`` returning a canned CompletedProcess."""
    import subprocess

    def _run(cmd):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return _run


def _make_repo(tmp_path):
    """Create a directory that looks like a git repo (has a .git marker)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


# ── no triggers → sleep ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_step_no_pending_tasks_sleeps(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step

    summary = await step.step("inst_personal")
    assert summary == {"triggers": 0}
    # No episode written.
    assert await db.load_recent_episodes("inst_personal") == []


# ── pending sync_repo task → runs it, writes one episode, marks done ─────────────

@pytest.mark.asyncio
async def test_step_runs_pending_sync_repo(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import actions, step

    repo = _make_repo(tmp_path)
    monkeypatch.setattr(actions, "_run", _fake_git(stdout="Updating abc..def"))

    task_id = await db.add_task(
        "inst_personal", "sync_repo", "proyecto_X",
        params={"repo_dir": str(repo)}, origen_actor="duenio",
    )

    summary = await step.step("inst_personal")
    assert summary["triggers"] == 1
    assert summary["actions"] == 1
    assert summary["deferred"] == 0

    # Exactly one episode, explaining why (despertar_por) and what (accion).
    eps = await db.load_recent_episodes("inst_personal")
    assert len(eps) == 1
    ep = eps[0]
    assert ep["despertar_por"] == "task:sync_repo"
    assert ep["accion"] == "sync_repo"
    assert ep["decision"] == "sync_repo"
    assert "sync_repo ok" in ep["resultado"]
    assert "Updating abc..def" in ep["resultado"]

    # Task marked done.
    assert await db.load_pending_tasks("inst_personal") == []

    # A second step finds nothing and writes no further episode.
    summary2 = await step.step("inst_personal")
    assert summary2 == {"triggers": 0}
    assert len(await db.load_recent_episodes("inst_personal")) == 1

    # Sanity: which task was completed.
    assert task_id


# ── instance isolation ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_step_respects_instance_isolation(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import actions, step

    repo = _make_repo(tmp_path)
    monkeypatch.setattr(actions, "_run", _fake_git())

    # Task lives under a *different* instance.
    await db.add_task("inst_other", "sync_repo", "x", params={"repo_dir": str(repo)})

    summary = await step.step("inst_personal")
    assert summary == {"triggers": 0}
    assert await db.load_recent_episodes("inst_personal") == []

    # The other instance still sees its task pending (we never touched it).
    assert len(await db.load_pending_tasks("inst_other")) == 1


# ── judgment branch — offline fake provider ──────────────────────────────────────


class FakeProvider:
    """Offline ModelProvider double — records calls, returns a canned Completion."""

    def __init__(self, *, name="fake", zero_retention=False, text="judged it",
                 cost_usd=0.0012, raises=None):
        self.name = name
        self.zero_retention = zero_retention
        self._text = text
        self._cost = cost_usd
        self._raises = raises
        self.calls = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        self.calls.append({"messages": messages, "model": model, "max_tokens": max_tokens})
        if self._raises is not None:
            raise self._raises
        return Completion(
            text=self._text, model=model or "m",
            input_tokens=10, output_tokens=20, cost_usd=self._cost,
        )


# ── unknown action with no usable provider → deferred episode, no raise ───────────

@pytest.mark.asyncio
async def test_step_unknown_action_is_deferred_safely(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step

    await db.add_task("inst_personal", "teleport", "marte", params={})

    # Provider is not configured → the judgment branch degrades to deferred.
    prov = FakeProvider(raises=ProviderNotConfigured("nope"))
    summary = await step.step("inst_personal", provider=prov)  # must not raise
    assert summary["triggers"] == 1
    assert summary["actions"] == 0
    assert summary["judged"] == 0
    assert summary["deferred"] == 1

    eps = await db.load_recent_episodes("inst_personal")
    assert len(eps) == 1
    assert eps[0]["decision"] == step.DEFERRED_DECISION
    assert eps[0]["accion"] == "teleport"

    # Not re-fired on the next tick (marked diferida, not pendiente).
    assert await db.load_pending_tasks("inst_personal") == []
    summary2 = await step.step("inst_personal", provider=prov)
    assert summary2 == {"triggers": 0}


# ── judgment branch — fake provider runs, records decision + cost ─────────────────

@pytest.mark.asyncio
async def test_step_judgment_runs_provider_and_records(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step

    await db.add_task("inst_personal", "decide_thing", "asunto", params={})

    prov = FakeProvider(text="hice el juicio", cost_usd=0.0034)
    summary = await step.step("inst_personal", provider=prov)
    assert summary["triggers"] == 1
    assert summary["actions"] == 0
    assert summary["judged"] == 1
    assert summary["deferred"] == 0

    # The fake provider was actually invoked.
    assert len(prov.calls) == 1

    eps = await db.load_recent_episodes("inst_personal")
    assert len(eps) == 1
    ep = eps[0]
    assert ep["despertar_por"] == "task:decide_thing"
    assert ep["accion"] == "decide_thing"
    assert ep["resultado"] == "hice el juicio"
    assert ep["decision"].startswith("think:")
    assert ep["costo_usd"] == pytest.approx(0.0034)

    # Task marked done; not re-fired.
    assert await db.load_pending_tasks("inst_personal") == []


@pytest.mark.asyncio
async def test_step_judgment_budget_exceeded_degrades(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step
    from lhassa.llm.gateway import BudgetExceeded

    await db.add_task("inst_personal", "decide_thing", "asunto", params={})

    prov = FakeProvider(raises=BudgetExceeded("spent"))
    summary = await step.step("inst_personal", provider=prov)  # must not raise
    assert summary["judged"] == 0
    assert summary["deferred"] == 1

    eps = await db.load_recent_episodes("inst_personal")
    assert eps[0]["decision"] == step.DEFERRED_DECISION
    assert "BudgetExceeded" in eps[0]["resultado"]


# ── judgment branch with a verify spec — records result, escalates via cascade ───


class ModelAwareProvider:
    """Offline provider whose answer depends on the model (cheap vs smart)."""

    def __init__(self, *, good_text="GOOD answer", bad_text="bad answer"):
        self.name = "fake"
        self.zero_retention = False
        self._good = good_text
        self._bad = bad_text
        self.calls = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        self.calls.append(model)
        text = self._good if model == "smart-m" else self._bad
        return Completion(
            text=text, model=model or "m", input_tokens=5, output_tokens=7,
            cost_usd=0.002,
        )


def _cascade_config():
    """A Config whose cheap/smart models differ so the cascade is observable."""
    from lhassa.config import Config

    cfg = Config()
    cfg.router_mode = "rules"
    cfg.model_cheap = "cheap-m"
    cfg.model_smart = "smart-m"
    return cfg


@pytest.mark.asyncio
async def test_step_verify_passes_on_cheap_no_escalation(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step

    # Verifier passes on the cheap answer → no escalation.
    await db.add_task(
        "inst_personal", "decide_thing", "asunto",
        params={"verify": {"type": "regex", "pattern": "bad answer"}},
    )
    prov = ModelAwareProvider()
    summary = await step.step("inst_personal", provider=prov, config=_cascade_config())
    assert summary["judged"] == 1

    # Only the cheap model was called — verify accepted it.
    assert prov.calls == ["cheap-m"]
    ep = (await db.load_recent_episodes("inst_personal"))[0]
    assert "verificado: passed" in ep["decision"]
    assert ep["resultado"] == "bad answer"


@pytest.mark.asyncio
async def test_step_verify_fails_cheap_escalates_to_smart(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step

    # Cheap answer lacks "GOOD" → verifier fails → cascade escalates once.
    await db.add_task(
        "inst_personal", "decide_thing", "asunto",
        params={"verify": {"type": "regex", "pattern": "GOOD"}},
    )
    prov = ModelAwareProvider()
    summary = await step.step("inst_personal", provider=prov, config=_cascade_config())
    assert summary["judged"] == 1

    # Cheap then smart — escalated exactly once.
    assert prov.calls == ["cheap-m", "smart-m"]
    ep = (await db.load_recent_episodes("inst_personal"))[0]
    assert "verificado: passed" in ep["decision"]  # smart answer passes
    assert ep["resultado"] == "GOOD answer"


@pytest.mark.asyncio
async def test_step_verify_fails_both_records_failed(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step

    # Neither answer matches → escalates and still records a failed verdict.
    await db.add_task(
        "inst_personal", "decide_thing", "asunto",
        params={"verify": {"type": "regex", "pattern": "NEVER-THERE"}},
    )
    prov = ModelAwareProvider()
    await step.step("inst_personal", provider=prov, config=_cascade_config())
    assert prov.calls == ["cheap-m", "smart-m"]
    ep = (await db.load_recent_episodes("inst_personal"))[0]
    assert "verificado: failed" in ep["decision"]


# ── delegate branch — launches a harness job, never blocks ───────────────────────


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def readline(self):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProc:
    def __init__(self, stdout, returncode=0, pid=555):
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream([b""])
        self.returncode = returncode
        self.pid = pid

    async def wait(self):
        return self.returncode

    def kill(self):
        pass

    async def communicate(self):
        """Allow OpenCodeProvider._run to call communicate() without crashing.

        Returns empty output so the provider raises ProviderNotConfigured and
        the judgment path degrades gracefully to a deferred episode.
        """
        return b"", b""


@pytest.mark.asyncio
async def test_step_delegate_launches_job_and_records_episode(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step
    from lhassa.harness import adapter as ad
    from lhassa.harness import runner

    runner._JOBS.clear()
    # Every harness "installed" so pick_harness resolves and launch spawns.
    monkeypatch.setattr(ad.shutil, "which", lambda name: "/usr/bin/" + name)

    async def fake_broadcast(ev):
        pass

    async def fake_create(*args, **kwargs):
        return _FakeProc([b'{"type":"result","total_cost_usd":0.10}\n', b""], pid=4242)

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(runner.state, "broadcast", fake_broadcast)

    # A delegated code task (delegate flag set in params).
    await db.add_task(
        "inst_personal", "implementar_feature", "repo_X",
        params={"delegate": True, "prompt": "añade un endpoint"},
    )

    summary = await step.step("inst_personal")
    assert summary["triggers"] == 1
    assert summary["delegated"] == 1
    assert summary["judged"] == 0
    assert summary["actions"] == 0

    # An episode notes the delegation + the PID, and a running job row exists.
    ep = (await db.load_recent_episodes("inst_personal"))[0]
    assert ep["decision"].startswith("delegate:")
    assert "delegado a opencode" in ep["resultado"]
    assert "PID 4242" in ep["resultado"]

    jobs = await _jobs(db)
    assert len(jobs) == 1
    assert jobs[0]["harness"] == "opencode"
    assert jobs[0]["estado"] == "running"

    # The task is marked done (the delegation happened — the job is tracked apart).
    assert await db.load_pending_tasks("inst_personal") == []

    # Next tick supervises: the finished job is reaped to done with its cost.
    job_id = jobs[0]["id"]
    await runner._JOBS[job_id].task  # let the stream finish
    summary2 = await step.step("inst_personal")
    assert summary2.get("reaped") == 1
    jobs2 = await _jobs(db)
    assert jobs2[0]["estado"] == "done"
    assert jobs2[0]["costo_usd"] == pytest.approx(0.10)
    runner._JOBS.clear()


@pytest.mark.asyncio
async def test_step_delegate_unavailable_harness_degrades_to_error_job(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step
    from lhassa.harness import adapter as ad
    from lhassa.harness import runner

    runner._JOBS.clear()
    # No CLI installed → pick_harness still honors an override, launch records error.
    monkeypatch.setattr(ad.shutil, "which", lambda name: None)
    monkeypatch.setenv("LHASSA_HARNESS", "claude")

    async def fake_broadcast(ev):
        pass

    async def fake_create(*args, **kwargs):
        raise AssertionError("no subprocess for an absent CLI")

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(runner.state, "broadcast", fake_broadcast)

    await db.add_task("inst_personal", "build_thing", "x", params={"delegate": True})

    summary = await step.step("inst_personal")  # must not raise
    assert summary["delegated"] == 1

    jobs = await _jobs(db)
    assert len(jobs) == 1
    assert jobs[0]["harness"] == "claude"
    assert jobs[0]["estado"].startswith("error")
    runner._JOBS.clear()


async def _jobs(db):
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM jobs ORDER BY creado_en") as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── watchlist.check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watchlist_check_maps_pending_tasks(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import watchlist

    assert await watchlist.check("inst_personal") == []

    await db.add_task(
        "inst_personal", "sync_repo", "proyecto_X",
        params={"repo_dir": "/tmp/x"}, origen_actor="duenio",
    )
    triggers = await watchlist.check("inst_personal")
    assert len(triggers) == 1
    t = triggers[0]
    assert t.kind == "task"
    assert t.action == "sync_repo"
    assert t.objetivo == "proyecto_X"
    assert t.params == {"repo_dir": "/tmp/x"}
    assert t.origen_actor == "duenio"
    assert t.task_id is not None
    assert t.maps_to_fixed_action() is True


def test_trigger_unknown_action_is_not_fixed():
    from lhassa.core.watchlist import Trigger
    t = Trigger(kind="task", action="teleport")
    assert t.maps_to_fixed_action() is False


# ── actions.sync_repo guards (never raise) ───────────────────────────────────────

def test_sync_repo_missing_dir():
    from lhassa.core import actions
    assert "no repo_dir" in actions.sync_repo({})


def test_sync_repo_dir_not_found(tmp_path):
    from lhassa.core import actions
    out = actions.sync_repo({"repo_dir": str(tmp_path / "nope")})
    assert "directory not found" in out


def test_sync_repo_not_a_git_repo(tmp_path):
    from lhassa.core import actions
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "not a git repo" in actions.sync_repo({"repo_dir": str(plain)})


def test_sync_repo_reports_git_failure(tmp_path, monkeypatch):
    from lhassa.core import actions
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(
        actions, "_run", _fake_git(returncode=1, stdout="", stderr="not a ff"),
    )
    out = actions.sync_repo({"repo_dir": str(repo)})
    assert "sync_repo failed" in out
    assert "not a ff" in out


def test_sync_repo_uses_objetivo_as_dir(tmp_path, monkeypatch):
    from lhassa.core import actions
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(actions, "_run", _fake_git(stdout="ok"))
    out = actions.sync_repo({"objetivo": str(repo)})
    assert "sync_repo ok" in out


# ── actions.run dispatch ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_actions_run_unknown_is_safe():
    from lhassa.core import actions
    out = await actions.run("does_not_exist", {})
    assert "unknown action" in out


@pytest.mark.asyncio
async def test_actions_run_attaches_action_to_episode(tmp_path, monkeypatch):
    from lhassa.core import actions
    from lhassa.core.episode import Episode

    repo = _make_repo(tmp_path)
    monkeypatch.setattr(actions, "_run", _fake_git(stdout="ok"))
    ep = Episode.begin(despertar_por="task:sync_repo", instance_id="inst_personal")
    await actions.run("sync_repo", {"repo_dir": str(repo)}, ep)
    assert ep.accion == "sync_repo"
