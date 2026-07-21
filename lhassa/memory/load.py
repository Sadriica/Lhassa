"""
"Subir al despertar" — the layered load of the chica (live context) from the
grande, up to a token budget (§2.3).

Layers, in priority order:
  1. pinned facts + ALL ``directiva`` facts (ALWAYS — they are identity; the
     persona's own directives are folded in here too).
  2. recency: the recent episodes (continuity).
  3. active context by TAGS (exact membership, NOT semantics).
  4. semantic retrieval (embeddings): facts whose embedding is closest to the
     active ``query``. OFF by default — only when ``config.memory_layer4_enabled``
     and a ``query`` is supplied AND fact embeddings have been indexed.
  5. episodic recall (Fase 1): the top few past episodes whose text matches the
     active ``query`` (FTS5, $0, deterministic — see ``core.db.search_episodes``).
     Dropped before semantic, and fully defensive (any error → empty, never
     raises).
  6. knowledge (document perception): the top few chunks of the owner's OWN
     documents (``lhassa.memory.knowledge.search``) matching the active
     ``query`` — grounds thinking in real notes/papers at $0, offline. Fires
     ONLY when a ``query`` is given, mirroring the recall layer exactly. The
     LOWEST-priority layer; dropped first under budget, and fully defensive
     (any error → empty, never raises — a missing/empty knowledge index is
     the default, silent case).

Then :func:`truncate` trims to the token budget, dropping the lowest-priority
layer first (``priority=["pinned","recent","tags","semantic","recall","knowledge"]``):
knowledge goes before recall, recall before semantic, semantic before tags, tags
before recent, and the identity core (directives + pinned facts) is never dropped.

Token counting is a deterministic estimate — exact via tiktoken when installed,
else a ~3 chars/token safety heuristic (see :func:`estimate_tokens`). The semantic
layer (4) is built but default OFF, so the default behavior is byte-for-byte the
pinned/recent/tags load.
"""

from dataclasses import dataclass, field

from lhassa.config import Config
from lhassa.core import db
from lhassa.memory.embeddings import cosine, get_embedder
from lhassa.memory.facts import Fact, load_by_tags, load_facts, resolve
from lhassa.persona import load_identity

# Priority of the layers, highest first. Truncation drops from the tail.
PRIORITY = ["pinned", "recent", "tags", "semantic", "recall", "knowledge"]


# Lazy, cached tiktoken probe: attempted at most once per process. When tiktoken
# is installed we use its real ``cl100k_base`` encoding; otherwise we keep a
# dependency-free char heuristic. Never a hard dependency (see pyproject "tokens"
# extra).
_TIKTOKEN_TRIED = False
_TIKTOKEN_ENC = None


def _get_tiktoken_encoding():
    """Return a cached tiktoken cl100k_base encoding, or None if unavailable."""
    global _TIKTOKEN_TRIED, _TIKTOKEN_ENC
    if _TIKTOKEN_TRIED:
        return _TIKTOKEN_ENC
    _TIKTOKEN_TRIED = True
    try:
        import tiktoken

        _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _TIKTOKEN_ENC = None
    return _TIKTOKEN_ENC


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate — exact via tiktoken when installed, else a
    safety char heuristic (~3 chars/token; mixed Spanish/English + code packs
    denser than 4 chars/token, so 3 keeps the budget from over-counting headroom).
    No network, never raises.
    """
    if not text:
        return 0
    enc = _get_tiktoken_encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return (len(text) + 2) // 3


def _render_fact(fact: Fact) -> str:
    return f"- ({fact.tipo}) {fact.contenido}"


def _render_episode(ep: dict) -> str:
    resultado = ep.get("resultado") or ""
    return f"- [{ep.get('creado_en', '')}] {ep.get('despertar_por', '')}: {resultado}".rstrip(": ")


def _render_recall(row: dict) -> str:
    resultado = (row.get("resultado") or "").replace("\n", " ").strip()
    return f"- [{row.get('creado_en', '')}] {row.get('despertar_por', '')} ({row.get('decision', '')}): {resultado}".rstrip(": ")


def _render_knowledge(chunk: dict) -> str:
    import os as _os

    name = _os.path.basename(chunk.get("source_path", "")) or "your notes"
    text = (chunk.get("text") or "").replace("\n", " ").strip()
    return f"- [from your notes: {name}] {text}"


@dataclass
class Context:
    """
    The chica selected at wake: the identity directives, the chosen facts, and the
    recent episodes — plus a deterministic token estimate and a prompt render.
    """

    instance_id: str
    directives: list[str] = field(default_factory=list)
    pinned_facts: list[Fact] = field(default_factory=list)
    tag_facts: list[Fact] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)
    semantic_facts: list[Fact] = field(default_factory=list)
    recall_episodes: list[dict] = field(default_factory=list)
    knowledge_chunks: list[dict] = field(default_factory=list)
    token_budget: int = 0

    @property
    def facts(self) -> list[Fact]:
        """All selected facts (identity first, then tag-activated, then semantic)."""
        return [*self.pinned_facts, *self.tag_facts, *self.semantic_facts]

    def to_prompt(self) -> str:
        """Render the context as a plain-text block for a system prompt."""
        blocks: list[str] = []
        if self.directives:
            lines = "\n".join(f"- {d}" for d in self.directives)
            blocks.append(f"Directivas (siempre vigentes):\n{lines}")
        if self.pinned_facts:
            lines = "\n".join(_render_fact(f) for f in self.pinned_facts)
            blocks.append(f"Hechos fijados:\n{lines}")
        if self.tag_facts:
            lines = "\n".join(_render_fact(f) for f in self.tag_facts)
            blocks.append(f"Contexto activo:\n{lines}")
        if self.semantic_facts:
            lines = "\n".join(_render_fact(f) for f in self.semantic_facts)
            blocks.append(f"Contexto relacionado:\n{lines}")
        if self.episodes:
            lines = "\n".join(_render_episode(e) for e in self.episodes)
            blocks.append(f"Episodios recientes:\n{lines}")
        if self.recall_episodes:
            lines = "\n".join(_render_recall(r) for r in self.recall_episodes)
            blocks.append(f"Recall (episodios relacionados):\n{lines}")
        if self.knowledge_chunks:
            lines = "\n".join(_render_knowledge(c) for c in self.knowledge_chunks)
            blocks.append(f"From your documents:\n{lines}")
        return "\n\n".join(blocks)

    @property
    def token_estimate(self) -> int:
        """Deterministic token estimate of the rendered prompt."""
        return estimate_tokens(self.to_prompt())


def truncate(ctx: Context, token_budget: int) -> Context:
    """
    Trim ``ctx`` to ``token_budget`` in place, dropping the lowest-priority layer
    first (tags before recent). The identity core — directives + pinned facts —
    is mandatory and is never dropped, even if it alone exceeds the budget.
    """
    ctx.token_budget = token_budget

    # Mandatory core (always kept): directives + pinned facts.
    mandatory = 0
    if ctx.directives:
        mandatory += estimate_tokens(
            "Directivas (siempre vigentes):\n"
            + "\n".join(f"- {d}" for d in ctx.directives)
        )
    if ctx.pinned_facts:
        mandatory += estimate_tokens(
            "Hechos fijados:\n" + "\n".join(_render_fact(f) for f in ctx.pinned_facts)
        )

    # Optional items, each tagged with a priority rank (lower rank = more
    # important). Recent episodes outrank tag facts, which outrank semantic facts.
    recent_rank = PRIORITY.index("recent")
    tags_rank = PRIORITY.index("tags")
    semantic_rank = PRIORITY.index("semantic")
    recall_rank = PRIORITY.index("recall")
    knowledge_rank = PRIORITY.index("knowledge")
    items: list[dict] = []
    for ep in ctx.episodes:
        items.append({"rank": recent_rank, "tok": estimate_tokens(_render_episode(ep)),
                      "kind": "ep", "payload": ep})
    for f in ctx.tag_facts:
        items.append({"rank": tags_rank, "tok": estimate_tokens(_render_fact(f)),
                      "kind": "tag", "payload": f})
    for f in ctx.semantic_facts:
        items.append({"rank": semantic_rank, "tok": estimate_tokens(_render_fact(f)),
                      "kind": "semantic", "payload": f})
    for r in ctx.recall_episodes:
        items.append({"rank": recall_rank, "tok": estimate_tokens(_render_recall(r)),
                      "kind": "recall", "payload": r})
    for c in ctx.knowledge_chunks:
        items.append({"rank": knowledge_rank, "tok": estimate_tokens(_render_knowledge(c)),
                      "kind": "knowledge", "payload": c})

    used = mandatory + sum(it["tok"] for it in items)
    # Drop lowest-priority (highest rank) items, from the tail, until we fit.
    while used > token_budget and items:
        drop_idx = max(range(len(items)), key=lambda i: (items[i]["rank"], i))
        used -= items[drop_idx]["tok"]
        items.pop(drop_idx)

    ctx.episodes = [it["payload"] for it in items if it["kind"] == "ep"]
    ctx.tag_facts = [it["payload"] for it in items if it["kind"] == "tag"]
    ctx.semantic_facts = [it["payload"] for it in items if it["kind"] == "semantic"]
    ctx.recall_episodes = [it["payload"] for it in items if it["kind"] == "recall"]
    ctx.knowledge_chunks = [it["payload"] for it in items if it["kind"] == "knowledge"]
    return ctx


async def _semantic_layer(
    instance_id: str,
    query: str,
    config,
    *,
    exclude_ids: set,
    topk: int,
) -> list[Fact]:
    """Layer 4: top-``topk`` facts whose embedding is closest to ``query``.

    Reads stored fact embeddings (built by :func:`index_facts`), embeds the query
    with the same embedder family, ranks by cosine, and returns facts NOT already
    selected by pinned/tags. A no-op (empty list) when no embeddings are indexed.
    """
    stored = await db.get_fact_embeddings(instance_id)
    if not stored:
        return []
    embedder = get_embedder(config)
    qvec = embedder.embed([query])[0]
    embedder_name = getattr(embedder, "name", "")
    by_id = {f.id: f for f in await load_facts(instance_id)}

    scored: list[tuple[float, str]] = []
    for row in stored:
        fid = row["fact_id"]
        if fid in exclude_ids or fid not in by_id:
            continue
        # Only compare against rows stamped with the same model — vectors from
        # different embedders/models must never be cosine-compared.
        row_model = row.get("model") or ""
        if row_model != embedder_name:
            continue
        vec = row.get("vector") or []
        if len(vec) != len(qvec):  # different dim — skip, don't crash
            continue
        sim = cosine(qvec, vec)
        if sim > 0.0:
            scored.append((sim, fid))

    # Highest similarity first; fact id breaks ties deterministically.
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [by_id[fid] for _, fid in scored[:topk]]


async def _recall_layer(instance_id: str, query: str, *, topk: int = 3) -> list[dict]:
    """Layer 5 (Fase 1 "Recall"): top past episodes matching ``query`` — $0, no LLM.

    Fully defensive by design: this is the lowest-priority, purely-additive layer,
    so ANY failure (missing FTS5 index, bad DB state, whatever) must silently
    yield an empty layer rather than disturb the rest of the context build.
    """
    try:
        return await db.search_episodes(instance_id, query, topk)
    except Exception:
        return []


async def _knowledge_layer(
    instance_id: str, query: str, config, *, topk: int = 3
) -> list[dict]:
    """Layer 6 (document perception): top past-document chunks matching ``query``.

    Fully defensive by design — the LOWEST-priority, purely-additive layer, so
    ANY failure (no roots configured, no index yet, a bad DB state) must
    silently yield an empty layer rather than disturb the rest of the context
    build. Mirrors :func:`_recall_layer` exactly.
    """
    try:
        from lhassa.memory import knowledge

        return await knowledge.search(instance_id, query, limit=topk, config=config)
    except Exception:
        return []


async def index_facts(instance_id: str | None = None, config=None) -> int:
    """Embed and store every fact lacking an embedding. Returns the count indexed.

    Idempotent and cheap: facts that already have a stored embedding are skipped.
    This is the explicit index step layer 4 retrieves against (run by tests and,
    later, a heartbeat) — :func:`load` never builds the index implicitly.
    """
    config = config or Config
    instance_id = instance_id or config.instance_id
    facts = await load_facts(instance_id)
    if not facts:
        return 0
    existing = {row["fact_id"] for row in await db.get_fact_embeddings(instance_id)}
    pending = [f for f in facts if f.id not in existing]
    if not pending:
        return 0
    embedder = get_embedder(config)
    vectors = embedder.embed([f.contenido for f in pending])
    model = getattr(embedder, "name", "")
    for fact, vec in zip(pending, vectors):
        await db.upsert_fact_embedding(instance_id, fact.id, vec, model=model)
    return len(pending)


async def load(
    instance_id: str | None = None,
    *,
    token_budget: int,
    active_tags=(),
    recent_n: int = 20,
    query: str | None = None,
    config=None,
) -> Context:
    """
    Build the chica for ``instance_id`` by layering the grande, then truncate to
    ``token_budget``. See module docstring for the layers and their priority.

    ``active_tags`` selects layer 2 (active context). Layer 4 (semantic) is built
    only when ``config.memory_layer4_enabled`` AND a ``query`` is given AND fact
    embeddings have been indexed; otherwise ``semantic_facts`` stays empty and the
    default behavior is unchanged. ``config`` defaults to the module ``Config``.
    """
    config = config or Config
    instance_id = instance_id or config.instance_id

    # Layer 1 — identity: pinned facts + ALL directiva facts (always).
    core_rows = await load_facts(instance_id)
    core = resolve(
        [f for f in core_rows if f.pinned or f.tipo == "directiva"]
    )

    # Persona directives ride with identity; fold in directiva-fact contenidos,
    # de-duplicated, so the same directive isn't listed twice.
    directives: list[str] = list(load_identity(instance_id).directives)
    seen = {d.strip().casefold() for d in directives}
    pinned_facts: list[Fact] = []
    for f in core:
        if f.tipo == "directiva":
            norm = f.contenido.strip().casefold()
            if norm not in seen:
                seen.add(norm)
                directives.append(f.contenido)
        else:  # a pinned dato
            pinned_facts.append(f)

    # Layer 2 — active context by tags (exact membership, NOT semantics).
    core_ids = {f.id for f in core}
    tag_facts = [
        f for f in resolve(await load_by_tags(instance_id, active_tags))
        if f.id not in core_ids
    ]

    # Layer 3 — recency (continuity).
    episodes = await db.load_recent_episodes(instance_id, recent_n)

    # Layer 4 — semantic retrieval (embeddings). OFF unless enabled + a query.
    semantic_facts: list[Fact] = []
    if getattr(config, "memory_layer4_enabled", False) and query:
        exclude = core_ids | {f.id for f in tag_facts}
        semantic_facts = await _semantic_layer(
            instance_id,
            query,
            config,
            exclude_ids=exclude,
            topk=getattr(config, "memory_semantic_topk", 5),
        )

    # Layer 5 — episodic recall (Fase 1). Lowest priority, purely additive: only
    # when a query is given, and never allowed to raise (see _recall_layer).
    recall_episodes: list[dict] = []
    if query:
        recall_episodes = await _recall_layer(instance_id, query)

    # Layer 6 — document perception (knowledge roots). Lowest priority,
    # purely additive: only when a query is given, never allowed to raise.
    knowledge_chunks: list[dict] = []
    if query:
        knowledge_chunks = await _knowledge_layer(instance_id, query, config)

    ctx = Context(
        instance_id=instance_id,
        directives=directives,
        pinned_facts=pinned_facts,
        tag_facts=tag_facts,
        episodes=episodes,
        semantic_facts=semantic_facts,
        recall_episodes=recall_episodes,
        knowledge_chunks=knowledge_chunks,
        token_budget=token_budget,
    )
    return truncate(ctx, token_budget)
