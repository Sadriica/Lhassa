"""Inbound Telegram poller — turns Lhassa's Telegram chat into a command channel.

Long-polls the Telegram Bot API ``getUpdates`` (offset-based) over httpx, answers
each accepted message through a **tool-less brain think** (identity + layered
memory + the router's routine provider → ``provider.complete``, no tools — the same
shape as :func:`lhassa.voice.brain_bridge.answer`, but logging a
``despertar_por="telegram"`` episode), and replies via the existing outbound
:func:`lhassa.notify.telegram.send`.

A tool-less think is deliberate: routing chat through the full 26-tool agent on the
cheap heartbeat backend produced malformed tool calls and failed replies, whereas a
plain completion on the routine (opencode) provider answers reliably.

Same discipline as the rest of :mod:`lhassa.notify`:
- **gated** — runs only when ``config.telegram_poll_enabled`` is true AND both a
  bot token and chat id are configured; otherwise it is a silent, network-free
  no-op (logged once);
- **owner-only** — messages whose ``chat.id`` is not exactly ``telegram_chat_id``
  are ignored (security: the bot answers nobody but its owner);
- **never raises** — a bad/malformed update is logged and skipped; a single error
  can never kill the poll loop.

Run standalone with ``python -m lhassa.notify.telegram_inbound``. It is **not**
wired into ``main.py`` — that is a later step.
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import re
import shutil  # noqa: F401 - re-exported so callers/tests can monkeypatch
import time
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from lhassa.notify import commands, telegram

# The getUpdates endpoint. ``<token>`` is interpolated per call.
_GET_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"

# Long-poll seconds passed to Telegram; the client timeout sits a little above it.
_LONG_POLL_SECONDS = 30
_TIMEOUT = float(_LONG_POLL_SECONDS) + 15.0

# Backoff bounds for run_forever when a cycle errors out (seconds).
_BACKOFF_START = 1.0
_BACKOFF_MAX = 60.0

# A chat turn is a short answer, not a long job.
_CHAT_MAX_TOKENS = 512

# Short fallback when the brain can't reach a model — sent verbatim to the chat.
_FALLBACK_REPLY = "I can't reach my model right now."

# Marker for an episode whose model could not run (mirrors core/step + voice).
_DEFERRED_DECISION = "deferred: telegram needs judgment"

# Chat-output instruction layered on the persona (plain text for a chat bubble).
_CHAT_INSTRUCTION = (
    "You are answering in a Telegram chat with your owner. Reply in short, natural "
    "messages. Lead with the answer and keep it brief."
)

# ── Telegram → CV work-flow bridge replies ────────────────────────────────────
# The base CV lives on disk (``cv_largo.tex``) and rarely changes, so a document
# upload is unambiguous: replacing the base is an EXPLICIT act (arm with /cvnewbase),
# and any other upload is a one-off tailor. No inline buttons — an armed flag decides.
#
# Sent when a (non-armed, caption-less) CV document arrives: we hold the CV and ask
# for the job posting in a follow-up message (many clients send a file uncaptioned).
_CV_NO_CAPTION = (
    "Got your CV ({chars} chars) — now send the job posting as a normal message and "
    "I'll tailor it. Optionally name the company with '@ Company' or 'company: X'."
)
# Confirmation after an armed /cvnewbase upload replaced the base CV.
_CV_BASE_SAVED = (
    "Base CV updated ({chars} chars; previous backed up). /cv will use it now."
)
# The document could not be downloaded / had no readable text.
_CV_NO_DOWNLOAD = "I couldn't download that document — try sending it again."
_CV_NO_TEXT = "I couldn't read any text from that document. Send a .tex or a text PDF."
# Something unexpected happened while tailoring.
_CV_ERROR = "Something went wrong tailoring your CV. Nothing was sent."

# A CV held from a caption-less upload, awaiting the job posting in a follow-up
# message. Keyed by chat id; in-memory (the poller is one long-running process).
_PENDING_CV: dict = {}
_PENDING_TTL = 1800.0  # 30 min — an older held CV is treated as stale and dropped.

# Chats ARMED via /cvnewbase for a base-CV replacement (chat id → arm timestamp).
# The next uploaded document in an armed chat replaces the on-disk base CV.
_AWAIT_BASE: dict = {}
_AWAIT_BASE_TTL = 1800.0  # 30 min — an old arm request is treated as stale and ignored.


def _pending_set(chat_id: str, base_cv: str, filename: str) -> None:
    _PENDING_CV[chat_id] = {"base_cv": base_cv, "filename": filename, "ts": time.time()}


def _pending_pop(chat_id: str) -> "dict | None":
    """Return (and clear) a non-stale held CV for ``chat_id``, else None."""
    held = _PENDING_CV.pop(chat_id, None)
    if not held:
        return None
    if time.time() - held.get("ts", 0) > _PENDING_TTL:
        return None
    return held


def _arm_base(chat_id: str) -> None:
    """Arm ``chat_id`` for a base-CV replacement (its next document upload)."""
    _AWAIT_BASE[chat_id] = time.time()


def _consume_armed(chat_id: str) -> bool:
    """Return whether ``chat_id`` is armed for a base replacement, and DISARM it.

    Pops the arm flag (so an upload consumes it exactly once) and returns True only
    when it was set within the TTL — a stale arm request is dropped and ignored.
    """
    ts = _AWAIT_BASE.pop(chat_id, None)
    if ts is None:
        return False
    return (time.time() - ts) <= _AWAIT_BASE_TTL
# ``@ Company`` / ``company: X`` extractors for the optional company name in a caption.
_COMPANY_RES = (
    re.compile(r"company\s*[:=]\s*(?P<name>[^\n]+)", re.IGNORECASE),
    re.compile(r"(?:^|\s)@\s*(?P<name>[^\n]+)"),
)

# Deterministic remember command: "remember: X", "recuerda: X", "recordá: X".
# Handled BEFORE the watch-rules hook so it is $0 (no LLM, no task mint).
_REMEMBER_RE = re.compile(
    r'^(remember|recuerda|record[áa])[,:]?\s+(.+)',
    re.IGNORECASE | re.DOTALL,
)


def _enabled(config) -> bool:
    """True only when polling is switched on AND credentials are present."""
    return bool(getattr(config, "telegram_poll_enabled", False)) and telegram.is_configured(config)


# The fallback CHAIN builder now lives in ``lhassa.llm.router`` (the interface
# layer) so both this shell (Telegram) and the spine (``core/step.py``'s judgment
# path) can walk the SAME "owner's CLI subscriptions before cloud" chain on a
# ProviderNotConfigured/BudgetExceeded. Kept as a thin, same-signature re-export
# so existing imports/tests of ``_fallback_chain`` here are unaffected.
from lhassa.llm.router import fallback_chain as _fallback_chain  # noqa: E402


def _secondary_model(config):
    """The model the cloud fallback should use (cheap routine model)."""
    return getattr(config, "model_cheap", "") or getattr(config, "model", "") or None


async def _brain_reply(text: str, *, config) -> str:
    """Answer one message via a tool-less brain think. Never raises.

    Mirrors :func:`lhassa.voice.brain_bridge.answer`: load identity + layered memory
    (with ``text`` as the semantic query), let the router pick the routine
    provider+model (so the opencode heartbeat backend is preferred), call
    ``provider.complete`` with **no tools**, and log a ``despertar_por="telegram"``
    episode. Degrades to :data:`_FALLBACK_REPLY` on a missing provider / spent
    budget / any error — it never raises into the poll loop.
    """
    from lhassa.core import db, watchlist
    from lhassa.core.episode import Episode
    from lhassa.llm import router
    from lhassa.llm.gateway import BudgetExceeded
    from lhassa.llm.provider import ProviderNotConfigured
    from lhassa.memory import load as memory_load
    from lhassa.persona import load_identity

    instance_id = getattr(config, "instance_id", None) or "inst_personal"

    # Idempotent — a chat turn must work even if it wakes the brain first.
    await db.init_db()

    ep = Episode.begin(despertar_por="telegram", instance_id=instance_id)
    reply = _FALLBACK_REPLY
    try:
        # Layer memory (semantic query = the message; layer 4 fires only when
        # enabled + indexed, otherwise it's the unchanged pinned/recent/tags).
        ctx = await memory_load.load(
            instance_id,
            token_budget=getattr(config, "context_token_budget", 2000),
            query=text,
        )

        # Routine path: not delegated, not flagged hard → the router prefers the
        # local/opencode heartbeat provider (§3-Fase 6).
        trigger = watchlist.Trigger(kind="telegram", action="responder", params={})
        decision = router.decide(trigger, ctx, config=config)

        identity = load_identity(instance_id)
        parts = [identity.persona_block]
        memory_block = ctx.to_prompt()
        if memory_block:
            parts.append(memory_block)
        parts.append(_CHAT_INSTRUCTION)
        system = "\n\n".join(parts)

        # Try the routine provider (opencode/local — $0), then walk the configured
        # fallback CHAIN: the owner's CLI subscriptions (claude → codex) before the
        # cloud gateway (default). opencode boots a fresh runtime per call and flakes
        # under the long-running poller, so the chain backs it up — the owner always
        # gets an answer. The chain is the mockable ``_fallback_chain`` seam.
        attempts: list[tuple] = []
        if decision.provider is not None:
            attempts.append((decision.provider, decision.model))
        attempts.extend(_fallback_chain(config, decision.provider))
        if not attempts:
            raise ProviderNotConfigured("router resolved no provider for telegram turn")

        last_exc = None
        for prov, mdl in attempts:
            try:
                result = await prov.complete(
                    [{"role": "user", "content": text}],
                    model=mdl,
                    max_tokens=_CHAT_MAX_TOKENS,
                    system=system,
                )
            except (ProviderNotConfigured, BudgetExceeded) as exc:
                last_exc = exc
                continue
            except Exception as exc:  # a flaky provider must not end the turn
                logger.warning(
                    f"Telegram inbound: provider {getattr(prov, 'name', '?')!r} failed: {exc}"
                )
                last_exc = exc
                continue
            reply = (result.text or "").strip() or _FALLBACK_REPLY
            ep.finish(
                decision=decision.reason,
                accion="responder",
                resultado=reply,
                costo_usd=result.cost_usd,
            )
            return reply

        # Every provider (routine + fallback chain) failed — defer, keep the episode.
        ep.finish(
            decision=_DEFERRED_DECISION,
            accion="responder",
            resultado=f"deferred: all providers failed ({last_exc})",
        )
        return _FALLBACK_REPLY

    except Exception as exc:  # never raise into the poll loop
        logger.warning(f"Telegram inbound: brain think failed: {exc}")
        ep.finish(
            decision="telegram: error",
            accion="responder",
            resultado=f"error: {type(exc).__name__}: {exc}",
        )
        return _FALLBACK_REPLY
    finally:
        # Always log the episode; a logging failure must not break the turn.
        with contextlib.suppress(Exception):
            await ep.append()


# ── Watch rules (message-pattern task minting) ───────────────────────────────


def _load_watch_rules(config) -> list[dict]:
    """Load ``~/.lhassa/watch_rules.json`` (or configured path). Never raises.

    Returns a list of validated rule dicts: {pattern: re.Pattern, accion: str,
    params: dict}. Missing/malformed file → empty list (feature silently off).
    Invalid regex entries are skipped.
    """
    path_raw = getattr(config, "watch_rules_file", "") or ""
    if not path_raw:
        return []
    import pathlib as _pathlib

    try:
        text = _pathlib.Path(path_raw).read_text(encoding="utf-8")
        raw = _json.loads(text)
    except (OSError, _json.JSONDecodeError, Exception):
        return []

    if not isinstance(raw, list):
        return []

    rules: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pattern_src = str(entry.get("pattern") or "").strip()
        accion = str(entry.get("accion") or "").strip()
        if not pattern_src or not accion:
            continue
        try:
            pattern = re.compile(pattern_src, re.IGNORECASE)
        except re.error:
            logger.debug(f"Telegram inbound: skipping invalid rule regex {pattern_src!r}")
            continue
        params = entry.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        rules.append({"pattern": pattern, "accion": accion, "params": dict(params)})
    return rules


async def _try_mint_rule_task(text: str, rules: list[dict], *, config) -> Optional[str]:
    """Try matching ``text`` against watch rules; mint the first matching task.

    Returns a short confirmation string (task id) if a rule matched and the task
    was minted, else ``None``. Never raises.
    """
    if not rules:
        return None
    try:
        from lhassa.core import db as _db
        from lhassa.core import dispatch as _dispatch
        from lhassa.security.trust import Origin, TrustLevel

        instance_id = getattr(config, "instance_id", None) or "inst_personal"
        await _db.init_db()

        for rule in rules:
            if rule["pattern"].search(text):
                origin = Origin(trust=TrustLevel.HUMANO, actor_id="telegram-rule")
                task_id, estado = await _dispatch.mint_task(
                    instance_id,
                    rule["accion"],
                    params=dict(rule["params"]),
                    origin=origin,
                    judgment=True,  # rule-minted tasks are judgment (any accion)
                )
                if task_id:
                    return task_id
                break  # rule matched but mint refused — don't try fallback rules
    except Exception as exc:
        logger.debug(f"Telegram inbound: rule task mint failed: {exc}")
    return None


async def _answer(text: str, *, config, chat_id: str = "") -> None:
    """Answer one message and reply with it. Never raises.

    Order of handling:
      1. ``/cv`` and CV work-flow commands.
      2. A pending held CV from a caption-less upload.
      3. Watch-rule matching (mint a task + reply with the id).
      4. Slash command registry (LLM-free).
      5. Brain think (the existing plain-language path).
    """
    # The ``/cv <job posting>`` command runs the CV work-flow against the on-disk base
    # CV and replies with the tailored file itself (not a plain string), so it is
    # handled here rather than through the string-returning command registry.
    stripped = text.strip()
    if stripped == "/cv" or stripped.startswith("/cv ") or stripped.startswith("/cv\n"):
        await _answer_cv_command(text, config=config)
        return

    # A CV held from a caption-less upload → this plain text IS the job posting.
    if not stripped.startswith("/"):
        held = _pending_pop(chat_id)
        if held:
            company = _parse_company(stripped)
            record, flow_id = await _run_cv_flow(
                caption=stripped, company=company, base_cv=held["base_cv"], config=config
            )
            await _reply_with_cv(record, flow_id, company, config=config)
            return

    # ``/cvnewbase`` ARMS this chat so the next uploaded document replaces the base CV
    # (the arm side-effect needs the chat id, which the string-only registry lacks; the
    # instruction reply itself is the registry command below).
    command_name, _ = commands.parse(stripped)
    if command_name == "cvnewbase":
        _arm_base(chat_id)

    # Deterministic "remember" shortcut — $0, no LLM, no task.
    # Handled before watch-rules so it short-circuits cleanly.
    if not stripped.startswith("/"):
        m = _REMEMBER_RE.match(stripped)
        if m:
            text_to_remember = m.group(2).strip()[:500]
            try:
                from lhassa.core import db as _db_remember
                from lhassa.memory.facts import add_fact as _add_fact_remember
                _instance_id = getattr(config, "instance_id", None) or "inst_personal"
                await _db_remember.init_db()
                _fact = await _add_fact_remember(
                    _instance_id,
                    tipo="dato",
                    contenido=text_to_remember,
                    procedencia="telegram-remember",
                    confianza=0.95,
                    tags=["telegram"],
                )
                await telegram.send(f"Remembered: {_fact.id}", config=config)
            except Exception as _exc:
                logger.warning(f"Telegram inbound: remember failed: {_exc}")
                with contextlib.suppress(Exception):
                    await telegram.send("Failed to remember that.", config=config)
            return

    # Watch-rule matching: check rules BEFORE the command/brain fallback. A match
    # mints a task and confirms with the task id, then returns (does not fall through).
    if not stripped.startswith("/"):
        rules = _load_watch_rules(config)
        if rules:
            task_id = await _try_mint_rule_task(stripped, rules, config=config)
            if task_id:
                try:
                    await telegram.send(
                        f"Noted. Task {task_id} minted.",
                        config=config,
                    )
                except Exception as exc:
                    logger.warning(f"Telegram inbound: rule reply send failed: {exc}")
                return

    reply = None
    try:
        reply = await commands.dispatch(text, config=config)
    except Exception as exc:  # dispatch is defensive, but belt-and-braces
        logger.warning(f"Telegram inbound: command dispatch failed: {exc}")
        reply = None

    if reply is None:
        # Not a command → let the brain answer (the existing plain-language path).
        reply = await _brain_reply(text, config=config)

    try:
        await telegram.send(reply, config=config)
    except Exception as exc:
        logger.warning(f"Telegram inbound: reply send failed: {exc}")


# ── Telegram → CV work-flow bridge ────────────────────────────────────────────
def _parse_company(caption: str) -> str | None:
    """Pull an optional company name out of a caption.

    Recognises ``company: X`` / ``company=X`` and ``@ Company`` (the ``company:`` form
    wins). Returns the trimmed name, or ``None`` when neither pattern is present — the
    flow then resolves the company to ``"untitled"``.
    """
    text = caption or ""
    for pattern in _COMPANY_RES:
        m = pattern.search(text)
        if m:
            name = m.group("name").strip().strip(".,;")
            if name:
                return name
    return None


async def _run_cv_flow(*, caption: str, company: str | None, base_cv, config):
    """Run the configured CV work-flow. Returns ``(record, flow_id)``.

    ``base_cv`` is passed straight through to :func:`run_flow` — a string (the
    uploaded CV's text) overrides the on-disk base CV, ``None`` keeps the disk base.
    ``record`` is ``None`` when the configured flow id is not stored (so the caller can
    say "not set up yet"). Never raises.

    The run goes through the deterministic org DELEGATION seam
    (:func:`lhassa.org.delegate.delegate_flow`) rather than calling ``run_flow``
    directly, so a flow flows down the command chain (Lhassa → owning department →
    flow) with an audit-trail episode at the hop — same return contract, $0 extra.
    """
    from lhassa.flows.store import FlowStore
    from lhassa.org.delegate import delegate_flow

    flow_id = (getattr(config, "cv_flow_id", "") or "flow-zvzpry").strip()
    flow = FlowStore(config).get(flow_id)
    if flow is None:
        return None, flow_id
    record = await delegate_flow(
        flow,
        inputs={"summary": caption},
        config=config,
        company=company,
        base_cv=base_cv,
    )
    return record, flow_id


def _tex_output_path(record: dict) -> str | None:
    """The exported ``.tex`` path if the run reached its tex output, else ``None``."""
    for step in record.get("steps", []) or []:
        if (
            step.get("kind") == "output"
            and step.get("output") == "tex"
            and step.get("path")
            and not step.get("error")
        ):
            path = step["path"]
            if Path(path).is_file():
                return path
    return None


def _gate_failure_reply(record: dict) -> str:
    """A short, honest explanation for why no tailored file was produced.

    Prefers the specific failed gate (the contact-preservation árbitro is the common
    one). We never send a broken file — we say what stopped us.
    """
    for step in record.get("steps", []) or []:
        if step.get("kind") == "gate" and not step.get("passed"):
            if step.get("needs_approval"):
                return "That CV needs your approval before I can send it."
            detail = str(step.get("detail") or "")
            if "contact" in detail.lower():
                return (
                    "I couldn't tailor it safely: contact details would have changed. "
                    "I didn't send a file."
                )
            return (
                "I couldn't tailor it safely: a verification check didn't pass. "
                "I didn't send a file."
            )
    return "I couldn't produce a tailored CV this time. Nothing was sent."


async def _reply_with_cv(record, flow_id: str, company: str | None, *, config) -> None:
    """Turn a CV run record into the owner's reply. Never raises.

    On success (the run reached its tex output) send the ``.tex`` via ``sendDocument``;
    on a gate failure send a short text explaining what stopped it; a missing flow id
    or a broken record degrades to a clear message. Nothing broken is ever sent.
    """
    from lhassa.notify import telegram_api

    if record is None:
        await telegram.send(
            f"My CV work-flow ({flow_id}) isn't set up yet — create it first.",
            config=config,
        )
        return

    path = _tex_output_path(record)
    if path:
        cap = f"Tailored CV for {company}." if company else "Your tailored CV."
        # Prefer a compiled PDF (tectonic); fall back to the raw .tex when the engine
        # is missing or the compile fails, so the owner always gets a file.
        from lhassa import cv

        send_path = path
        with contextlib.suppress(Exception):
            pdf = cv.compile_pdf(path)
            if pdf is not None:
                send_path = str(pdf)
        ok = await telegram_api.send_document(send_path, caption=cap, config=config)
        if not ok:
            await telegram.send(
                "I tailored your CV but couldn't send the file — try again.",
                config=config,
            )
        return

    await telegram.send(_gate_failure_reply(record), config=config)


async def _answer_document(document: dict, caption, *, config, chat_id: str = "") -> None:
    """Handle an uploaded CV document: extract → run the flow → reply. Never raises.

    The ``caption`` (falling back to any message text) is the JOB POSTING / target
    role. With no caption we HOLD the extracted CV for this chat and ask for the role
    in a follow-up message (:func:`_answer` picks it up) — many clients send a file
    without letting you add a caption.
    """
    try:
        cap = (str(caption).strip() if caption else "")

        from lhassa.notify import telegram_api

        data = await telegram_api.get_file(document.get("file_id"), config=config)
        if not data:
            await telegram.send(_CV_NO_DOWNLOAD, config=config)
            return

        from lhassa import cv

        base_cv = cv.extract_text(data, filename=document.get("file_name") or "")
        if not base_cv.strip():
            await telegram.send(_CV_NO_TEXT, config=config)
            return

        # ARMED via /cvnewbase → this upload REPLACES the on-disk base CV (any caption is
        # ignored in this mode). save_base backs up the previous base first.
        if _consume_armed(chat_id):
            _PENDING_CV.pop(chat_id, None)  # a base replacement supersedes any held CV
            cv.save_base(base_cv)
            await telegram.send(_CV_BASE_SAVED.format(chars=len(base_cv)), config=config)
            return

        if not cap:
            # Not armed, no caption → HOLD the CV; the next plain-text message is the
            # job posting and we tailor a one-off from it.
            _pending_set(chat_id, base_cv, document.get("file_name") or "cv")
            await telegram.send(_CV_NO_CAPTION.format(chars=len(base_cv)), config=config)
            return

        _PENDING_CV.pop(chat_id, None)  # a captioned upload supersedes any held CV
        company = _parse_company(cap)
        record, flow_id = await _run_cv_flow(
            caption=cap, company=company, base_cv=base_cv, config=config
        )
        await _reply_with_cv(record, flow_id, company, config=config)
    except Exception as exc:  # never raise into the poll loop
        logger.warning(f"Telegram inbound: CV document handling failed: {exc}")
        with contextlib.suppress(Exception):
            await telegram.send(_CV_ERROR, config=config)


async def _answer_cv_command(text: str, *, config) -> None:
    """The optional ``/cv <job posting>`` text command — tailor the on-disk base CV.

    No attachment: ``base_cv`` stays ``None`` so :func:`run_flow` loads the base CV
    from disk. Never raises.
    """
    try:
        posting = text.strip()[len("/cv"):].strip()
        if not posting:
            await telegram.send(
                "Usage: /cv <job posting or target role> — I'll tailor your base CV.",
                config=config,
            )
            return
        company = _parse_company(posting)
        # A tailored run takes a while (a real model turn) — ack immediately so the
        # owner knows it's working before the file lands.
        with contextlib.suppress(Exception):
            await telegram.send(
                f"Tailoring your CV to {company or 'the role'}… this takes ~2 min.",
                config=config,
            )
        record, flow_id = await _run_cv_flow(
            caption=posting, company=company, base_cv=None, config=config
        )
        await _reply_with_cv(record, flow_id, company, config=config)
    except Exception as exc:  # never raise into the poll loop
        logger.warning(f"Telegram inbound: /cv command failed: {exc}")
        with contextlib.suppress(Exception):
            await telegram.send(_CV_ERROR, config=config)


async def poll_once(config=None, *, offset=None):
    """Run one ``getUpdates`` cycle. Returns the next offset to use.

    Gated + defensive: when not enabled/configured it returns ``offset`` unchanged
    and makes **no network call**. Accepts only messages from the configured chat
    id, answers each through the brain, and advances the offset past every update
    seen (so even ignored updates are not re-fetched). Never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    if not _enabled(config):
        return offset

    token = (getattr(config, "telegram_bot_token", "") or "").strip()
    allowed_chat = str(getattr(config, "telegram_chat_id", "") or "").strip()
    url = _GET_UPDATES_URL.format(token=token)
    params: dict = {"timeout": _LONG_POLL_SECONDS}
    if offset is not None:
        params["offset"] = offset

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, params=params)
    except Exception as exc:  # transport error — keep the offset, let caller retry
        logger.warning(f"Telegram inbound: getUpdates failed (transport): {exc}")
        return offset

    if response.status_code != 200:
        logger.warning(
            f"Telegram inbound: getUpdates HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
        return offset

    try:
        data = response.json()
    except Exception:
        logger.warning("Telegram inbound: could not decode getUpdates JSON")
        return offset

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(f"Telegram inbound: getUpdates rejected: {str(data)[:200]}")
        return offset

    updates = data.get("result") or []
    new_offset = offset
    for update in updates:
        # Advance past every update we see, even malformed/ignored ones, so the
        # next cycle does not re-fetch them.
        try:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                new_offset = update_id + 1

            message = update.get("message") or update.get("edited_message")
            if not isinstance(message, dict):
                continue

            chat_id = str((message.get("chat") or {}).get("id", "")).strip()
            if chat_id != allowed_chat:
                # Security: answer nobody but the owner.
                logger.info(f"Telegram inbound: ignoring message from chat {chat_id!r}")
                continue

            # A CV document (with the caption as the job posting) routes to the CV
            # work-flow bridge; a plain text message keeps the existing brain path.
            document = message.get("document")
            if isinstance(document, dict) and document.get("file_id"):
                caption = message.get("caption") or message.get("text")
                await _answer_document(document, caption, config=config, chat_id=chat_id)
                continue

            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                continue

            await _answer(text.strip(), config=config, chat_id=chat_id)
        except Exception as exc:  # one bad update must not kill the loop
            logger.warning(f"Telegram inbound: skipping bad update: {exc}")
            continue

    return new_offset


async def run_forever(config=None) -> None:
    """Poll forever, calling :func:`poll_once` with exponential backoff on error.

    When polling is disabled or unconfigured this logs once and returns
    immediately — no network, no loop.
    """
    from lhassa import actionpack
    actionpack.register_all()

    if config is None:
        from lhassa.config import Config

        config = Config()

    if not _enabled(config):
        logger.info(
            "Telegram inbound poller not started "
            "(telegram_poll_enabled off or token/chat id missing)."
        )
        return

    logger.info("Telegram inbound poller started.")

    # Best-effort: publish the bot's command menu once on start. Guarded so a slow or
    # failing setMyCommands can never block or crash the poller's startup path.
    with contextlib.suppress(Exception):
        from lhassa.notify import telegram_api

        await telegram_api.set_my_commands(
            [
                ("cv", "Tailor your CV to a job posting"),
                ("cvnewbase", "Replace the base CV with your next upload"),
                ("cvbase", "Show the base CV on file"),
                ("status", "Brain status"),
                ("help", "Help"),
            ],
            config=config,
        )

    offset = None
    backoff = _BACKOFF_START
    while True:
        try:
            new_offset = await poll_once(config=config, offset=offset)
            if new_offset != offset:
                offset = new_offset
            backoff = _BACKOFF_START  # a clean cycle resets the backoff
        except Exception as exc:  # poll_once is defensive, but belt-and-braces
            logger.warning(f"Telegram inbound: poll cycle error: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


if __name__ == "__main__":
    asyncio.run(run_forever())
