"""The capability catalog — the self-routing consciousness's SHELL half.

Lhassa's self-improvement loop (:mod:`lhassa.agents.repetition`) has always found
repeated COSTLY judgment patterns and proposed minting a deterministic action for
them. This module closes a companion loop: a message class that already HAS a
deterministic, $0 recognizer/handler (the calendar-query seam is the first one,
:func:`lhassa.calendar.query.is_calendar_query` / :func:`lhassa.calendar.query.
answer`) but that still falls through to a real think or the research bridge —
because no ROUTE has been turned on for it yet.

The layering is deliberate (see the module's callers in
:mod:`lhassa.notify.telegram_inbound`): this is a SHELL (``notify/``), so it is
free to import ``calendar.query`` (leaf), ``agents.repetition`` is never imported
FROM here (the dependency runs the other way — the consciousness only ever reads
episode TEXT this module wrote, via plain SQL, never a Python import). That keeps
``agents/`` — an ADAPTER — completely shell-free.

Three moving pieces:

  :data:`CAPABILITIES` — the catalog: name, a pure ``recognizer(text) -> bool``,
      a description, and the deterministic ``handler(text, *, config) -> str |
      None`` coroutine that actually answers it.
  :func:`match` / :func:`tag_intent` — the ROUTER-SIDE marker: ``tag_intent``
      appends ``" [routable:<cap>]"`` to an episode's ``decision`` text whenever a
      message that fell through to a think/research WAS recognised by a
      capability — pure text, no DB write of its own. The consciousness
      (:func:`lhassa.agents.repetition.detect_routable`) later reads this marker
      back with plain SQL over the episodes table.
  :func:`apply_route` / :func:`add_route` / :func:`apply_approved_route` — the
      route TABLE, stored as DATA (JSON, ``config.routes_file`` — mirrors
      :mod:`lhassa.notify.commands`'s watch-rules file). Applying an approved
      ``mint_route`` proposal never generates code: it just adds
      ``{capability: {...}}`` to this file, and every subsequent message the
      capability recognizes is served by the SAME handler the catalog already
      had — the fix was always there, the human just switched it on.

Every public function here is defensive: pure, $0, never raises — a malformed
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


async def _calendar_handler(text: str, *, config=None) -> Optional[str]:
    """The ``calendar_view`` deterministic handler — $0, no model.

    Thin wrapper over :func:`lhassa.calendar.query.answer` so the catalog entry
    below stays a plain data row. ``None`` when the calendar seam itself can't
    resolve an answer (no parseable window, etc.) — the caller then falls
    through to its normal path, exactly like today's calendar short-circuit.
    """
    from lhassa.calendar import query as _cal_query

    instance_id = getattr(config, "instance_id", None) or "inst_personal"
    return await _cal_query.answer(instance_id, text, config=config)


def _calendar_recognizer(text: str) -> bool:
    from lhassa.calendar.query import is_calendar_query

    return is_calendar_query(text)


class Capability:
    """One entry in the catalog: a name, a $0 recognizer, and its handler."""

    __slots__ = ("name", "recognizer", "description", "handler")

    def __init__(
        self, name: str, recognizer: Recognizer, description: str, handler: Handler
    ) -> None:
        self.name = name
        self.recognizer = recognizer
        self.description = description
        self.handler = handler


# The catalog. Structured so a future capability (e.g. "remind_view") is a
# one-line addition — no core edit, and (per the standing "every swap point is
# a registry" principle) new entries only ever get APPENDED here.
CAPABILITIES: list[Capability] = [
    Capability(
        "calendar_view",
        _calendar_recognizer,
        "Answer 'what's on my calendar' style questions straight from the "
        "calendar feed — $0, no model, no web search.",
        _calendar_handler,
    ),
]


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
    never raises — a recognizer that itself raises is treated as "no match" for
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


# The exact marker text written to an episode's ``decision`` — read back by
# lhassa.agents.repetition.detect_routable via plain SQL/regex, never a Python
# import (keeps agents/ shell-free).
_TAG_RE = re.compile(r"\[routable:([a-z0-9_]+)\]", re.IGNORECASE)


def tag_intent(text: str) -> str:
    """The ``" [routable:<cap>]"`` suffix for an episode's decision text.

    Empty string when ``text`` matches no capability — a caller can always
    append this directly to its decision string with no conditional needed.
    Never raises.
    """
    cap = match(text)
    return f" [routable:{cap}]" if cap else ""


# ── the route table — DATA, not code ─────────────────────────────────────────


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
    """Turn ON routing for ``capability`` — the ONLY effect of approving a
    ``mint_route`` proposal. Pure DATA: no code is generated or executed.

    Refuses (returns ``False``, writes nothing) an unknown capability name —
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
    """Apply an approved ``mint_route`` approval's payload — the approval-
    ->apply connection point.

    Called from wherever a ``mint_route`` approval is resolved ``approved=True``
    (today: :mod:`lhassa.notify.telegram_inbound`'s inline-keyboard callback
    handler and the ``/approve`` chat command — the SAME two call-sites that
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
    capability has no active route yet (:func:`is_routed` is False — the
    default, deny-by-default state before an owner approves the proposal); or
    the handler itself can't resolve an answer (e.g. the calendar seam
    recognises the message but can't parse a time window) — any of these
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
