# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic chat-intent classifier — the router for the answer cache.

"Thinking is the EXCEPTION" (§CLAUDE.md): before a Telegram turn spends a model
call, this module answers one cheap ($0, no model, no embeddings) question —
*what kind of message is this?* — so the caller (``notify.telegram_inbound``)
can decide whether an answer cache lookup even applies.

Four buckets, in priority order (the first matching rule wins):

  "command"    — starts with ``/`` (the slash-command registry already handles
                 these before the brain is ever asked; this bucket exists so a
                 caller can short-circuit without re-deriving the check).
  "live_data"  — time-sensitive: news, prices, weather, scores, "today"/"now"/
                 "hoy"/"ahora" — anything whose correct answer changes over time.
                 MUST NEVER be served from the answer cache (a cached "today's
                 weather" is a lie tomorrow) — this is exactly what already
                 routes to the research bridge (``actionpack/_research.py``,
                 ``notify.telegram_inbound``'s ``[[research: …]]`` marker).
  "cacheable"  — a STABLE factual/how-to/definitional question: the answer
                 doesn't rot (a capital city, a definition, "how do I install
                 X"). Safe to serve from a past answer once semantically
                 similar enough.
  "judgment"   — everything else (small talk, opinions, anything about "my"
                 state/plans, ambiguous asks) — always needs a real think.

Pure rules + keyword/regex signals. No model, no embeddings, no I/O — a single
function call, always $0, never raises (a malformed/None input degrades to
"judgment", the safest default: when in doubt, actually think).
"""

from __future__ import annotations

import re

# ── live_data: time-sensitive, must never be cached ─────────────────────────
# Reuses the same news/today vocabulary as actionpack/_research.py (the
# existing live-data router) plus prices/weather/scores, which that module
# doesn't need to detect itself (the research bridge only sharpens an ALREADY
# live-data query — this classifier is what routes it there in the first place).
_LIVE_DATA_RE = re.compile(
    r"\b("
    r"noticias?|news|headlines?|titulares|última hora|breaking|"
    r"hoy|today|ahora|right now|esta (mañana|tarde|noche)|tonight|"
    r"latest|últim[oa]s?( hora)?|"
    r"precio(s)?|price(s)?|cotizaci[oó]n|tipo de cambio|exchange rate|"
    r"clima|weather|pronóstico|forecast|temperatura actual|"
    r"resultado(s)? (del|de la)|score|marcador|"
    r"bolsa|stock price|acciones hoy|"
    r"qué hora es|what time is it"
    r")\b",
    re.IGNORECASE,
)

# ── cacheable: stable factual / how-to / definitional questions ─────────────
# A positive-signal list (NOT "any question is cacheable") — a question needs
# to actually look definitional/how-to shaped to land here; anything else
# (opinions, plans, small talk) falls through to judgment.
_CACHEABLE_RE = re.compile(
    r"^\s*("
    r"what\s+(is|are|was|were)\b|"
    r"who\s+(is|was|are|were)\b|"
    r"where\s+is\b|"
    r"when\s+(is|was|did)\b|"
    r"which\s+(is|are)\b|"
    r"how\s+(do|does|did|can|to|many|much)\b|"
    r"why\s+(is|does|do)\b|"
    r"explain\b|define\b|definition of\b|meaning of\b|"
    r"qu[ée]\s+(es|son|significa|fue)\b|"
    r"qui[ée]n\s+(es|fue|son)\b|"
    r"c[oó]mo\s+(se|puedo|hago|funciona)\b|"
    r"cu[áa]l\s+(es|son)\b|"
    r"cu[áa]ndo\s+(es|fue)\b|"
    r"d[oó]nde\s+(es|est[áa])\b|"
    r"por\s*qu[ée]\b"
    r")"
)

# "capital of France" / "capital de Francia" style — cacheable even mid-sentence.
_CACHEABLE_ANYWHERE_RE = re.compile(
    r"\b(capital of|capital de|how (to|do i)|c[oó]mo (hago|instalo|configuro))\b",
    re.IGNORECASE,
)


def classify(text: str) -> str:
    """Classify one inbound chat message into a $0 intent bucket.

    Returns one of ``"command"``, ``"live_data"``, ``"cacheable"``, ``"judgment"``.
    Never raises: a falsy/non-string input is treated as ``"judgment"`` (the
    safe default — an ambiguous message always gets a real think, never a bad
    cache hit).
    """
    try:
        stripped = (text or "").strip()
    except Exception:
        return "judgment"
    if not stripped:
        return "judgment"

    if stripped.startswith("/"):
        return "command"

    # live_data takes priority over cacheable — "what's the weather today"
    # matches both a cacheable "what is" prefix AND a live-data keyword; the
    # correctness rule (never cache time-sensitive data) must win.
    if _LIVE_DATA_RE.search(stripped):
        return "live_data"

    if _CACHEABLE_RE.match(stripped) or _CACHEABLE_ANYWHERE_RE.search(stripped):
        return "cacheable"

    return "judgment"
