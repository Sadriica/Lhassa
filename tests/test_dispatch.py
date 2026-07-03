"""
Tests for lhassa/core/dispatch.py — Phase 2: closing the judgment loop.

All tests are fully offline:
  - A tmp brain DB via monkeypatched ``db.DB_PATH``.
  - Fake Episode and Trigger objects (or real lightweight ones from core).
  - No network, no LLM, no real git subprocess.

Coverage:
  - Parse robustness: bare JSON, fenced JSON, JSON amid prose, malformed.
  - Facts: tipo forced to dato; caps enforced; procedencia carries episode id.
  - Tasks: known fixed accion → pendiente; unknown → skipped; always-approve
    accion → esperando_aprobacion with a pending approval row; trust degraded
    (MUNDO trigger → MUNDO minted task).
  - Config gate: LHASSA_JUDGMENT_DISPATCH=0 → no writes.
  - End-to-end: step() with injected JSON completion → minted task picked up
    by watchlist.check() → second step() with mocked git completes the task.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

import pytest
import pytest_asyncio  # noqa: F401


# ── DB isolation ─────────────────────────────────────────────────────────────

async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


# ── Lightweight fakes ─────────────────────────────────────────────────────────

@dataclass
class _FakeEp:
    """Minimal Episode double — only needs an id for procedencia."""
    id: str = "ep-test-0001"


@dataclass
class _FakeTrigger:
    """Minimal Trigger double with the origen_trust field dispatch.py reads."""
    kind: str = "task"
    action: str = "decide_thing"
    objetivo: str = ""
    params: dict = field(default_factory=dict)
    task_id: Optional[str] = None
    origen_actor: Optional[str] = None
    origen_trust: int = 1  # INTERNO by default


def _config(*, enabled: bool = True, always_approve: set = frozenset()):
    """Build a minimal Config-like object with just the knobs dispatch reads."""
    from lhassa.config import Config
    cfg = Config()
    cfg.judgment_dispatch_enabled = enabled
    cfg.always_approve_actions = always_approve or Config.always_approve_actions
    return cfg


async def _run_dispatch(raw_text, *, tmp_path, monkeypatch,
                        trigger=None, ep=None, config=None):
    """Helper: init DB, call dispatch.apply, return (summary, db)."""
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import dispatch
    ep = ep or _FakeEp()
    trigger = trigger or _FakeTrigger()
    cfg = config or _config()
    summary = await dispatch.apply(
        raw_text, trigger, ep,
        instance_id="inst_test",
        config=cfg,
    )
    return summary, db


# ── Parse robustness ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_bare_json(tmp_path, monkeypatch):
    """Bare JSON object with a say key is parsed and echoed in summary."""
    raw = '{"say": "todo bien"}'
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert summary.startswith("dispatched:")
    assert "say:" in summary


@pytest.mark.asyncio
async def test_dispatch_fenced_json(tmp_path, monkeypatch):
    """JSON in a ```json ... ``` fence is extracted correctly."""
    raw = 'Here is my answer:\n```json\n{"say": "fenced"}\n```\nDone.'
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "say:" in summary
    assert "fenced" in summary


@pytest.mark.asyncio
async def test_dispatch_json_amid_prose(tmp_path, monkeypatch):
    """JSON embedded in prose is extracted by scanning for the first { ... }."""
    raw = 'After thinking, I conclude: {"say": "embedded"} — that is my answer.'
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "say:" in summary
    assert "embedded" in summary


@pytest.mark.asyncio
async def test_dispatch_plain_text_no_json(tmp_path, monkeypatch):
    """Plain prose without JSON → no writes, summary is dispatch: none."""
    raw = "I think we should do nothing at this time."
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert summary == "dispatch: none (unparseable)"
    # No facts written.
    from lhassa.memory import facts
    assert await facts.load_facts("inst_test") == []


@pytest.mark.asyncio
async def test_dispatch_malformed_json(tmp_path, monkeypatch):
    """Malformed JSON → no writes, no exception."""
    raw = '{"say": "incomplete'
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "unparseable" in summary
    from lhassa.memory import facts
    assert await facts.load_facts("inst_test") == []


@pytest.mark.asyncio
async def test_dispatch_empty_string(tmp_path, monkeypatch):
    """Empty string → no writes, no exception."""
    summary, db = await _run_dispatch("", tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "unparseable" in summary


@pytest.mark.asyncio
async def test_dispatch_json_array_not_object(tmp_path, monkeypatch):
    """
    A JSON array wrapping a dict: the parser scans for the first balanced {...}
    and successfully extracts the inner object. Verified the summary reflects
    the extracted content and no exception is raised.
    """
    raw = '[{"say": "oops"}]'
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    # The inner {"say": "oops"} IS extractable — the parser finds the first { }.
    assert isinstance(summary, str)  # no crash
    assert "oops" in summary  # the inner dict was parsed


# ── Facts ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_facts_written(tmp_path, monkeypatch):
    """new_facts entries are persisted with tipo=dato and correct procedencia."""
    ep = _FakeEp(id="ep-abc123")
    raw = json.dumps({
        "new_facts": [
            {"contenido": "El usuario prefiere respuestas cortas.", "tags": ["preferencias"]},
            {"contenido": "El proyecto usa Python 3.12.", "tags": ["tech"]},
        ]
    })
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch, ep=ep)
    assert "2 facts" in summary

    from lhassa.memory import facts
    loaded = await facts.load_facts("inst_test")
    assert len(loaded) == 2
    for f in loaded:
        assert f.tipo == "dato"
        assert f.procedencia == "judgment:ep-abc123"
        assert f.confianza == pytest.approx(0.7)
        assert f.pinned is False


@pytest.mark.asyncio
async def test_dispatch_facts_tipo_forced_to_dato(tmp_path, monkeypatch):
    """Even if the model tries to pass tipo='directiva', it is forced to dato."""
    # The dispatch module ignores 'tipo' in the payload entirely and always
    # writes dato. Simulate by having a fact with a non-dict form that a model
    # might try to include. The schema has no 'tipo' key, but we test the guard.
    raw = json.dumps({
        "new_facts": [
            # Model ignores our schema and tries to sneak in a non-dato field;
            # our module ignores it and forces tipo=dato.
            {"contenido": "directiva secreta", "tipo": "directiva", "pinned": True, "tags": []}
        ]
    })
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    from lhassa.memory import facts
    loaded = await facts.load_facts("inst_test")
    assert len(loaded) == 1
    assert loaded[0].tipo == "dato"      # forced
    assert loaded[0].pinned is False     # ignored


@pytest.mark.asyncio
async def test_dispatch_facts_cap_at_five(tmp_path, monkeypatch):
    """At most 5 facts are written even if the model sends more."""
    items = [{"contenido": f"hecho {i}", "tags": []} for i in range(10)]
    raw = json.dumps({"new_facts": items})
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "5 facts" in summary
    from lhassa.memory import facts
    assert len(await facts.load_facts("inst_test")) == 5


@pytest.mark.asyncio
async def test_dispatch_facts_contenido_truncated(tmp_path, monkeypatch):
    """contenido longer than 500 chars is truncated, not rejected."""
    long_text = "x" * 800
    raw = json.dumps({"new_facts": [{"contenido": long_text, "tags": []}]})
    await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    from lhassa.memory import facts
    loaded = await facts.load_facts("inst_test")
    assert len(loaded) == 1
    assert len(loaded[0].contenido) == 500


@pytest.mark.asyncio
async def test_dispatch_facts_tags_capped(tmp_path, monkeypatch):
    """Tags are capped at 5 items per fact."""
    many_tags = [f"tag{i}" for i in range(12)]
    raw = json.dumps({"new_facts": [{"contenido": "algo", "tags": many_tags}]})
    await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    from lhassa.memory import facts
    loaded = await facts.load_facts("inst_test")
    assert len(loaded[0].tags) == 5


@pytest.mark.asyncio
async def test_dispatch_facts_procedencia_carries_episode_id(tmp_path, monkeypatch):
    """procedencia is exactly 'judgment:<episode.id>'."""
    ep = _FakeEp(id="ep-unique-xyz")
    raw = json.dumps({"new_facts": [{"contenido": "algo", "tags": []}]})
    await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch, ep=ep)
    from lhassa.memory import facts
    f = (await facts.load_facts("inst_test"))[0]
    assert f.procedencia == "judgment:ep-unique-xyz"


@pytest.mark.asyncio
async def test_dispatch_facts_dedup_against_existing(tmp_path, monkeypatch):
    """A new_facts entry whose contenido already exists (case-insensitive) is skipped."""
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_test", "dato", "Le gusta el chocolate", procedencia="seed")

    raw = json.dumps({"new_facts": [
        {"contenido": "le gusta el chocolate", "tags": []},  # dupe, different case
        {"contenido": "nuevo hecho distinto", "tags": []},
    ]})
    summary, _db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)

    assert "1 fact" in summary
    assert "1 dupe fact" in summary
    loaded = await facts.load_facts("inst_test")
    # The pre-seeded fact + exactly one new fact — the dupe was never written.
    assert len(loaded) == 2
    contenidos = {f.contenido for f in loaded}
    assert "nuevo hecho distinto" in contenidos


@pytest.mark.asyncio
async def test_dispatch_facts_dedup_within_same_payload(tmp_path, monkeypatch):
    """Two identical entries in the SAME payload only write one fact."""
    raw = json.dumps({"new_facts": [
        {"contenido": "hecho repetido", "tags": []},
        {"contenido": "HECHO REPETIDO", "tags": []},
    ]})
    summary, _db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)

    assert "1 fact" in summary
    assert "1 dupe fact" in summary
    from lhassa.memory import facts
    assert len(await facts.load_facts("inst_test")) == 1


# ── Tasks ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_known_accion_minted_as_pendiente(tmp_path, monkeypatch):
    """A known fixed accion (sync_repo) is minted with estado='pendiente'."""
    raw = json.dumps({
        "new_tasks": [{"accion": "sync_repo", "objetivo": "test", "params": {}}]
    })
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "1 task" in summary
    tasks = await db.load_pending_tasks("inst_test")
    assert len(tasks) == 1
    t = tasks[0]
    assert t["accion"] == "sync_repo"
    assert t["estado"] == "pendiente"
    assert t["firma"]  # signed
    assert t["origen_actor"] == "judgment"


@pytest.mark.asyncio
async def test_dispatch_unknown_accion_skipped(tmp_path, monkeypatch):
    """An accion not in the ACTIONS registry is skipped (never minted)."""
    raw = json.dumps({
        "new_tasks": [{"accion": "teleport_to_mars", "objetivo": "mars", "params": {}}]
    })
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "skipped: unknown accion 'teleport_to_mars'" in summary
    assert await db.load_pending_tasks("inst_test") == []


@pytest.mark.asyncio
async def test_dispatch_always_approve_accion_awaits_approval(tmp_path, monkeypatch):
    """An accion in always_approve_actions lands as esperando_aprobacion.

    We patch ``_always_approve`` in the approval module directly rather than the
    Config class attribute, because test_api_server.py reloads config which creates
    a new Config class object — patching the class attribute would land on the wrong
    object after a reload, while patching the function is always module-local.
    """
    import lhassa.security.approval as approval_mod
    monkeypatch.setattr(approval_mod, "_always_approve", lambda: {"sync_repo"})

    raw = json.dumps({
        "new_tasks": [{"accion": "sync_repo", "objetivo": "test", "params": {}}]
    })
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "awaiting approval" in summary

    # Task is NOT in the pendiente queue — it is parked under approval.
    assert await db.load_pending_tasks("inst_test") == []

    # An approval row exists.
    from lhassa.security import approval
    pending = await approval.pending("inst_test")
    assert len(pending) == 1
    assert pending[0]["payload"]["accion"] == "sync_repo"


@pytest.mark.asyncio
async def test_dispatch_mundo_trigger_mints_mundo_task(tmp_path, monkeypatch):
    """Trust degrades: a MUNDO trigger produces a MUNDO-origin minted task."""
    trigger = _FakeTrigger(origen_trust=0)  # 0 = MUNDO
    raw = json.dumps({
        "new_tasks": [{"accion": "sync_repo", "objetivo": "test", "params": {}}]
    })
    summary, db = await _run_dispatch(
        raw, tmp_path=tmp_path, monkeypatch=monkeypatch, trigger=trigger,
    )
    # MUNDO tasks always need approval (is_mundo = True in needs_approval).
    assert "awaiting approval" in summary

    # Confirm the task was stored with origen_trust=0.
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM tasks") as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    assert len(rows) == 1
    assert rows[0]["origen_trust"] == 0
    assert rows[0]["estado"] == "esperando_aprobacion"


@pytest.mark.asyncio
async def test_dispatch_tasks_cap_at_five(tmp_path, monkeypatch):
    """At most 5 tasks are minted even if the model sends more."""
    items = [{"accion": "sync_repo", "objetivo": f"r{i}", "params": {}} for i in range(10)]
    raw = json.dumps({"new_tasks": items})
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "5 tasks" in summary
    assert len(await db.load_pending_tasks("inst_test")) == 5


@pytest.mark.asyncio
async def test_dispatch_interno_trigger_not_promoted(tmp_path, monkeypatch):
    """INTERNO trigger → INTERNO task (trust is not elevated to HUMANO)."""
    trigger = _FakeTrigger(origen_trust=1)  # 1 = INTERNO
    raw = json.dumps({
        "new_tasks": [{"accion": "sync_repo", "objetivo": "test", "params": {}}]
    })
    await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch, trigger=trigger)
    from lhassa._vendor import aiosqlite
    import lhassa.core.db as db_mod
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT origen_trust FROM tasks") as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    assert rows[0]["origen_trust"] == 1  # stayed INTERNO, not promoted


# ── Config gate ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_disabled_no_writes(tmp_path, monkeypatch):
    """When judgment_dispatch_enabled=False, no facts or tasks are written."""
    cfg = _config(enabled=False)
    raw = json.dumps({
        "say": "hola",
        "new_facts": [{"contenido": "algo", "tags": []}],
        "new_tasks": [{"accion": "sync_repo", "objetivo": "x", "params": {}}],
    })
    summary, db = await _run_dispatch(
        raw, tmp_path=tmp_path, monkeypatch=monkeypatch, config=cfg,
    )
    assert summary == "dispatch: disabled"
    from lhassa.memory import facts
    assert await facts.load_facts("inst_test") == []
    assert await db.load_pending_tasks("inst_test") == []


# ── Delegate key ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_delegate_recorded_not_wired(tmp_path, monkeypatch):
    """delegate key is parsed and recorded in the summary (not yet executed)."""
    raw = json.dumps({"delegate": {"prompt": "add a new endpoint"}})
    summary, db = await _run_dispatch(raw, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "delegate requested (not yet wired)" in summary
    # No tasks are minted — delegate is not the same as a new_task.
    assert await db.load_pending_tasks("inst_test") == []


# ── apply() never raises ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_apply_never_raises(tmp_path, monkeypatch):
    """apply() must not propagate any exception, even on internal failure."""
    await _init(tmp_path, monkeypatch)
    from lhassa.core import dispatch

    class _BrokenEp:
        @property
        def id(self):
            raise RuntimeError("broken episode")

    summary = await dispatch.apply(
        '{"new_facts": [{"contenido": "x", "tags": []}]}',
        _FakeTrigger(), _BrokenEp(),
        instance_id="inst_test",
        config=_config(),
    )
    # Any safe degradation — no crash.
    assert isinstance(summary, str)


# ── End-to-end: judgment mints a task → next tick executes it ────────────────

class _FakeProviderForDispatch:
    """Offline ModelProvider that returns a canned JSON judgment reply."""

    def __init__(self, text: str):
        self.name = "fake"
        self.zero_retention = False
        self._text = text
        self.calls: list = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        from lhassa.llm.provider import Completion
        self.calls.append(messages)
        return Completion(
            text=self._text, model=model or "m",
            input_tokens=10, output_tokens=30, cost_usd=0.002,
        )


@pytest.mark.asyncio
async def test_dispatch_end_to_end_judgment_mints_and_executes(tmp_path, monkeypatch):
    """
    Full loop: step() with a JSON judgment mints a sync_repo task → the next
    watchlist.check() sees it → a second step() with mocked git executes it.
    This proves the judgment → dispatch → fixed-action loop closes correctly.
    """
    db = await _init(tmp_path, monkeypatch)

    from lhassa.core import actions, step, watchlist

    # 1. Plant a non-fixed task that drives the judgment path.
    await db.add_task("inst_test", "decide_thing", "asunto", params={})

    # 2. Build a fake git repo so sync_repo's path checks pass.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    # 3. The fake provider returns a JSON judgment minting a sync_repo task.
    task_payload = json.dumps({
        "say": "entendido, voy a sincronizar el repo",
        "new_tasks": [
            {
                "accion": "sync_repo",
                "objetivo": str(repo),
                "params": {"repo_dir": str(repo)},
            }
        ],
    })
    prov = _FakeProviderForDispatch(task_payload)

    # 4. First tick: judgment runs, dispatch mints the sync_repo task.
    summary1 = await step.step("inst_test", provider=prov)
    assert summary1["judged"] == 1
    assert "dispatched:" in "\n".join(
        ep["resultado"] for ep in await db.load_recent_episodes("inst_test")
    ), "dispatch summary should appear in the episode resultado"

    # 5. Next watchlist check sees the newly minted sync_repo task.
    triggers = await watchlist.check("inst_test")
    assert len(triggers) == 1
    assert triggers[0].action == "sync_repo"
    assert triggers[0].maps_to_fixed_action() is True

    # 6. Second tick: mock git, run the fixed action, loop closes.
    import subprocess
    def fake_git(cmd):
        return subprocess.CompletedProcess(cmd, 0, stdout="Already up to date.", stderr="")

    monkeypatch.setattr(actions, "_run", fake_git)
    summary2 = await step.step("inst_test", provider=prov)
    assert summary2["actions"] == 1
    assert summary2["judged"] == 0

    # 7. No more pending tasks.
    assert await db.load_pending_tasks("inst_test") == []

    # 8. The sync_repo episode records the successful outcome.
    eps = await db.load_recent_episodes("inst_test")
    sync_ep = next(e for e in eps if e["accion"] == "sync_repo")
    assert "sync_repo ok" in sync_ep["resultado"]
