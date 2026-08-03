"""
Tests for lhassa.memory.consolidate — the Phase-2 semantic consolidation pass.

Fully offline: a fake CHEAP provider (or an injected ``completion_fn`` through a
tmp-file gateway) returns canned cluster summaries / contradiction pairs. tmp
chica + brain DB via the monkeypatch seams. NO network, NO live LLM.
"""

import json

import pytest

from lhassa.memory import consolidate


# ── seams + doubles ──────────────────────────────────────────────────────────


async def _init(tmp_path, monkeypatch):
    """Reroute the grande (db) and the chica to tmp files; init the grande."""
    import lhassa.core.db as db
    import lhassa.memory.chica as chica
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    monkeypatch.setattr(chica, "CHICA_DB_PATH", tmp_path / "test_chica.db")
    await db.init_db()
    return db, chica


class FakeConfig:
    """Minimal Config double covering what consolidate + the router read."""

    def __init__(
        self,
        *,
        semantic_consolidation=True,
        chica_max_items=3,
        model="cheap-model",
        model_cheap="cheap-model",
        model_smart="smart-model",
        router_mode="rules",
        instance_id="inst_personal",
    ):
        self.semantic_consolidation = semantic_consolidation
        self.chica_max_items = chica_max_items
        self.model = model
        self.model_cheap = model_cheap
        self.model_smart = model_smart
        self.router_mode = router_mode
        self.instance_id = instance_id


class FakeProvider:
    """Records the model of each call; replies with a canned JSON per call kind.

    ``cluster_json`` answers the clustering prompt; ``contradiction_json`` answers
    the contradiction prompt. The prompt is sniffed for the word "contradict".
    """

    def __init__(self, *, cluster_json="", contradiction_json="", cost_per_call=0.0):
        self.name = "fake"
        self.zero_retention = False
        self.models = []
        self._cluster = cluster_json
        self._contra = contradiction_json
        self._cost = cost_per_call

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        from lhassa.llm.provider import Completion
        self.models.append(model)
        text = messages[0]["content"]
        body = self._contra if "contradict" in text.lower() else self._cluster
        return Completion(text=body, model=model or "?", cost_usd=self._cost)


# ── clustering / summarizing ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clusters_and_summarizes_dropping_originals(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    inst = "inst_personal"
    # Four similar items about the same thing → one cluster.
    await chica.remember(inst, "k0", "user likes mint chocolate")
    await chica.remember(inst, "k1", "user enjoys chocolate with mint")
    await chica.remember(inst, "k2", "mint choc is a favorite of the user")
    await chica.remember(inst, "k3", "totally unrelated note about taxes")

    cluster_json = json.dumps(
        {"clusters": [{"summary": "user likes mint chocolate",
                       "members": ["k0", "k1", "k2"]}]}
    )
    prov = FakeProvider(cluster_json=cluster_json, contradiction_json='{"contradictions": []}')

    res = await consolidate.consolidate_semantic(
        inst, provider=prov, config=FakeConfig(chica_max_items=3),
    )

    assert res["clustered"] == 3
    assert res["summarized"] == 1
    keys = {it["key"] for it in await chica.items(inst)}
    # Originals dropped, the unrelated note + the summary remain.
    assert "k0" not in keys and "k1" not in keys and "k2" not in keys
    assert "k3" in keys
    contents = {it["content"] for it in await chica.items(inst)}
    assert "user likes mint chocolate" in contents
    # Fewer items than we started with (4 → 2).
    assert await chica.size(inst) == 2


@pytest.mark.asyncio
async def test_uses_the_cheap_model(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    inst = "inst_personal"
    for i in range(4):
        await chica.remember(inst, f"k{i}", f"similar note {i}")

    cluster_json = json.dumps(
        {"clusters": [{"summary": "merged", "members": ["k0", "k1"]}]}
    )
    prov = FakeProvider(cluster_json=cluster_json, contradiction_json='{"contradictions": []}')

    await consolidate.consolidate_semantic(
        inst, provider=prov,
        config=FakeConfig(chica_max_items=2, model_cheap="cheap-model", model_smart="smart-model"),
    )
    # Every call used the cheap model, never the smart one.
    assert prov.models  # at least one call happened
    assert all(m == "cheap-model" for m in prov.models)
    assert "smart-model" not in prov.models


@pytest.mark.asyncio
async def test_never_touches_pinned_or_directivas(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    inst = "inst_personal"
    await chica.remember(inst, "p0", "pinned identity dato", pinned=True)
    await chica.remember(inst, "d0", "be concise", kind=chica.KIND_DIRECTIVA)
    await chica.remember(inst, "k0", "similar note a")
    await chica.remember(inst, "k1", "similar note b")

    # Model maliciously tries to fold the protected keys into a cluster.
    cluster_json = json.dumps(
        {"clusters": [{"summary": "merged", "members": ["k0", "k1", "p0", "d0"]}]}
    )
    prov = FakeProvider(cluster_json=cluster_json, contradiction_json='{"contradictions": []}')

    res = await consolidate.consolidate_semantic(
        inst, provider=prov, config=FakeConfig(chica_max_items=2),
    )
    # Only the two evictable items counted; protected keys are not candidates.
    assert res["clustered"] == 2
    keys = {it["key"] for it in await chica.items(inst)}
    assert "p0" in keys and "d0" in keys      # protected, never dropped
    assert "k0" not in keys and "k1" not in keys


# ── contradiction detection ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flags_contradictions_among_facts(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    inst = "inst_personal"
    f_a = await facts.add_fact(inst, "dato", "lives in Lima")
    f_b = await facts.add_fact(inst, "dato", "lives in Quito")

    # Push the chica over threshold so the pass runs.
    for i in range(4):
        await chica.remember(inst, f"k{i}", f"note {i}")

    contra_json = json.dumps({"contradictions": [[f_a.id, f_b.id]]})
    prov = FakeProvider(cluster_json='{"clusters": []}', contradiction_json=contra_json)

    res = await consolidate.consolidate_semantic(
        inst, provider=prov, config=FakeConfig(chica_max_items=2),
    )
    assert (f_a.id, f_b.id) in res["contradictions"]


@pytest.mark.asyncio
async def test_detect_contradictions_helper(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    from lhassa.memory.facts import Fact
    f1 = Fact(id="f1", instance_id="i", tipo="dato", contenido="X is true")
    f2 = Fact(id="f2", instance_id="i", tipo="dato", contenido="X is false")
    contra_json = json.dumps({"contradictions": [["f1", "f2"], ["f1", "ghost"]]})
    prov = FakeProvider(contradiction_json=contra_json)

    pairs = await consolidate.detect_contradictions(
        [f1, f2], provider=prov, config=FakeConfig(),
    )
    # The ghost id (not in the input) is filtered out.
    assert pairs == [("f1", "f2")]


@pytest.mark.asyncio
async def test_detect_contradictions_skips_model_below_two_facts(tmp_path, monkeypatch):
    from lhassa.memory.facts import Fact
    prov = FakeProvider(contradiction_json='{"contradictions": [["a","b"]]}')
    pairs = await consolidate.detect_contradictions(
        [Fact(id="solo", instance_id="i", tipo="dato", contenido="lonely")],
        provider=prov, config=FakeConfig(),
    )
    assert pairs == []
    assert prov.models == []  # no model call was made


# ── cost metering ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_recorded_from_completion(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    inst = "inst_personal"
    await facts.add_fact(inst, "dato", "a")
    await facts.add_fact(inst, "dato", "b")
    for i in range(4):
        await chica.remember(inst, f"k{i}", f"note {i}")

    # Each cheap call costs 0.001 → cluster call + contradiction call = 0.002.
    prov = FakeProvider(
        cluster_json='{"clusters": [{"summary": "m", "members": ["k0","k1"]}]}',
        contradiction_json='{"contradictions": []}',
        cost_per_call=0.001,
    )
    res = await consolidate.consolidate_semantic(
        inst, provider=prov, config=FakeConfig(chica_max_items=2),
    )
    assert res["cost_usd"] == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_metered_through_gateway(tmp_path, monkeypatch):
    """A real GatewayProvider + tmp ledger meters the cheap call (completion_fn)."""
    _, chica = await _init(tmp_path, monkeypatch)
    from lhassa.llm.gateway import LedgerGateway
    from lhassa.llm.provider import GatewayProvider
    from lhassa.memory import facts
    inst = "inst_personal"
    await facts.add_fact(inst, "dato", "a")
    await facts.add_fact(inst, "dato", "b")
    for i in range(4):
        await chica.remember(inst, f"k{i}", f"note {i}")

    gw = LedgerGateway(db_path=tmp_path / "gw.db")
    cfg = FakeConfig(chica_max_items=2)
    cfg.llm_provider = "anthropic"
    cfg.llm_cache_enabled = False
    cfg.llm_daily_token_budget = 0
    prov = GatewayProvider(cfg, gateway=gw)

    async def fake_completion(provider, model, messages, max_tokens, temperature, system):
        # Cluster prompt → a merge; contradiction prompt → none.
        if "contradict" in messages[0]["content"].lower():
            return json.dumps({"contradictions": []}), 10, 5
        return json.dumps({"clusters": [{"summary": "m", "members": ["k0", "k1"]}]}), 100, 50

    res = await consolidate.consolidate_semantic(
        inst, provider=prov, model="claude-haiku-4-5", completion_fn=fake_completion,
        config=cfg,
    )
    # haiku pricing: (100*1 + 50*5)/1e6 + (10*1 + 5*5)/1e6
    expected = (100 * 1.0 + 50 * 5.0) / 1_000_000 + (10 * 1.0 + 5 * 5.0) / 1_000_000
    assert res["cost_usd"] == pytest.approx(expected)
    rows = await gw.ledger_rows()
    assert len(rows) == 2  # both cheap calls hit the ledger


# ── no-op guards ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_noop_when_feature_off(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    inst = "inst_personal"
    for i in range(10):
        await chica.remember(inst, f"k{i}", f"note {i}")
    prov = FakeProvider(cluster_json='{"clusters":[]}')
    res = await consolidate.consolidate_semantic(
        inst, provider=prov, config=FakeConfig(semantic_consolidation=False, chica_max_items=2),
    )
    assert res == {"clustered": 0, "summarized": 0, "contradictions": [], "cost_usd": 0.0}
    assert prov.models == []  # never called the model
    assert await chica.size(inst) == 10  # nothing dropped


@pytest.mark.asyncio
async def test_noop_below_threshold(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    inst = "inst_personal"
    await chica.remember(inst, "k0", "a")
    await chica.remember(inst, "k1", "b")
    prov = FakeProvider(cluster_json='{"clusters":[]}')
    res = await consolidate.consolidate_semantic(
        inst, provider=prov, config=FakeConfig(chica_max_items=5),
    )
    assert res == {"clustered": 0, "summarized": 0, "contradictions": [], "cost_usd": 0.0}
    assert prov.models == []


@pytest.mark.asyncio
async def test_force_runs_below_threshold(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    inst = "inst_personal"
    await chica.remember(inst, "k0", "similar a")
    await chica.remember(inst, "k1", "similar b")
    prov = FakeProvider(
        cluster_json='{"clusters": [{"summary": "m", "members": ["k0","k1"]}]}',
        contradiction_json='{"contradictions": []}',
    )
    res = await consolidate.consolidate_semantic(
        inst, provider=prov, config=FakeConfig(chica_max_items=99), force=True,
    )
    assert res["summarized"] == 1  # ran despite being below threshold


@pytest.mark.asyncio
async def test_provenance_fact_optional(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    inst = "inst_personal"
    for i in range(4):
        await chica.remember(inst, f"k{i}", f"similar {i}")
    prov = FakeProvider(
        cluster_json='{"clusters": [{"summary": "merged gist", "members": ["k0","k1"]}]}',
        contradiction_json='{"contradictions": []}',
    )
    await consolidate.consolidate_semantic(
        inst, provider=prov, config=FakeConfig(chica_max_items=2),
        record_provenance=True,
    )
    grande_facts = await facts.load_facts(inst)
    assert any(f.contenido == "merged gist" and f.procedencia == "consolidate_semantic"
               for f in grande_facts)
