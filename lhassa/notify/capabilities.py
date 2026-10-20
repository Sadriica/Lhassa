# SPDX-License-Identifier: AGPL-3.0-or-later
"""The capability catalog; the self-routing consciousness's SHELL half.

Lhassa's self-improvement loop (:mod:`lhassa.agents.repetition`) has always found
repeated COSTLY judgment patterns and proposed minting a deterministic action for
them. This module closes a companion loop: a message class that already HAS a
deterministic, $0 recognizer/handler (the calendar pack's ``calendar_view`` is
the first one) but that still falls through to a real think or the research
bridge; because no ROUTE has been turned on for it yet.

The catalog is a registry: core ships it EMPTY and a capability pack appends
its entry with :func:`register_capability` (no pack installed, no capability,
every function below degrades to "no match").

The layering is deliberate (see the module's callers in
:mod:`lhassa.notify.telegram_inbound`): this is a SHELL (``notify/``);
``agents.repetition`` is never imported
FROM here (the dependency runs the other way; the consciousness only ever reads
episode TEXT this module wrote, via plain SQL, never a Python import). That keeps
``agents/`` (an ADAPTER) completely shell-free.

Three moving pieces:

  :data:`CAPABILITIES`: the catalog: name, a pure ``recognizer(text) -> bool``,
      a description, and the deterministic ``handler(text, *, config) -> str |
      None`` coroutine that actually answers it.
  :func:`match` / :func:`tag_intent`: the ROUTER-SIDE marker: ``tag_intent``
      appends ``" [routable:<cap>]"`` to an episode's ``decision`` text whenever a
      message that fell through to a think/research WAS recognised by a
      capability; pure text, no DB write of its own. The consciousness
      (:func:`lhassa.agents.repetition.detect_routable`) later reads this marker
      back with plain SQL over the episodes table.
  :func:`apply_route` / :func:`add_route` / :func:`apply_approved_route`: the
      route TABLE, stored as DATA (JSON, ``config.routes_file``: mirrors
      :mod:`lhassa.notify.commands`'s watch-rules file). Applying an approved
      ``mint_route`` proposal never generates code: it just adds
      ``{capability: {...}}`` to this file, and every subsequent message the
      capability recognizes is served by the SAME handler the catalog already
      had; the fix was always there, the human just switched it on.

Every public function here is defensive: pure, $0, never raises; a malformed
input degrades to "no capability" / "no route" so a caller can always fall
through to its existing (unchanged) path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

Recognizer = Callable[[str], bool]
Handler = Callable[..., Awaitable[Optional[str]]]
# ``(followup_text, context, *, config) -> str | None``: re-interprets an
# elliptical follow-up ("Y el sábado?") FOR this one capability, given the
# sticky-topic context the previous turn recorded. ``None`` when the
# capability can't resolve the follow-up either; the caller then falls
# through to the normal chat path, exactly like a Handler miss.
ContinueFollowup = Callable[..., Awaitable[Optional[str]]]


#: ``(text, *, config) -> (reply, decision) | None``: an ALWAYS-ON deterministic
#: answer served before the think (no route needed), with the ``decision`` text
#: its episode records. ``None`` when the capability does not answer ``text``.
Shortcut = Callable[..., Awaitable[Optional[tuple[str, str]]]]
#: ``(text) -> dict``: the sticky-topic context a message carries for this
#: capability (the calendar remembers which day was asked about). Falsy values
#: are ignored when refreshing a topic.
TopicContext = Callable[[str], dict]


class Capability:
    """One entry in the catalog: a name, a $0 recognizer, and its handler."""

    __slots__ = (
        "name",
        "recognizer",
        "description",
        "handler",
        "continue_followup",
        "shortcut",
        "topic_context",
    )

    def __init__(
        self,
        name: str,
        recognizer: Recognizer,
        description: str,
        handler: Handler,
        continue_followup: Optional[ContinueFollowup] = None,
        shortcut: Optional[Shortcut] = None,
        topic_context: Optional[TopicContext] = None,
    ) -> None:
        self.name = name
        self.recognizer = recognizer
        self.description = description
        self.handler = handler
        self.continue_followup = continue_followup
        self.shortcut = shortcut
        self.topic_context = topic_context


# The catalog. Core ships it empty; a pack appends its entry through
# register_capability (per the standing "every swap point is a registry"
# principle: no core edit). A capability with no ``continue_followup`` simply
# can't be continued; an elliptical follow-up sticking to its topic falls
# through to the normal chat path, exactly as if no topic were set at all.
CAPABILITIES: list[Capability] = []


def register_capability(cap: Capability) -> Capability:
    """Append ``cap`` to the catalog. Re-registering a name is a no-op (the
    first registration wins), so a pack imported twice never duplicates."""
    existing = get_capability(cap.name)
    if existing is not None:
        return existing
    CAPABILITIES.append(cap)
    return cap


def topic_context(cap: Optional[Capability], text: str) -> dict:
    """The sticky-topic context ``text`` carries for ``cap``; ``{}`` when the
    capability declares none or its hook fails. Never raises."""
    if cap is None or cap.topic_context is None:
        return {}
    try:
        ctx = cap.topic_context(text)
        return dict(ctx) if isinstance(ctx, dict) else {}
    except Exception:
        return {}


def get_capability(name: str) -> Optional[Capability]:
    """The catalog entry named ``name``, or ``None``. Never raises."""
    try:
        for cap in CAPABILITIES:
            if cap.name == name:
                return cap
    except Exception:
        return None
    return None


def match(text: str) -> Optional[str]:
    """The name of the first capability whose recognizer matches ``text``.

    ``None`` when nothing matches (or ``text`` is falsy/malformed). Pure, $0,
    never raises; a recognizer that itself raises is treated as "no match" for
    that one capability rather than aborting the whole scan.
    """
    try:
        stripped = (text or "").strip()
    except Exception:
        return None
    if not stripped:
        return None
    for cap in CAPABILITIES:
        try:
            if cap.recognizer(stripped):
                return cap.name
        except Exception:
            continue
    return None


# The exact marker text written to an episode's ``decision``: read back by
# lhassa.agents.repetition.detect_routable via plain SQL/regex, never a Python
# import (keeps agents/ shell-free).
_TAG_RE = re.compile(r"\[routable:([a-z0-9_]+)\]", re.IGNORECASE)


def tag_intent(text: str) -> str:
    """The ``" [routable:<cap>]"`` suffix for an episode's decision text.

    Empty string when ``text`` matches no capability; a caller can always
    append this directly to its decision string with no conditional needed.
    Never raises.
    """
    cap = match(text)
    return f" [routable:{cap}]" if cap else ""


# ── is_followup(); the connector / elliptical-fragment detector ────────────
#
# Pure $0, no model: this is the deterministic signal the sticky-topic thread-
# continuity hook (:mod:`lhassa.notify.telegram_inbound`) uses to decide
# whether an incoming message is trying to CONTINUE the previous capability's
# topic rather than opening something new. Two rules, checked on the
# accent-stripped, lower-cased, punctuation-stripped word list:
#
#   1. The message STARTS WITH a connector word ("y"/"e"/"o"/"u"/"también"/
#      "and"/"or"/"also") or connector phrase ("what about", "and the"); this
#      alone covers every example in the brief ("Y el sabado?", "¿y en
#      marzo?", "también el lunes", "and Saturday?").
#   2. OR the message is very short (≤4 words) AND doesn't open like a full
#      new utterance: its first word isn't a question-starter (qué/como/
#      cuándo/... or what/how/when/...) or a greeting/interjection (hola/hi/
#      thanks/...), and it contains no common verb ("es"/"tengo"/"have"/...).
#      This is intentionally conservative; a bare "hola" or "cómo estás" must
#      NOT read as a follow-up, and any full sentence over 4 words never
#      reaches this branch at all.
#
# ``is_followup`` is deliberately loose beyond that: it does NOT check whether
# the fragment is semantically about the topic's subject (a date, a place,
# ...); that judgment belongs to the capability's own ``continue_followup``
# (e.g. the calendar continuation declines with ``None`` when the follow-up
# carries no parseable time reference, even though it read as a followup).
_CONNECTOR_WORDS = {"y", "e", "o", "u", "tambien", "and", "or", "also"}
_CONNECTOR_PHRASES = ("what about", "and the")
_QUESTION_STARTERS = {
    "que", "como", "cuando", "donde", "quien", "cual", "cuanto",
    "why", "how", "when", "where", "who", "which", "what",
    "is", "are", "do", "does", "did", "would", "could", "can", "will",
}
_GREETING_WORDS = {
    "hola", "hi", "hello", "hey", "buenas", "gracias", "thanks",
    "bye", "adios", "chau", "ok", "vale",
}
_VERB_DENYLIST = {
    "es", "esta", "soy", "estoy", "tengo", "tienes", "quiero", "necesito",
    "hay", "voy", "puedo", "sabes", "have", "want", "need", "am", "was",
}
_WORD_RE = re.compile(r"[a-z]+")


def _followup_words(text: str) -> list[str]:
    """Lowercase, accent-stripped word tokens (matching-only; never displayed)."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WORD_RE.findall(stripped.lower())


def is_followup(text: Any) -> bool:
    """Is ``text`` a connector-led / elliptical follow-up? See module comment
    above for the exact rules. Pure, $0, never raises."""
    try:
        stripped = (text or "").strip()
    except Exception:
        return False
    if not stripped:
        return False
    try:
        words = _followup_words(stripped)
    except Exception:
        return False
    if not words:
        return False

    if words[0] in _CONNECTOR_WORDS:
        return True
    joined = " ".join(words)
    if any(joined.startswith(phrase) for phrase in _CONNECTOR_PHRASES):
        return True

    if len(words) <= 4:
        first = words[0]
        if first in _QUESTION_STARTERS or first in _GREETING_WORDS:
            return False
        if any(w in _VERB_DENYLIST for w in words):
            return False
        return True
    return False


# ── the route table. DATA, not code ─────────────────────────────────────────


def _routes_path(config=None) -> Optional[Path]:
    """Resolve ``config.routes_file`` (or the ``Config`` default). Never raises."""
    try:
        raw = getattr(config, "routes_file", "") if config is not None else ""
        if not raw:
            from lhassa.config import Config

            raw = getattr(Config, "routes_file", "") or str(
                Path.home() / ".lhassa" / "routes.json"
            )
        return Path(raw)
    except Exception:
        return None


def load_routes(config=None) -> dict[str, Any]:
    """The route table: ``{capability: {...}}``. ``{}`` on any trouble."""
    path = _routes_path(config)
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_routes(routes: dict[str, Any], *, config=None) -> bool:
    """Persist the route table. Returns whether the write succeeded; never raises."""
    path = _routes_path(config)
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(routes, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning(f"capabilities: could not save routes: {exc}")
        return False


def is_routed(capability: str, *, config=None) -> bool:
    """Whether ``capability`` has an active route entry. Never raises."""
    try:
        return bool(capability) and capability in load_routes(config)
    except Exception:
        return False


def add_route(capability: str, *, config=None, **extra: Any) -> bool:
    """Turn ON routing for ``capability``: the ONLY effect of approving a
    ``mint_route`` proposal. Pure DATA: no code is generated or executed.

    Refuses (returns ``False``, writes nothing) an unknown capability name;
    routing something the catalog doesn't recognise would silently no-op
    forever, so it's rejected up front instead. Idempotent: routing an already
    -routed capability again just re-saves the same entry.
    """
    if get_capability(capability) is None:
        return False
    routes = load_routes(config)
    routes[capability] = {"capability": capability, **extra}
    return save_routes(routes, config=config)


def remove_route(capability: str, *, config=None) -> bool:
    """Turn OFF routing for ``capability``. ``False`` if it wasn't routed."""
    routes = load_routes(config)
    if capability not in routes:
        return False
    del routes[capability]
    return save_routes(routes, config=config)


def apply_approved_route(payload: dict, *, config=None) -> bool:
    """Apply an approved ``mint_route`` approval's payload; the approval-
    ->apply connection point.

    Called from wherever a ``mint_route`` approval is resolved ``approved=True``
    (today: :mod:`lhassa.notify.telegram_inbound`'s inline-keyboard callback
    handler and the ``/approve`` chat command; the SAME two call-sites that
    already special-case ``tipo == "code_run"`` to launch a run post-resolve).
    Reads ``payload["capability"]`` and turns routing on for it via
    :func:`add_route`. Returns whether a route was actually added; never raises.
    """
    try:
        capability = str((payload or {}).get("capability") or "").strip()
    except Exception:
        return False
    if not capability:
        return False
    return add_route(capability, config=config)


async def apply_route(text: str, *, config=None) -> Optional[str]:
    """Serve ``text`` for $0 through an ACTIVE route, or ``None``.

    ``None`` whenever: no capability recognises ``text``; the recognised
    capability has no active route yet (:func:`is_routed` is False; the
    default, deny-by-default state before an owner approves the proposal); or
    the handler itself can't resolve an answer (e.g. the calendar seam
    recognises the message but can't parse a time window); any of these
    degrades to "let the caller fall through to its normal path", exactly the
    behavior before this module existed. Never raises.
    """
    cap_name = match(text)
    if cap_name is None:
        return None
    if not is_routed(cap_name, config=config):
        return None
    cap = get_capability(cap_name)
    if cap is None:
        return None
    try:
        return await cap.handler(text, config=config)
    except Exception as exc:
        logger.warning(f"capabilities: apply_route {cap_name!r} failed: {exc}")
        return None
