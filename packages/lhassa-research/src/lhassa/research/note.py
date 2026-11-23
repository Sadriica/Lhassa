# SPDX-License-Identifier: AGPL-3.0-or-later
"""draft_research_note; the evidence machine behind Lhassa's research line.

The house principle (doc/research.md): knowledge is a documented byproduct of
real work. This action makes that mechanical; `gather_evidence` assembles a
$0, deterministic EVIDENCE PACK from the instruments (ledger, bench_runs,
episodes, loops, doc/decisions.md excerpts), each datum with provenance; then
ONE routine-model pass drafts a technical note that may only state numbers the
pack contains. No model available → the pack alone is written (the data is
already the value). Drafts land in ``doc/notes/drafts/<slug>.md`` with a
review-before-publishing header; a draft is NEVER published by the brain.

Deliberate, not scheduled: a note is an act, not a cadence; the doors are the
CLI (``lhassa research draft``) and the temple. Owned by the GESHE monk.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_MAX_DECISION_EXCERPTS = 6
_MAX_BENCH_ROWS = 20
_MAX_EPISODE_WINDOW_DAYS = 7
_DRAFT_HEADER = "<!-- draft: evidence-cited, review before publishing -->"
_TOPIC_STOPWORDS = {
    "about", "after", "before", "from", "into", "note", "research", "that",
    "the", "this", "with", "sobre", "para", "como", "desde", "nota",
}


def _slug(topic: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (topic or "").strip().lower()).strip("-")
    return s or "untitled"


def _checkout_root() -> Path:
    """The core checkout that holds ``doc/``: the parent of core's ``lhassa/``.

    Resolved through core (not this file) because the pack lives under
    ``packages/lhassa-research/src``; the drafts and ``doc/decisions.md`` stay
    where the research line has always kept them.
    """
    import lhassa.config

    return Path(lhassa.config.__file__).resolve().parents[1]


def _drafts_dir(config) -> Path:
    base = (getattr(config, "notes_dir", "") or "").strip()
    if base:
        return Path(base)
    # Repo-relative default: doc/notes/drafts next to doc/research.md.
    return _checkout_root() / "doc" / "notes" / "drafts"


def _topic_terms(topic: str) -> set[str]:
    return {
        word
        for word in re.split(r"[^a-zA-Z0-9áéíóúñü]+", (topic or "").lower())
        if len(word) >= 3 and word not in _TOPIC_STOPWORDS
    }


def _row_matches(row: dict, terms: set[str], fields: tuple[str, ...]) -> bool:
    if not terms:
        return False
    haystack = " ".join(str(row.get(field) or "") for field in fields).lower()
    return any(term in haystack for term in terms)


def _within_window(value, cutoff: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed >= cutoff
    except Exception:
        return False


async def gather_evidence(topic: str, config) -> dict:
    """Assemble the evidence pack; deterministic, $0, provenance per datum.

    Never raises; a failing instrument contributes an honest ``error`` entry
    instead of silence, so the pack always says what it could and couldn't see.
    """
    gathered = datetime.now(timezone.utc)
    terms = _topic_terms(topic)
    cutoff = gathered - timedelta(days=_MAX_EPISODE_WINDOW_DAYS)
    pack: dict = {
        "topic": topic,
        "gathered_at": gathered.isoformat(),
        "scope": {
            "topic_terms": sorted(terms),
            "episode_window_days": _MAX_EPISODE_WINDOW_DAYS,
            "rule": (
                "instrument rows are included only when a topic term occurs "
                "in their identifying fields"
            ),
        },
        "sources": [],
        "operational_context": {},
    }
    instance_id = getattr(config, "instance_id", None) or "inst_personal"

    try:
        from lhassa.llm import gateway

        spend = await gateway.spend_summary(config)
        pack["operational_context"]["ledger_today"] = spend
        pack["sources"].append(
            "operational context only: ledger spend_summary "
            "(today UTC; not topic-attributed)"
        )
    except Exception as exc:
        pack["operational_context"]["ledger_today"] = {"error": str(exc)}

    try:
        from lhassa.core import db

        await db.init_db()
        bench_all = await db.list_bench_runs(instance_id, limit=200)
        bench = [
            row
            for row in bench_all
            if _row_matches(row, terms, ("id", "fixture_id", "subject", "detail"))
        ][:_MAX_BENCH_ROWS]
        pack["bench_runs"] = [
            {
                k: r.get(k)
                for k in (
                    "id", "fixture_id", "subject", "passed", "metric", "detail",
                    "cost_usd", "duration_ms", "created_at",
                )
            }
            for r in bench
        ]
        if bench:
            pack["sources"].append(
                f"bench_runs table ({len(bench)} topic-matched rows, ids preserved)"
            )

        recent_all = await db.load_recent_episodes(instance_id, 500)
        recent = [
            row
            for row in recent_all
            if _within_window(row.get("creado_en"), cutoff)
            and _row_matches(
                row,
                terms,
                ("id", "despertar_por", "decision", "accion", "resultado", "dominio"),
            )
        ]
        thinks = [e for e in recent if str(e.get("decision") or "").startswith("think:")]
        zero_cost = [e for e in recent if not e.get("costo_usd")]
        pack["episodes_window"] = {
            "sampled": len(recent),
            "thinks": len(thinks),
            "think_cost_usd": round(sum(float(e.get("costo_usd") or 0) for e in thinks), 6),
            "zero_cost_acts": len(zero_cost),
            "row_ids": [e.get("id") for e in recent],
            "since": cutoff.isoformat(),
        }
        if recent:
            pack["sources"].append(
                f"episodes ({len(recent)} topic-matched rows since "
                f"{cutoff.date()}, ids preserved)"
            )

        loops_all = await db.list_loops(instance_id, limit=200)
        loops = [
            row
            for row in loops_all
            if _row_matches(
                row, terms, ("id", "goal", "kind", "origin", "last_verdict")
            )
        ][:50]
        if loops:
            done = [x for x in loops if x.get("status") == "done"]
            pack["loops"] = {
                "total": len(loops),
                "done": len(done),
                "capped": len([x for x in loops if x.get("status") == "capped"]),
                "avg_turns_done": round(sum(x.get("turn") or 0 for x in done) / len(done), 2) if done else None,
                "row_ids": [x.get("id") for x in loops],
            }
            pack["sources"].append(f"loops table ({len(loops)} topic-matched rows)")

        approvals = await db.load_pending_approvals(instance_id)
        pack["operational_context"]["approvals_pending"] = len(approvals)
        tools = []
        try:
            from lhassa.agents import versioning

            tools = await versioning.list_tools(instance_id)  # may not exist; guarded
        except Exception:
            pass
        if tools:
            pack["operational_context"]["self_written_tools"] = len(tools)
    except Exception as exc:
        pack["db_error"] = str(exc)

    try:
        decisions = _checkout_root() / "doc" / "decisions.md"
        text = decisions.read_text(encoding="utf-8", errors="replace")
        needles = [w for w in re.split(r"[^a-zA-Z0-9]+", topic.lower()) if len(w) >= 4]
        excerpts = []
        for para in text.split("\n\n"):
            low = para.lower()
            if needles and any(n in low for n in needles):
                excerpts.append(para.strip()[:400])
            if len(excerpts) >= _MAX_DECISION_EXCERPTS:
                break
        pack["decisions_excerpts"] = excerpts
        if excerpts:
            pack["sources"].append(f"doc/decisions.md ({len(excerpts)} matching excerpts)")
    except Exception as exc:
        pack["decisions_excerpts"] = []
        pack["decisions_error"] = str(exc)

    return pack


def _render_pack(pack: dict) -> str:
    import json

    return json.dumps(pack, indent=2, ensure_ascii=False, default=str)


async def draft_research_note(params: dict) -> str:
    """Draft one technical note from real evidence. Never raises.

    params: topic (required), lang ("en" default), config (tests).
    """
    try:
        if not isinstance(params, dict):
            params = {}
        from lhassa.config import Config

        cfg = params.get("config") or Config
        topic = str(params.get("topic") or "").strip()
        if not topic:
            return "draft_research_note: no topic given; pass topic=<what the note is about>."
        lang = str(params.get("lang") or "en").strip().lower()

        pack = await gather_evidence(topic, cfg)
        pack_text = _render_pack(pack)

        body = ""
        model_used = "none"
        try:
            provider = params.get("provider")
            model = params.get("model")
            if provider is None:
                # No explicit provider (the normal path) → the router decides.
                from lhassa.core.watchlist import Trigger
                from lhassa.llm import router
                from lhassa.memory import load as memory_load

                trigger = Trigger(kind="research", action="draft_note", params={})
                ctx = await memory_load.load(
                    getattr(cfg, "instance_id", None) or "inst_personal",
                    token_budget=600,
                    query=topic,
                )
                decision = router.decide(trigger, ctx, config=cfg)
                provider = decision.provider
                if provider is not None:
                    model = router.pick_model(provider, hard=False, config=cfg)
            if provider is not None:
                system = (
                    "You are Lhassa drafting a Witara technical note (doc/research.md's "
                    "method): calm, no hype, claim → cited evidence. HARD RULE: every "
                    "number and factual claim MUST come from the EVIDENCE PACK below; "
                    "if the pack doesn't support a claim, write what's missing instead "
                    "of inventing it. Rows are topic evidence only when listed under "
                    "bench_runs, episodes_window row_ids, loops, or decisions_excerpts; "
                    "operational_context is global context and MUST NOT be presented as "
                    "evidence for the topic. Check every value against the verbatim pack. "
                    f"Write in {'Spanish' if lang == 'es' else 'English'}. "
                    "Structure: a one-paragraph thesis, the evidence (cite the pack's "
                    "source names inline), what we do NOT yet know, and the next "
                    "measurement worth taking. 400-700 words."
                )
                result = await provider.complete(
                    [{"role": "user", "content": f"Topic: {topic}\n\nEVIDENCE PACK:\n{pack_text}"}],
                    model=model,
                    max_tokens=1500,
                    system=system,
                )
                body = (result.text or "").strip()
                model_used = f"{getattr(provider, 'name', '?')}/{model or 'default'}"
        except Exception as exc:
            body = ""
            model_used = f"degraded ({exc})"

        drafts = _drafts_dir(cfg)
        drafts.mkdir(parents=True, exist_ok=True)
        path = drafts / f"{_slug(topic)}.md"
        content = [
            _DRAFT_HEADER,
            f"<!-- topic: {topic} | drafted: {pack['gathered_at']} | model: {model_used} -->",
            "",
            body if body else "_No model available; evidence pack only; the data below is the draft._",
            "",
            "---",
            "",
            "## Appendix; evidence pack (verbatim, per-datum provenance)",
            "",
            "```json",
            pack_text,
            "```",
            "",
        ]
        path.write_text("\n".join(content), encoding="utf-8")
        kind = "note draft" if body else "evidence pack (no model)"
        return f"draft_research_note: {kind} written to {path}; review before publishing."
    except Exception as exc:
        return f"draft_research_note errored: {exc}"
