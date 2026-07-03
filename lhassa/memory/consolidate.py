"""
Semantic consolidation — the cheap-LLM second pass over the chica (§2.3, Fase 2).

The brief splits "bajar / consolidar" into two layers, by **threshold** (the chica
filled up), never by a clock:

  1. **Mechanical LRU (first line, ~80%, NO tokens).** Lives in
     :func:`lhassa.memory.chica.consolidate`: evict the least-recently-used
     NON-pinned, NON-directiva items down to ``chica_max_items``. This is the bulk
     of the work and is exact and free. It also remains the safety net for raw
     overflow after the semantic pass runs.

  2. **Semantic (second pass, the cheap LLM).** This module. A CHEAP model
     (router ``pick_model(..., hard=False)``) is asked to:
       - **summarize clusters** of similar evictable items into ONE compact item
         (the originals are dropped, the summary is kept — fewer items, same gist);
       - **flag contradictions** among the facts (``detect_contradictions``).

The semantic pass **only touches the chica** (it may, optionally, record a derived
summary *fact* in the grande as provenance — off by default). The grande stays the
append-only source of truth, so nothing is ever truly lost.

Everything is threshold-driven: :func:`consolidate_semantic` is a no-op when the
feature is off (``LHASSA_SEMANTIC_CONSOLIDATION``), or when the chica is at/below
its threshold — unless explicitly forced. Every model call goes **through the
provider** (so the gateway meters cost), and only the cheap model is used. The
model call is fully injectable (``provider`` / ``completion_fn``) so the whole
module is offline-testable with canned responses.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from lhassa.config import Config
from lhassa.llm import router
from lhassa.llm.provider import Completion, ModelProvider, get_model_provider
from lhassa.memory import chica, facts as facts_mod
from lhassa.memory.facts import Fact

# Cheap models are small; a few hundred tokens of JSON is plenty for either task.
_MAX_TOKENS = 512


def _empty(cost: float = 0.0) -> dict:
    """The canonical no-op / result shape."""
    return {"clustered": 0, "summarized": 0, "contradictions": [], "cost_usd": cost}


def _resolve_instance(instance_id: str | None, config: Any) -> str:
    return instance_id or getattr(config, "instance_id", Config.instance_id)


def _resolve_provider_model(
    provider: ModelProvider | None, model: str | None, config: Any
) -> tuple[ModelProvider, str]:
    """Pick the CHEAP model on the chosen provider (default: the gateway provider).

    ``model`` overrides the router; otherwise ``router.pick_model(hard=False)``
    selects the cheap routine model. This is the single place the "cheap only"
    rule is enforced.
    """
    provider = provider or get_model_provider(config)
    model = model or router.pick_model(provider, hard=False, config=config)
    return provider, model


def _extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object out of a model answer.

    Tolerates ```json fences and surrounding prose by slicing from the first ``{``
    to the last ``}``. Returns ``{}`` if nothing parses (a defensive no-op rather
    than a crash on a malformed cheap-model answer).
    """
    if not text:
        return {}
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_json_array(text: str) -> list:
    """Extract the first JSON array from text. Returns ``[]`` on any failure.

    Handles bare JSON arrays, fenced ```json ... ``` blocks, and arrays embedded
    in surrounding prose. Copied here (not imported from ``core.dispatch``) to
    avoid a circular import: ``memory`` → ``core`` → ``memory.facts``.
    """
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, TypeError):
        pass
    # Fenced code block.
    fenced = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, list):
                return parsed
        except (ValueError, TypeError):
            pass
    # Embedded array: slice from first [ to last ].
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, list):
            return parsed
    except (ValueError, TypeError):
        pass
    return []


async def _cheap_complete(
    messages: list[dict],
    *,
    provider: ModelProvider,
    model: str,
    completion_fn: Any,
) -> Completion:
    """Run one cheap-model call through the provider (so the gateway meters it)."""
    opts: dict[str, Any] = {}
    if completion_fn is not None:
        opts["completion_fn"] = completion_fn
    return await provider.complete(messages, model=model, max_tokens=_MAX_TOKENS, **opts)


# ── Contradiction detection ───────────────────────────────────────────────────


def _contradiction_messages(facts: list[Fact]) -> list[dict]:
    lines = "\n".join(f"- {f.id}: {f.contenido}" for f in facts)
    prompt = (
        "You audit a knowledge base for contradictions. Below is a list of facts, "
        "each as `- <id>: <text>`. Return ONLY JSON of the form "
        '{"contradictions": [["<id_a>", "<id_b>"], ...]} listing pairs of fact ids '
        "whose statements contradict each other. If none contradict, return "
        '{"contradictions": []}.\n\nFacts:\n' + lines
    )
    return [{"role": "user", "content": prompt}]


async def _detect_contradictions_costed(
    facts: list[Fact],
    *,
    provider: ModelProvider,
    model: str,
    completion_fn: Any,
) -> tuple[list[tuple], float]:
    """Core contradiction call → ``(pairs, cost_usd)``.

    Skips the model call entirely (zero cost) when there are fewer than two facts
    to compare. Only returns pairs whose ids are both present in ``facts``.
    """
    if len(facts) < 2:
        return [], 0.0

    valid_ids = {f.id for f in facts}
    completion = await _cheap_complete(
        _contradiction_messages(facts),
        provider=provider,
        model=model,
        completion_fn=completion_fn,
    )
    data = _extract_json(completion.text)
    pairs: list[tuple] = []
    for raw in data.get("contradictions", []) or []:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        a, b = raw[0], raw[1]
        if a in valid_ids and b in valid_ids and a != b:
            pairs.append((a, b))
    return pairs, float(completion.cost_usd or 0.0)


async def detect_contradictions(
    facts: list[Fact],
    *,
    provider: ModelProvider | None = None,
    model: str | None = None,
    completion_fn: Any = None,
    config: Any = Config,
) -> list[tuple]:
    """Ask the CHEAP model to flag contradictory pairs among ``facts``.

    Returns a list of ``(id_a, id_b)`` tuples. The model call is injectable
    (``provider`` / ``completion_fn``) so this is pure-ish and offline-testable;
    with fewer than two facts it never calls a model.
    """
    provider, model = _resolve_provider_model(provider, model, config)
    pairs, _ = await _detect_contradictions_costed(
        facts, provider=provider, model=model, completion_fn=completion_fn
    )
    return pairs


# ── Clustering / summarizing ──────────────────────────────────────────────────


def _cluster_messages(items: list[dict]) -> list[dict]:
    lines = "\n".join(f"- {it['key']}: {it['content']}" for it in items)
    prompt = (
        "You consolidate a working-memory cache. Below are items, each as "
        "`- <key>: <text>`. Group items that say substantially the SAME thing into "
        "clusters and write ONE compact summary per cluster that preserves the "
        "meaning. Only cluster items that are genuinely redundant; leave unique "
        "items out. Return ONLY JSON of the form "
        '{"clusters": [{"summary": "<text>", "members": ["<key>", ...]}, ...]}.'
        "\n\nItems:\n" + lines
    )
    return [{"role": "user", "content": prompt}]


def _summary_key(summary: str) -> str:
    """Deterministic, collision-resistant key for a written summary item."""
    digest = hashlib.sha1(summary.encode("utf-8")).hexdigest()[:12]
    return f"summary:{digest}"


# ── The semantic pass ─────────────────────────────────────────────────────────


async def consolidate_semantic(
    instance_id: str | None = None,
    *,
    provider: ModelProvider | None = None,
    model: str | None = None,
    completion_fn: Any = None,
    config: Any = Config,
    max_items: int | None = None,
    force: bool = False,
    record_provenance: bool = False,
) -> dict:
    """Cheap-LLM second pass over the chica — cluster+summarize, flag contradictions.

    Threshold-driven (never on a clock): a NO-OP when
    ``config.semantic_consolidation`` is off, or when the chica is at/below
    ``max_items`` (defaults to ``config.chica_max_items``) — unless ``force=True``
    (an explicit call). This complements, and never replaces, the mechanical LRU
    in :func:`lhassa.memory.chica.consolidate`, which remains the first line and
    still handles raw overflow afterwards.

    Steps:
      1. Gather the evictable chica items (NON-pinned, NON-directiva) and the
         instance's candidate facts. Pinned items and directivas are never touched.
      2. Ask the CHEAP model (``router.pick_model(hard=False)``) to summarize
         clusters of similar items and, separately, to flag contradictions among
         the facts.
      3. Write each cluster summary back as one consolidated item and drop the
         redundant originals. Optionally (``record_provenance``) append a derived
         summary fact to the grande as provenance.

    Returns ``{"clustered", "summarized", "contradictions": [...], "cost_usd"}``:
    ``clustered`` = originals folded into a summary, ``summarized`` = summaries
    written, ``contradictions`` = flagged ``(id_a, id_b)`` pairs, ``cost_usd`` =
    metered spend across both cheap-model calls.
    """
    instance_id = _resolve_instance(instance_id, config)

    if not getattr(config, "semantic_consolidation", True):
        logger.debug("consolidate_semantic: disabled by config — no-op")
        return _empty()

    max_items = max_items if max_items is not None else config.chica_max_items
    total = await chica.size(instance_id)
    if not force and total <= max_items:
        logger.debug(
            f"consolidate_semantic: below threshold ({total} <= {max_items}) — no-op"
        )
        return _empty()

    provider, model = _resolve_provider_model(provider, model, config)
    cost = 0.0

    # (1) evictable items = NON-pinned, NON-directiva. Pinned + directivas (identity)
    # are never candidates — exactly like the mechanical LRU protects them.
    all_items = await chica.items(instance_id)
    evictable = [
        it
        for it in all_items
        if not it["pinned"] and it["kind"] != chica.KIND_DIRECTIVA
    ]
    evictable_keys = {it["key"] for it in evictable}

    clustered = 0
    summarized = 0

    # (2)+(3) cluster + summarize. Need at least two items for redundancy to exist.
    if len(evictable) >= 2:
        completion = await _cheap_complete(
            _cluster_messages(evictable),
            provider=provider,
            model=model,
            completion_fn=completion_fn,
        )
        cost += float(completion.cost_usd or 0.0)
        data = _extract_json(completion.text)

        for cluster in data.get("clusters", []) or []:
            if not isinstance(cluster, dict):
                continue
            summary = (cluster.get("summary") or "").strip()
            # Only keep members that are real, still-present evictable keys.
            members = [
                k for k in (cluster.get("members") or []) if k in evictable_keys
            ]
            # A cluster only earns its keep if it collapses ≥2 originals into one.
            if not summary or len(members) < 2:
                continue

            skey = _summary_key(summary)
            await chica.remember(
                instance_id, skey, summary, pinned=False, kind=chica.KIND_ITEM
            )
            for k in members:
                # Don't drop the summary if a member happened to hash to its key.
                if k != skey:
                    await chica.forget(instance_id, k)
                    evictable_keys.discard(k)
            clustered += len(members)
            summarized += 1

            if record_provenance:
                # Minimal, optional: a derived "dato" fact in the grande as a
                # provenance breadcrumb. The grande stays append-only.
                await facts_mod.add_fact(
                    instance_id,
                    "dato",
                    summary,
                    tags=["consolidacion"],
                    procedencia="consolidate_semantic",
                    confianza=0.5,
                )

    # (2) contradictions among the candidate facts (separate cheap-model call).
    candidate_facts = await facts_mod.load_facts(instance_id)
    contradictions, c_cost = await _detect_contradictions_costed(
        candidate_facts, provider=provider, model=model, completion_fn=completion_fn
    )
    cost += c_cost

    logger.info(
        f"consolidate_semantic: clustered={clustered} summarized={summarized} "
        f"contradictions={len(contradictions)} cost_usd={cost:.6f} "
        f"(provider={provider.name} model={model})"
    )
    return {
        "clustered": clustered,
        "summarized": summarized,
        "contradictions": contradictions,
        "cost_usd": cost,
    }


async def distill_episodes(
    instance_id: str | None = None,
    *,
    since_hours: float = 24,
    provider: "ModelProvider | None" = None,
    model: "str | None" = None,
    completion_fn: Any = None,
    config: Any = Config,
) -> str:
    """Extract durable facts from recent episodes via the cheap model.

    Loads the last ``since_hours`` of episodes (cap 50), asks the CHEAP model
    for at most 10 novel, durable facts about the owner or system, and writes
    them as ``dato`` facts (confianza=0.6, procedencia="distill:<ISO-date>").
    Skips facts whose exact ``contenido`` already exists in the grande
    (case-insensitive). Tags distilled facts with ``["distilled"]``.

    Returns a short summary string. Degrades gracefully — never raises:
    - ``BudgetExceeded`` → ``"distill deferred: budget exceeded"``
    - ``ProviderNotConfigured`` → ``"distill deferred: provider not configured"``
    - Malformed JSON reply → no writes, returns a note string.

    NOTE: This is the ONE function in the memory layer that may spend tokens.
    """
    from lhassa.llm.gateway import BudgetExceeded
    from lhassa.llm.provider import ProviderNotConfigured

    instance_id = _resolve_instance(instance_id, config)

    try:
        provider_obj, model_str = _resolve_provider_model(provider, model, config)
    except (BudgetExceeded, ProviderNotConfigured) as exc:
        return f"distill deferred: {exc}"
    except Exception as exc:
        logger.debug(f"distill_episodes: provider setup failed: {exc}")
        return f"distill deferred: {exc}"

    try:
        from lhassa.core import db as _core_db

        all_episodes = await _core_db.load_recent_episodes(instance_id, 50)
        cutoff_ts = datetime.now(timezone.utc).timestamp() - since_hours * 3600

        episodes: list[dict] = []
        for ep in all_episodes:
            ts_str = ep.get("creado_en") or ""
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.timestamp() >= cutoff_ts:
                        episodes.append(ep)
                except Exception:
                    episodes.append(ep)  # unparseable timestamp → include
            else:
                episodes.append(ep)

        if not episodes:
            return "distill: no recent episodes"

        lines: list[str] = []
        for ep in episodes[:50]:
            resultado = (ep.get("resultado") or "")[:200]
            accion = ep.get("accion") or ""
            despertar = ep.get("despertar_por") or ""
            snippet = f"[{despertar}:{accion}] {resultado}".strip()[:200]
            lines.append(f"- {snippet}")

        prompt = (
            "Below are recent system episodes. Extract at most 10 SHORT, NOVEL, "
            "DURABLE facts about the owner or system worth remembering long-term. "
            "Focus on preferences, habits, goals, recurring patterns, or notable "
            "system state. Return ONLY a JSON array of strings (one fact per "
            'string), or [] when nothing qualifies. Example: ["Prefers dark mode"]'
            "\n\nEpisodes:\n" + "\n".join(lines)
        )

        completion = await _cheap_complete(
            [{"role": "user", "content": prompt}],
            provider=provider_obj,
            model=model_str,
            completion_fn=completion_fn,
        )

        facts_list = _extract_json_array(completion.text)
        if not isinstance(facts_list, list):
            logger.debug(
                f"distill_episodes: model returned non-array: {completion.text[:100]}"
            )
            return "distill: model returned non-array"

        # Load existing contenidos for deduplication (case-insensitive exact match).
        existing_facts = await facts_mod.load_facts(instance_id)
        existing_lower: set[str] = {f.contenido.strip().casefold() for f in existing_facts}

        iso_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        written = 0
        for item in facts_list[:10]:
            if not isinstance(item, str):
                continue
            text = item.strip()[:500]
            if not text:
                continue
            if text.casefold() in existing_lower:
                continue  # skip exact duplicate
            await facts_mod.add_fact(
                instance_id,
                "dato",
                text,
                confianza=0.6,
                procedencia=f"distill:{iso_date}",
                tags=["distilled"],
            )
            existing_lower.add(text.casefold())
            written += 1

        cost = float(completion.cost_usd or 0.0)
        logger.info(
            f"distill_episodes: wrote={written} cost_usd={cost:.6f} "
            f"episodes={len(episodes)}"
        )
        return (
            f"distill: wrote {written} facts from {len(episodes)} episodes "
            f"(cost_usd={cost:.6f})"
        )

    except BudgetExceeded as exc:
        return f"distill deferred: budget exceeded: {exc}"
    except ProviderNotConfigured as exc:
        return f"distill deferred: provider not configured: {exc}"
    except Exception as exc:
        logger.debug(f"distill_episodes: unexpected error: {exc}")
        return f"distill: error: {exc}"
