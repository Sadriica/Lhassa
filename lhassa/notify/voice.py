# SPDX-License-Identifier: AGPL-3.0-or-later
"""The notify voice; reshape machine-built notifications into Lhassa's own voice.

Owner's ask, verbatim: *"a brain that, before sending notification/automation
messages, interprets them and makes them more Lhassa or more human-like; it can
have templates depending on the notification kind."*

This module is the ONE place that turns a robotic line like::

    ⚙️ check_spend: check_spend: 2040 output tokens today (budget unlimited, 0%
    used), cost $0.0053

into something a person would actually read. It sits behind
:func:`lhassa.notify.dispatch.notify` (the ONE fan-out choke point), so every
channel gets the same voiced text; no per-channel special-casing.

**One source of voice.** Every lead phrase this module uses comes from
:data:`lhassa.persona.LEAD_PHRASES` (and its Spanish mirror); the SAME table
:func:`lhassa.persona.format_alert` draws from. Before this module and
``persona.py`` were unified they carried duplicated, silently-drifting copies
of the same phrases ("Heads up: " here vs "Heads up -- " there); the
deterministic plane consulted its own near-copy instead of the one place that
actually defines Lhassa's voice. There is now exactly one vocabulary.

Four deterministic layers, same degrade discipline as the digest's
``digest_style`` (:mod:`lhassa.notify.digest`):

  * :func:`classify`: an explicit ``kind`` always wins; otherwise a cheap,
    regex-only sniff of the text. $0, never raises.
  * :func:`template`: the $0 reshaping layer. Per-kind deterministic templates
    (the ``TEMPLATES`` registry): strip robotic prefixes, dedupe a doubled
    action name, naturalize obvious ``key=value`` tails, look up a natural
    first-person lead per action via :mod:`lhassa.notify.phrasebook` (with an
    intent-based outer framing; "Done: " / "Heads up: " / "I learned
    something: ", point 5 of Stream 4, not one blanket framing for every
    action-result). When unsure, it passes the text through verbatim with
    only the framing added; it NEVER drops information. Idempotent: a text
    that already carries a known lead phrase (ours, or the monitor's own
    :func:`lhassa.persona.format_alert` leads) is returned unchanged.
  * **Identity reaches the $0 plane** (no model): :func:`template` also
    localizes the lead phrase to the owner's language
    (``config.digest_language``, reused as the general notify-language
    signal; ``LEAD_PHRASES_ES`` if it starts with ``"es"``) and applies the
    identity's own directives; concretely, a "keep it concise" directive
    (loaded via :func:`lhassa.persona.load_identity`) caps the rendered
    length. Both are pure post-processing over already-templated text; a
    broken identity registry degrades to the untouched text, never a raised
    exception.
  * :func:`revoice`: the style switch (``config.notify_style``):
    ``"plain"`` leaves the text untouched, ``"template"`` (the default) applies
    :func:`template`, ``"persona"`` adds ONE cheap-model re-voicing pass on top
    of the template (mirrors :func:`lhassa.notify.digest._persona_pass`
    exactly: cheap/fast model, bounded ``max_tokens``, the identity's
    persona block as system prompt, same language as the input, every fact
    kept). ANY failure in the persona pass; a missing provider, a raised
    exception, an empty completion; degrades to the template() result; a
    notification ALWAYS arrives.

The ``report`` kind is special: the digest already re-voices itself
(``digest_style = "persona"``, see :mod:`lhassa.notify.digest`), so
``template()`` for ``"report"`` is pass-through-verbatim; callers that hand
in an already-voiced digest (``kind="report"``) are never double-voiced.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from loguru import logger

from lhassa import persona
from lhassa.notify import phrasebook

# The closed set of notification kinds this module understands. ``classify``
# always returns one of these; ``template`` treats anything else as "generic".
KINDS = ("alert", "reminder", "action-result", "approval", "report", "generic")

# One-line first-person framings, added ONCE per kind (never for "report" or
# "generic": those pass through as-is beyond the light cleanup below).
# Sourced from :data:`lhassa.persona.LEAD_PHRASES`: the one vocabulary (see
# module docstring). Extra keys in that table not in ``KINDS`` (e.g.
# ``"learned"``, used only for the action-result intent framing below) are
# simply unused here.
_FRAMING: dict[str, str] = persona.LEAD_PHRASES

# The idempotency sentinel: a text that already starts with ANY known lead
# phrase; ours, or ``persona.format_alert``'s own leads (which now draw from
# the exact same table); is considered already voiced and returned unchanged
# rather than double-framed. English and Spanish leads both count, so a
# reprocessed already-localized message stays a no-op.
_ALL_LEADS = tuple(
    sorted(
        {
            p.strip().lower()
            for table in (persona.LEAD_PHRASES, persona.LEAD_PHRASES_ES)
            for p in table.values()
            if p.strip()
        }
    )
)


def _already_voiced(text: str) -> bool:
    low = text.strip().lower()
    return any(low.startswith(lead) for lead in _ALL_LEADS)


def classify(text: str, kind: Optional[str] = None) -> str:
    """Return the notification kind for ``text``. Explicit ``kind`` always wins.

    Otherwise a cheap, deterministic, regex-only sniff of the text shape; no
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
    """Turn obvious ``key=value`` tails into ``key: value``: trivially safe,
    never touches anything that isn't a plain ``word=token`` pair."""
    return _KV_RE.sub(lambda m: f"{m.group(1)}: {m.group(2)}", text)


def _extract_action(text: str) -> "tuple[str | None, str]":
    """Pull ``(action_name, rest)`` out of a machine-built action-result line.

    Handles the doubled-name shape (``"check_spend: check_spend: rest"``) and
    the plain leading-name shape (``"check_spend: rest"``); strips a leading
    ``⚙️`` first. Returns ``(None, naturalized_text)`` when neither shape
    matches; the caller falls back to passing the text through untouched.
    """
    t = text.strip()
    if t.startswith("⚙️"):
        t = t[len("⚙️"):].strip()

    m = _DOUBLED_ACTION_RE.match(t)
    if m:
        return m.group(1), _naturalize_kv(m.group(2))

    m = _LEADING_ACTION_RE.match(t)
    if m:
        return m.group(1), _naturalize_kv(m.group(2))

    return None, _naturalize_kv(t)


def _clean_action_result(text: str) -> str:
    """Turn a machine-built action-result line into a natural sentence.

    ``"⚙️ check_spend: check_spend: 2040 output tokens..."`` becomes
    ``"I checked spend: 2040 output tokens..."`` when ``check_spend`` has a
    :mod:`lhassa.notify.phrasebook` entry (the obligation; see that module's
    parity test), or falls back to the mechanical
    ``"Check-spend: 2040 output tokens..."`` rendering for an action this
    table doesn't (yet) know. When the shape doesn't match at all, the text is
    naturalized (key=value tails only) and passed through. NEVER dropped.
    """
    action, rest = _extract_action(text)
    if action is None:
        return rest

    lead = phrasebook.phrase_for(action)
    if lead:
        return f"{lead}: {rest}" if rest else lead
    return f"{_title_action(action)}: {rest}" if rest else _title_action(action)


def _template_report(text: str, _kind: str) -> str:
    # The digest is already voiced (plain or persona) before it ever reaches
    # here; pass-through-verbatim, byte for byte.
    return text


def _template_action_result(text: str, kind: str) -> str:
    # Templates by INTENT, not only by kind (Stream 4, point 5): most
    # action-results are "I finished something" ("Done: "), but a few are
    # "something needs your attention" ("Heads up: ") or "I learned
    # something": see lhassa.notify.phrasebook.intent_for.
    action, _rest = _extract_action(text)
    intent = phrasebook.intent_for(action) if action else kind
    framing = _FRAMING.get(intent, _FRAMING[kind])
    return framing + _clean_action_result(text)


def _template_framed(text: str, kind: str) -> str:
    return _FRAMING[kind] + _naturalize_kv(text.strip())


def _template_generic(text: str, _kind: str) -> str:
    # No framing for generic text; light-touch naturalization only, so an
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


# ── Identity reaches the $0 plane (no model) ────────────────────────────────
# The owner's language and hard limits, applied as pure deterministic
# post-processing over already-templated text; never a raise, never a cost.

# A directive naming any of these (case-insensitive substring) is read as
# "keep notifications brief": the same signal an English "keep it concise"
# or "be brief" directive would give. The default personal identity carries
# "responder conciso por defecto" out of the box (see persona.py), so this is
# live for the owner today, not a hypothetical.
_CONCISE_MARKERS = ("conciso", "concise", "brief", "breve")
_CONCISE_MAX_CHARS = 480


def _localize_lead(text: str, config) -> str:
    """Swap an English lead phrase for its Spanish mirror when the owner's
    configured notify language calls for it (``notify_language``, falling back
    to ``digest_language``: the same one knob the model-written messages are
    pinned to, so the $0 lead and the re-voiced body never disagree)."""
    if not text or config is None:
        return text
    lang = (
        getattr(config, "notify_language", "")
        or getattr(config, "digest_language", "")
        or ""
    ).strip().lower()
    if not lang.startswith("es"):
        return text
    for key, en_lead in persona.LEAD_PHRASES.items():
        if en_lead and text.startswith(en_lead):
            return persona.LEAD_PHRASES_ES.get(key, en_lead) + text[len(en_lead):]
    return text


def _apply_identity_limits(text: str, config) -> str:
    """Apply the loaded identity's directives to templated text; concretely,
    a "keep it concise" directive caps the length. Any failure (a broken
    identity registry) leaves ``text`` untouched; identity must never cost a
    notification."""
    if not text:
        return text
    try:
        instance_id = getattr(config, "instance_id", None) if config is not None else None
        identity = persona.load_identity(instance_id)
        concise = any(
            marker in directive.lower()
            for directive in identity.directives
            for marker in _CONCISE_MARKERS
        )
        if concise and len(text) > _CONCISE_MAX_CHARS:
            return text[: _CONCISE_MAX_CHARS - 1].rstrip() + "…"
    except Exception as exc:  # identity must never cost a notification
        logger.debug(f"notify-voice: identity limits skipped: {exc}")
    return text


def template(text: str, kind: str, config=None) -> str:
    """The $0 deterministic reshaping layer for one notification ``kind``.

    Idempotent: re-voicing an already-voiced text (starts with a known framing
    or lead phrase) is a no-op; returns ``text`` unchanged (only localized,
    never re-framed or re-truncated). Unrecognized ``kind`` falls back to the
    generic (light-touch, no framing) template. ``config``, when given, lets
    the owner's identity reach this $0 plane; see :func:`_localize_lead` /
    :func:`_apply_identity_limits`. Never raises; never drops information.
    """
    if not text or not text.strip():
        return text
    t = text
    if kind != "report" and _already_voiced(t):
        return _localize_lead(t, config)

    fn = TEMPLATES.get(kind, _template_generic)
    try:
        out = fn(t, kind)
    except Exception as exc:  # the $0 layer must never break a notification
        logger.debug(f"notify-voice: template({kind!r}) degraded to verbatim: {exc}")
        return text
    out = _localize_lead(out, config)
    return _apply_identity_limits(out, config)


# ── The persona layer (one cheap-model pass, opt-in via notify_style) ──────

# The re-voicing instruction, layered under the identity's persona block.
# Written against measured output, not intuition: the earlier version ("first
# person, warm, calm and BRIEF") produced flat, PASSIVE lines that read like a
# clerk's log ("Una falla nueva quedó registrada") and padded them with filler
# offers the brain never made ("dime cuándo quieres que lo miremos juntos").
# Naming the failure modes is what fixed it: demand the active first person,
# forbid the passive, forbid inventing a next step, and require that a failure
# be called a failure. Compare before changing this: the same three fixtures
# through both versions is a 30-second check.
_REVOICE_INSTRUCTION = (
    "The line below is a machine-built notification about YOUR OWN work. Say it "
    "as yourself, to your owner, in ONE or TWO short sentences.\n"
    "- Speak in the FIRST PERSON about what you did or found ('I ran', 'I "
    "found', 'I am watching'). You are the one who did the work: never describe "
    "it from the outside, and never use the passive voice.\n"
    "- Keep every fact, number, name and id EXACTLY as given. Never invent one, "
    "never drop one. If something failed, say plainly that it failed.\n"
    "- Translate the machine's jargon into what it MEANS for your owner, but "
    "keep the technical name so it stays searchable.\n"
    "- The monk's register: short sentences, present tense, understatement. "
    "Calm, never alarmed, never cheerful about a failure. No preamble, no "
    "sign-off, no 'let me know'. Never use an em dash; use a colon, a semicolon "
    "or a comma.\n"
    "- Report only what ALREADY happened. Never say you are looking into it, "
    "will fix it, or are about to do anything: nothing runs after this message, "
    "so any such promise is a lie. Never offer help you were not asked for.\n"
    "Answer with the rewritten notification ONLY, no explanation. "
)

# Two worked examples, threaded as real chat turns before the actual line.
# Rules alone did not hold this model: across repeated runs of one fixture the
# same instruction produced first person, third person and passive voice by
# turns, and once dropped the action's technical name. The examples pin what
# the prose could not (person, length, keeping the identifier, stopping without
# an offer of help), so they must DEMONSTRATE every one of those: an example
# that paraphrases away the action name teaches exactly that, which is how the
# first draft of this table made the output worse.
#
# Keyed by the pinned language, because an example is only useful in the
# language the answer is written in. A language with no entry falls through to
# the rules alone (the previous behaviour), never to examples in a language the
# owner does not read.
_REVOICE_EXAMPLES: dict[str, list[dict]] = {
    "es": [
        {"role": "user", "content": "Sync-repo: 2 repos synced, 1 skipped (dirty tree)"},
        {
            "role": "assistant",
            "content": (
                "Sincronicé 2 repos con sync_repo; salté 1 porque tenía cambios "
                "sin guardar."
            ),
        },
        {
            "role": "user",
            "content": (
                "Something needs a look: 1 new failure: distill_memory: fail; "
                "provider unreachable"
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Encontré un fallo nuevo: distill_memory no pudo correr, no "
                "alcancé al proveedor."
            ),
        },
    ],
    "en": [
        {"role": "user", "content": "Sync-repo: 2 repos synced, 1 skipped (dirty tree)"},
        {
            "role": "assistant",
            "content": "I synced 2 repos with sync_repo; I skipped 1, it had uncommitted changes.",
        },
        {
            "role": "user",
            "content": (
                "Something needs a look: 1 new failure: distill_memory: fail; "
                "provider unreachable"
            ),
        },
        {
            "role": "assistant",
            "content": "I found a new failure: distill_memory could not run, I could not reach the provider.",
        },
    ],
}


def _revoice_examples(config) -> list[dict]:
    """The few-shot turns for the owner's pinned language ([] when unknown)."""
    lang = (
        getattr(config, "notify_language", "")
        or getattr(config, "digest_language", "")
        or ""
    ).strip().lower()[:2]
    return list(_REVOICE_EXAMPLES.get(lang, ()))


async def _persona_pass(text: str, kind: str, config) -> str:
    """Re-voice ``text`` as Lhassa via ONE cheap, fast model call.

    Mirrors :func:`lhassa.notify.digest._persona_pass`: the API gateway's
    routine (cheap) model, the instance's persona block as the system prompt,
    the owner's pinned language, every fact/number/id kept exactly, plus the
    few-shot turns in :data:`_REVOICE_EXAMPLES`. Raises on failure;
    :func:`revoice` catches and falls back to :func:`template`.
    Logs one best-effort Episode (``dominio="scribe"``: the temple's docs
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
        + _REVOICE_INSTRUCTION
        + persona.language_directive(
            config, "Reply in the SAME language as the notification below."
        )
    )
    examples = _revoice_examples(config)
    if examples:
        system += (
            " The exchanges before the real notification are examples of your "
            "voice; follow them."
        )
    completion = await provider.complete(
        [*examples, {"role": "user", "content": text}],
        model=model,
        max_tokens=200,
        system=system,
    )
    out = persona.scrub_dashes((completion.text or "").strip()) or text

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

    ``"plain"``: unchanged. ``"template"`` (default); :func:`template` only,
    $0. ``"persona"``: :func:`template` then one cheap-model pass; ANY
    failure there degrades to the template() result. Never raises; a
    notification must ALWAYS arrive.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    style = (getattr(config, "notify_style", "template") or "template").strip().lower()
    if style == "plain":
        return text

    resolved_kind = classify(text, kind)
    templated = template(text, resolved_kind, config)

    if style != "persona":
        return templated

    try:
        return await _persona_pass(templated, resolved_kind, config)
    except Exception as exc:
        logger.debug(f"notify-voice: persona pass degraded to template: {exc}")
        return templated


# ── The phrasebook; deterministic, $0 translation of an action's outcome ──────
#
# `template()` frames a notification ("Heads up: …"); it does not TRANSLATE it.
# So an internal action's own line reached the owner verbatim:
#
#     guardian completed check_integrity: fail. 1 critical (run conf_3bc1d93…)
#
# Every part of that is machine vocabulary: the action's name, "1 critical", an
# opaque run id. The phrasebook turns a known action's outcome into a sentence a
# person can act on, with NO model call.
#
# Two rules keep it honest:
#   * an entry returns ``None`` when it cannot parse its own action's output;
#     the caller then falls back to the raw line rather than inventing a summary;
#   * unregistered actions simply have no entry, so nothing changes for them.
#
# Add one with @register_phrase("<action>"); a drop-in, like every other
# registry in the codebase.

PHRASES: "dict[str, Callable[[str], Optional[str]]]" = {}


def register_phrase(action: str):
    """Register the human phrasing for one action's result (decorator)."""

    def _wrap(fn):
        PHRASES[action] = fn
        return fn

    return _wrap


def phrase(action: str, result: str) -> Optional[str]:
    """A plain sentence for ``action``'s ``result``, or ``None`` if unknown.

    Never raises: a phrasing that blows up degrades to ``None``, which the
    caller reads as "say it the old way".
    """
    fn = PHRASES.get((action or "").strip())
    if fn is None:
        return None
    try:
        said = fn(str(result or ""))
        return said.strip() if said and said.strip() else None
    except Exception as exc:
        logger.debug(f"notify-voice: phrase({action!r}) degraded: {exc}")
        return None


def _count(pattern: str, text: str) -> Optional[int]:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def _plural(n: int, singular: str, plural: Optional[str] = None) -> str:
    """"1 problem" / "2 problems": a "(s)" suffix is what machines write."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


@register_phrase("check_integrity")
def _phrase_check_integrity(result: str) -> Optional[str]:
    """The owner does not know what a "contract" or a run id is; they know
    whether something needs their attention, and what to type to see it."""
    if "every contract passed" in result:
        return "All my internal checks passed; nothing needs you."
    critical = _count(r"(\d+) critical", result) or 0
    warnings = _count(r"(\d+) warning", result) or 0
    if not critical and not warnings:
        return None
    if critical and warnings:
        what = f"{_plural(critical, 'serious problem')} and {warnings} minor"
    elif critical:
        what = _plural(critical, "serious problem")
    else:
        what = _plural(warnings, "minor problem")
    return (
        f"Something needs a look: {what} in my own internal checks. "
        "Run `lhassa integrity findings` to see what failed."
    )


@register_phrase("curate_skills")
def _phrase_curate_skills(result: str) -> Optional[str]:
    # The action reports verb-first: "checked 12, stale 2, archived 3, …".
    archived = _count(r"archived (\d+)", result)
    stale = _count(r"stale (\d+)", result)
    if archived:
        return (
            f"I tidied my skills: {_plural(archived, 'skill')} went unused for months, "
            f"so I archived {'it' if archived == 1 else 'them'}; nothing was deleted."
        )
    if stale is not None and not stale:
        return "I checked my skills: all still in use, nothing to tidy."
    return None


@register_phrase("review_tools")
def _phrase_review_tools(result: str) -> Optional[str]:
    if "none retired" in result:
        return "I reviewed the tools I wrote myself; all still earning their keep."
    retired = _count(r"retired (\d+)", result)
    if retired:
        return (
            f"I retired {_plural(retired, 'tool')} I had written myself; "
            "they stopped being useful."
        )
    return None


@register_phrase("review_capabilities")
def _phrase_review_capabilities(result: str) -> Optional[str]:
    if "no dormant capabilities" in result:
        return "Everything I can do is still being used."
    proposed = _count(r"(\d+) proposed", result)
    if proposed:
        return (
            f"I found {_plural(proposed, 'thing')} I can do that nobody uses. "
            f"I left you a proposal to retire {'it' if proposed == 1 else 'them'}"
            "; see /approvals."
        )
    return None


@register_phrase("detect_repetition")
def _phrase_detect_repetition(result: str) -> Optional[str]:
    # The action reports "N pattern(s) found, M newly proposed".
    proposed = _count(r"(\d+) newly proposed", result)
    if proposed:
        return (
            f"I noticed {_plural(proposed, 'thing')} I keep doing over and over, "
            f"and left you a proposal to make {'it' if proposed == 1 else 'them'} "
            "automatic; see /approvals."
        )
    if proposed == 0:
        return "I looked for things I repeat too often; nothing new this time."
    return None


@register_phrase("calendar_sync")
def _phrase_calendar_sync(result: str) -> Optional[str]:
    unsynced = _count(r"(\d+) unsynced", result)
    delivered = _count(r"(\d+) delivered", result)
    if unsynced is None:
        return None
    if unsynced == 0:
        return "Your calendar and my reminders are in step; nothing to sync."
    return (
        f"I found {_plural(unsynced, 'calendar event')} I wasn't tracking and "
        f"set up {delivered or 0} of them as reminders."
    )


@register_phrase("derive_world_facts")
def _phrase_derive_world_facts(result: str) -> Optional[str]:
    new = _count(r"(\d+) new", result)
    if new is None:
        return None
    if new == 0:
        return "I re-read your calendar for new routines; nothing I didn't already know."
    return f"I learned {_plural(new, 'new thing')} about your routine from your calendar."


@register_phrase("apply_feedback")
def _phrase_apply_feedback(result: str) -> Optional[str]:
    if "no scorable signals" in result:
        return "I checked how my recent answers landed; not enough signal to tell yet."
    return None


@register_phrase("draft_research_note")
def _phrase_draft_research_note(result: str) -> Optional[str]:
    if "written to" in result:
        return (
            "I drafted a research note for you. It is review-only; nothing is "
            "published until you read it."
        )
    return None
