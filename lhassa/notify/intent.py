# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic chat-intent classifier; the router for the answer cache.

"Thinking is the EXCEPTION" (doc/concepts.md): before a Telegram turn spends a model
call, this module answers one cheap ($0, no model, no embeddings) question;
*what kind of message is this?*; so the caller (``chat.think``)
can decide whether an answer cache lookup even applies.

Four buckets, in priority order (the first matching rule wins):

  "command"   : starts with ``/`` (the slash-command registry already handles
                 these before the brain is ever asked; this bucket exists so a
                 caller can short-circuit without re-deriving the check).
  "live_data" : time-sensitive: news, prices, weather, scores, "today"/"now"/
                 "hoy"/"ahora": anything whose correct answer changes over time.
                 MUST NEVER be served from the answer cache (a cached "today's
                 weather" is a lie tomorrow); this is exactly what already
                 routes to the research bridge (``actionpack/_research.py``,
                 ``chat.markers``' ``[[research: …]]`` marker).
  "cacheable" : a STABLE factual/how-to/definitional question: the answer
                 doesn't rot (a capital city, a definition, "how do I install
                 X"). Safe to serve from a past answer once semantically
                 similar enough.
  "judgment"  : everything else (small talk, opinions, anything about "my"
                 state/plans, ambiguous asks); always needs a real think.

Pure rules + keyword/regex signals. No model, no embeddings, no I/O; a single
function call, always $0, never raises (a malformed/None input degrades to
"judgment", the safest default: when in doubt, actually think).
"""

from __future__ import annotations

import re

# ── live_data: time-sensitive, must never be cached ─────────────────────────
# Reuses the same news/today vocabulary as actionpack/_research.py (the
# existing live-data router) plus prices/weather/scores, which that module
# doesn't need to detect itself (the research bridge only sharpens an ALREADY
# live-data query; this classifier is what routes it there in the first place).
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
# A positive-signal list (NOT "any question is cacheable"); a question needs
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

# "capital of France" / "capital de Francia" style; cacheable even mid-sentence.
_CACHEABLE_ANYWHERE_RE = re.compile(
    r"\b(capital of|capital de|how (to|do i)|c[oó]mo (hago|instalo|configuro))\b",
    re.IGNORECASE,
)


def classify(text: str) -> str:
    """Classify one inbound chat message into a $0 intent bucket.

    Returns one of ``"command"``, ``"live_data"``, ``"cacheable"``, ``"judgment"``.
    Never raises: a falsy/non-string input is treated as ``"judgment"`` (the
    safe default; an ambiguous message always gets a real think, never a bad
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

    # live_data takes priority over cacheable; "what's the weather today"
    # matches both a cacheable "what is" prefix AND a live-data keyword; the
    # correctness rule (never cache time-sensitive data) must win.
    if _LIVE_DATA_RE.search(stripped):
        return "live_data"

    if _CACHEABLE_RE.match(stripped) or _CACHEABLE_ANYWHERE_RE.search(stripped):
        return "cacheable"

    return "judgment"


# ── work-type classification (Plan 4, Stream 1; task identification) ───────
#
# A SECOND, independent $0 axis over the same inbound text: not "can this be
# cached" but "what KIND of work is this": the input to the working-mode
# registry (``notify.modes``) and the effort resolver (``core.effort``). Pure
# regex + optional episode signals (never a model), same "never raises,
# degrades to the safest default" contract as :func:`classify` above. The
# default here is ``"question"`` (answer directly, minimal effort) rather than
# ``classify``'s ``"judgment"``: the safest default for THIS axis is the
# cheapest mode, not the most expensive one; a genuinely hard ask still gets
# routed to real judgment inside its mode/effort, it just isn't assumed to be
# expensive before there's any signal that it is.
#
# Six buckets, checked in priority order (most specific verb-shaped signal
# first; "question" is both a positive match AND the catch-all):
#   "build"      : write/implement/fix/create code or a concrete artifact.
#   "review"     : look over/audit/critique existing work.
#   "maintenance": housekeeping: clean up, sync, archive, update in place.
#   "decision"   : choose between options, a recommendation is requested.
#   "research"   : investigate/find out/compare, an open-ended lookup.
#   "question"   : a direct ask with a bounded answer (default).
WORK_TYPES = ("question", "research", "build", "review", "maintenance", "decision")

_BUILD_RE = re.compile(
    r"\b("
    r"build|implement|write (a |the )?(code|script|function|tool|test)|"
    r"create (a|an|the)|add (a|an|the) (feature|endpoint|command|function|test)|"
    r"fix (the |this |a )?(bug|issue|error)|refactor|"
    r"escribe (un |una |el |la )?(c[oó]digo|script|funci[oó]n)|"
    r"implementa|arregla (el |la |un |una )?(bug|error)|"
    r"crea (un|una)|agrega (un|una)|corrige"
    r")\b",
    re.IGNORECASE,
)

_REVIEW_RE = re.compile(
    r"\b("
    r"review|audit|critique|check (my|this|the)|look over|"
    r"what do you think of (this|my)|feedback on|"
    r"revisa|audita|eval[uú]a|qu[eé] opinas de (este|esta|mi)|"
    r"dame (tu )?opini[oó]n"
    r")\b",
    re.IGNORECASE,
)

_MAINTENANCE_RE = re.compile(
    r"\b("
    r"clean\s*up|archive|sync\b|synchroni[sz]e|housekeeping|"
    r"update the|tidy up|maintain|"
    r"limpia|sincroniza|mantenimiento|archiva|actualiza (el|la)"
    r")\b",
    re.IGNORECASE,
)

_DECISION_RE = re.compile(
    r"\b("
    r"should i|which (one|is better)|help me (decide|choose)|"
    r"recommend|pros and cons|what.s better|worth it\??$|"
    r"deber[ií]a|cu[aá]l (elijo|es mejor|prefieres)|qu[eé] me recomiendas|"
    r"me conviene"
    r")\b",
    re.IGNORECASE,
)

_RESEARCH_RE = re.compile(
    r"\b("
    r"research|investigate|find out|look up|dig into|"
    r"compare .* (and|vs\.?|with)|what.s (new|out there) (on|about)|"
    r"sources on|state of the art|"
    r"investiga|averigua|busca informaci[oó]n|indaga|"
    r"compara .* (y|con)"
    r")\b",
    re.IGNORECASE,
)


def classify_work_type(text: str) -> str:
    """Classify one message into a WORK TYPE (the task-identification axis).

    Returns one of :data:`WORK_TYPES`. Pure regex, no model, never raises; a
    falsy/non-string input, or text matching none of the specific buckets,
    degrades to ``"question"`` (the cheapest mode/effort combination, never a
    silently expensive default).
    """
    try:
        stripped = (text or "").strip()
    except Exception:
        return "question"
    if not stripped:
        return "question"

    for label, pattern in (
        ("build", _BUILD_RE),
        ("review", _REVIEW_RE),
        ("maintenance", _MAINTENANCE_RE),
        ("decision", _DECISION_RE),
        ("research", _RESEARCH_RE),
    ):
        if pattern.search(stripped):
            return label

    return "question"
