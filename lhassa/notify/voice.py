"""The notify voice — reshape machine-built notifications into Lhassa's own voice.

Owner's ask, verbatim: *"a brain that, before sending notification/automation
messages, interprets them and makes them more Lhassa or more human-like; it can
have templates depending on the notification kind."*

This module is the ONE place that turns a robotic line like::

    ⚙️ check_spend: check_spend: 2040 output tokens today (budget unlimited, 0%
    used), cost $0.0053

into something a person would actually read. It sits behind
:func:`lhassa.notify.dispatch.notify` (the ONE fan-out choke point), so every
channel gets the same voiced text — no per-channel special-casing.

Three deterministic layers, same degrade discipline as the digest's
``digest_style`` (:mod:`lhassa.notify.digest`):

  * :func:`classify` — an explicit ``kind`` always wins; otherwise a cheap,
    regex-only sniff of the text. $0, never raises.
  * :func:`template` — the $0 reshaping layer. Per-kind deterministic templates
    (the ``TEMPLATES`` registry): strip robotic prefixes, dedupe a doubled
    action name, naturalize obvious ``key=value`` tails, and add ONE line of
    first-person framing. When unsure, it passes the text through verbatim
    with only the framing added — it NEVER drops information. Idempotent: a
    text that already carries the framing (ours, or the monitor's own
    :func:`lhassa.persona.format_alert` leads) is returned unchanged.
  * :func:`revoice` — the style switch (``config.notify_style``):
    ``"plain"`` leaves the text untouched, ``"template"`` (the default) applies
    :func:`template`, ``"persona"`` adds ONE cheap-model re-voicing pass on top
    of the template (mirrors :func:`lhassa.notify.digest._persona_pass`
    exactly: cheap/fast model, bounded ``max_tokens``, the identity's
    persona block as system prompt, same language as the input, every fact
    kept). ANY failure in the persona pass — a missing provider, a raised
    exception, an empty completion — degrades to the template() result; a
    notification ALWAYS arrives.

The ``report`` kind is special: the digest already re-voices itself
(``digest_style = "persona"``, see :mod:`lhassa.notify.digest`), so
``template()`` for ``"report"`` is pass-through-verbatim — callers that hand
in an already-voiced digest (``kind="report"``) are never double-voiced.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from loguru import logger

# The closed set of notification kinds this module understands. ``classify``
# always returns one of these; ``template`` treats anything else as "generic".
KINDS = ("alert", "reminder", "action-result", "approval", "report", "generic")

# One-line first-person framings, added ONCE per kind (never for "report" or
# "generic" — those pass through as-is beyond the light cleanup below). These
# same strings are also the idempotency sentinel: a text that already starts
# with one of these (ours, or the pre-existing monitor leads from
# ``lhassa.persona.format_alert``) is considered already voiced and returned
# unchanged rather than double-framed.
_FRAMING: dict[str, str] = {
    "reminder": "You asked me to remind you: ",
    "action-result": "Done — ",
    "alert": "Heads up: ",
    "approval": "Waiting on your word: ",
}

# Extra known lead phrases that count as "already voiced" even though they
# were not produced by this module — chiefly ``persona.format_alert``'s own
# leads ("Heads up — ...", "Something needs a look — ...") so a watcher alert
# that is already Lhassa-voiced is not re-wrapped with a second framing line.
_EXTRA_LEADS = (
    "something needs a look",
    "heads up",
)


def _already_voiced(text: str) -> bool:
    low = text.strip().lower()
    for framing in _FRAMING.values():
        if low.startswith(framing.strip().lower()):
            return True
    return any(low.startswith(lead) for lead in _EXTRA_LEADS)


def classify(text: str, kind: Optional[str] = None) -> str:
    """Return the notification kind for ``text``. Explicit ``kind`` always wins.

    Otherwise a cheap, deterministic, regex-only sniff of the text shape — no
    model, never raises. Unrecognized shapes fall back to ``"generic"``.
    """
    if kind:
        return kind.strip().lower()

    t = (text or "").strip()
    low = t.lower()

    if t.startswith("⚙️"):
        return "action-result"
    if "reminder" in low or "recuérdame" in low or "recuerdame" in low or "recuérda" in low:
        return "reminder"
    if "approval" in low or "/approvals" in low:
        return "approval"
    if low.startswith("heads up") or low.startswith("something needs a look") or "monitor ·" in low:
        return "alert"
    return "generic"


# ── The $0 template layer ──────────────────────────────────────────────────

# Matches a doubled machine action name, e.g. "check_spend: check_spend: rest"
# (with or without a leading "⚙️ " already stripped).
_DOUBLED_ACTION_RE = re.compile(r"^([\w.\-]+):\s*\1:\s*(.*)$")
_LEADING_ACTION_RE = re.compile(r"^([\w.\-]+):\s*(.*)$")
_KV_RE = re.compile(r"(?<![:\w])([A-Za-z_][\w]*)=([^\s,;]+)")


def _title_action(name: str) -> str:
    """``check_spend`` -> ``Check-spend`` (dash-joined, first segment capitalized)."""
    parts = name.replace("_", "-").split("-")
    if not parts:
        return name
    parts[0] = parts[0].capitalize()
    return "-".join(parts)


def _naturalize_kv(text: str) -> str:
    """Turn obvious ``key=value`` tails into ``key: value`` — trivially safe,
    never touches anything that isn't a plain ``word=token`` pair."""
    return _KV_RE.sub(lambda m: f"{m.group(1)}: {m.group(2)}", text)


def _clean_action_result(text: str) -> str:
    """Strip the ``⚙️`` prefix and dedupe a doubled action name.

    ``"⚙️ check_spend: check_spend: 2040 output tokens..."`` becomes
    ``"Check-spend: 2040 output tokens..."``. When the shape doesn't match, the
    text is naturalized (key=value tails only) and passed through — NEVER
    dropped.
    """
    t = text.strip()
    if t.startswith("⚙️"):
        t = t[len("⚙️"):].strip()

    m = _DOUBLED_ACTION_RE.match(t)
    if m:
        return f"{_title_action(m.group(1))}: {_naturalize_kv(m.group(2))}"

    m = _LEADING_ACTION_RE.match(t)
    if m:
        return f"{_title_action(m.group(1))}: {_naturalize_kv(m.group(2))}"

    return _naturalize_kv(t)


def _template_report(text: str, _kind: str) -> str:
    # The digest is already voiced (plain or persona) before it ever reaches
    # here — pass-through-verbatim, byte for byte.
    return text


def _template_action_result(text: str, kind: str) -> str:
    return _FRAMING[kind] + _clean_action_result(text)


def _template_framed(text: str, kind: str) -> str:
    return _FRAMING[kind] + _naturalize_kv(text.strip())


def _template_generic(text: str, _kind: str) -> str:
    # No framing for generic text — light-touch naturalization only, so an
    # arbitrary/unknown-shaped message is never editorialized.
    return _naturalize_kv(text.strip())


# kind -> (text, kind) -> str. Drop-in per-kind reshaping, no core edit needed
# to add a new kind (register it here + add it to KINDS + _FRAMING as needed).
TEMPLATES: dict[str, Callable[[str, str], str]] = {
    "report": _template_report,
    "action-result": _template_action_result,
    "reminder": _template_framed,
    "alert": _template_framed,
    "approval": _template_framed,
    "generic": _template_generic,
}


def template(text: str, kind: str) -> str:
    """The $0 deterministic reshaping layer for one notification ``kind``.

    Idempotent: re-voicing an already-voiced text (starts with a known framing
    or lead phrase) is a no-op — returns ``text`` unchanged. Unrecognized
    ``kind`` falls back to the generic (light-touch, no framing) template.
    Never raises; never drops information.
    """
    if not text or not text.strip():
        return text
    t = text
    if kind != "report" and _already_voiced(t):
        return text

    fn = TEMPLATES.get(kind, _template_generic)
    try:
        return fn(t, kind)
    except Exception as exc:  # the $0 layer must never break a notification
        logger.debug(f"notify-voice: template({kind!r}) degraded to verbatim: {exc}")
        return text


# ── The persona layer (one cheap-model pass, opt-in via notify_style) ──────


async def _persona_pass(text: str, kind: str, config) -> str:
    """Re-voice ``text`` as Lhassa via ONE cheap, fast model call.

    Mirrors :func:`lhassa.notify.digest._persona_pass`: the API gateway's
    routine (cheap) model, the instance's persona block as the system prompt,
    same language as the input, every fact/number/id kept exactly. Raises on
    failure — :func:`revoice` catches and falls back to :func:`template`.
    Logs one best-effort Episode (``dominio="scribe"`` — the temple's docs
    monk owns the voice); a logging failure never costs the re-voiced text.
    """
    from lhassa.llm import router
    from lhassa.llm.provider import get_model_provider
    from lhassa.persona import load_identity

    instance_id = getattr(config, "instance_id", None) or "inst_personal"
    provider = get_model_provider(config)
    model = router.pick_model(provider, hard=False, config=config)
    identity = load_identity(instance_id)
    system = (
        identity.persona_block
        + "\n\n"
        + "Rewrite the notification below as yourself speaking directly to your "
          "owner: first person, warm, calm and BRIEF (one or two short "
          "sentences). Use the monk's register: patient, frugal, attentive, "
          "humble — never mystical, never preachy. Keep every fact, number and "
          "id EXACTLY as given — never invent or drop one. Reply in the SAME "
          "language as the notification below."
    )
    completion = await provider.complete(
        [{"role": "user", "content": text}],
        model=model,
        max_tokens=200,
        system=system,
    )
    out = (completion.text or "").strip() or text

    try:
        from lhassa.core.episode import Episode

        ep = Episode.begin(despertar_por="notify-voice", instance_id=instance_id)
        ep.dominio = "scribe"
        ep.finish(
            decision="think:notify-voice",
            accion="revoice",
            resultado=f"kind={kind}",
            costo_usd=getattr(completion, "cost_usd", 0.0) or 0.0,
        )
        await ep.append()
    except Exception as exc:  # logging is best-effort, never costs the reply
        logger.debug(f"notify-voice: episode logging skipped: {exc}")

    return out


async def revoice(text: str, *, kind: Optional[str] = None, config=None) -> str:
    """Reshape ``text`` into Lhassa's own voice per ``config.notify_style``.

    ``"plain"`` — unchanged. ``"template"`` (default) — :func:`template` only,
    $0. ``"persona"`` — :func:`template` then one cheap-model pass; ANY
    failure there degrades to the template() result. Never raises — a
    notification must ALWAYS arrive.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    style = (getattr(config, "notify_style", "template") or "template").strip().lower()
    if style == "plain":
        return text

    resolved_kind = classify(text, kind)
    templated = template(text, resolved_kind)

    if style != "persona":
        return templated

    try:
        return await _persona_pass(templated, resolved_kind, config)
    except Exception as exc:
        logger.debug(f"notify-voice: persona pass degraded to template: {exc}")
        return templated
