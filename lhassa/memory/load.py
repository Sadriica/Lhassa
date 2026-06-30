"""
"Subir al despertar" — the layered load of the chica (live context) from the
grande, up to a token budget (§2.3).

Layers, in priority order:
  1. pinned facts + ALL ``directiva`` facts (ALWAYS — they are identity; the
     persona's own directives are folded in here too).
  2. recency: the recent episodes (continuity).
  3. active context by TAGS (exact membership, NOT semantics).

Then :func:`truncate` trims to the token budget, dropping the lowest-priority
layer first (``priority=["pinned","recent","tags"]``): tags go before recent, and
the identity core (directives + pinned facts) is never dropped.

Token counting is a cheap, deterministic estimate (~4 chars/token). NO LLM, NO
embeddings. Layer 4 (embeddings) is intentionally NOT built here — default off.
"""

from dataclasses import dataclass, field

from lhassa.config import Config
from lhassa.core import db
from lhassa.memory.facts import Fact, load_by_tags, load_facts, resolve
from lhassa.persona import load_identity

# Priority of the layers, highest first. Truncation drops from the tail.
PRIORITY = ["pinned", "recent", "tags"]


def estimate_tokens(text: str) -> int:
    """Cheap deterministic token estimate: ~4 chars per token. No model."""
    if not text:
        return 0
    return (len(text) + 3) // 4


def _render_fact(fact: Fact) -> str:
    return f"- ({fact.tipo}) {fact.contenido}"


def _render_episode(ep: dict) -> str:
    resultado = ep.get("resultado") or ""
    return f"- [{ep.get('creado_en', '')}] {ep.get('despertar_por', '')}: {resultado}".rstrip(": ")


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
    token_budget: int = 0

    @property
    def facts(self) -> list[Fact]:
        """All selected facts (pinned/identity first, then tag-activated)."""
        return [*self.pinned_facts, *self.tag_facts]

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
        if self.episodes:
            lines = "\n".join(_render_episode(e) for e in self.episodes)
            blocks.append(f"Episodios recientes:\n{lines}")
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
    # important). Recent episodes outrank tag facts.
    recent_rank = PRIORITY.index("recent")
    tags_rank = PRIORITY.index("tags")
    items: list[dict] = []
    for ep in ctx.episodes:
        items.append({"rank": recent_rank, "tok": estimate_tokens(_render_episode(ep)),
                      "kind": "ep", "payload": ep})
    for f in ctx.tag_facts:
        items.append({"rank": tags_rank, "tok": estimate_tokens(_render_fact(f)),
                      "kind": "fact", "payload": f})

    used = mandatory + sum(it["tok"] for it in items)
    # Drop lowest-priority (highest rank) items, from the tail, until we fit.
    while used > token_budget and items:
        drop_idx = max(range(len(items)), key=lambda i: (items[i]["rank"], i))
        used -= items[drop_idx]["tok"]
        items.pop(drop_idx)

    ctx.episodes = [it["payload"] for it in items if it["kind"] == "ep"]
    ctx.tag_facts = [it["payload"] for it in items if it["kind"] == "fact"]
    return ctx


async def load(
    instance_id: str | None = None,
    *,
    token_budget: int,
    active_tags=(),
    recent_n: int = 20,
) -> Context:
    """
    Build the chica for ``instance_id`` by layering the grande, then truncate to
    ``token_budget``. See module docstring for the layers and their priority.

    ``active_tags`` selects layer 2 (active context). Layer 4 (embeddings) is NOT
    built — default off.
    """
    instance_id = instance_id or Config.instance_id

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

    ctx = Context(
        instance_id=instance_id,
        directives=directives,
        pinned_facts=pinned_facts,
        tag_facts=tag_facts,
        episodes=episodes,
        token_budget=token_budget,
    )
    return truncate(ctx, token_budget)
