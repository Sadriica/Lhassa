# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telegram to CV work-flow bridge; the cv pack's inbound hook.

Registered by :mod:`lhassa.cv.pack` through
:func:`lhassa.notify.inbound_hooks.register_inbound_hook`, so the Telegram poller
(:mod:`lhassa.notify.telegram_inbound`) never imports the cv pack:

  * ``/cv <job posting>`` tailors the on-disk base CV and replies with the file.
  * An uploaded document is a CV: with a caption (the job posting) it is tailored at
    once; without one it is HELD until the next plain-text message; in a chat armed by
    ``/cvnewbase`` it REPLACES the base CV instead.
  * The tailored file travels UP the org hierarchy (:func:`lhassa.org.deliver.deliver`),
    never a surface-direct send.

Every entry point here never raises into the poll loop.
"""

from __future__ import annotations

import contextlib
import re
import time
from pathlib import Path

from loguru import logger

from lhassa.notify import commands, telegram

# ── replies and per-chat state ───────────────────────────────────────────────
# The base CV lives on disk (``cv_largo.tex``) and rarely changes, so a document
# upload is unambiguous: replacing the base is an EXPLICIT act (arm with /cvnewbase),
# and any other upload is a one-off tailor. No inline buttons; an armed flag decides.
#
# Sent when a (non-armed, caption-less) CV document arrives: we hold the CV and ask
# for the job posting in a follow-up message (many clients send a file uncaptioned).
_CV_NO_CAPTION = (
    "Got your CV ({chars} chars), now send the job posting as a normal message and "
    "I'll tailor it. Optionally name the company with '@ Company' or 'company: X'."
)
# Confirmation after an armed /cvnewbase upload replaced the base CV.
_CV_BASE_SAVED = (
    "Base CV updated ({chars} chars; previous backed up). /cv will use it now."
)
# The document could not be downloaded / had no readable text.
_CV_NO_DOWNLOAD = "I couldn't download that document, try sending it again."
_CV_NO_TEXT = "I couldn't read any text from that document. Send a .tex or a text PDF."
# Something unexpected happened while tailoring.
_CV_ERROR = "Something went wrong tailoring your CV. Nothing was sent."

# A CV held from a caption-less upload, awaiting the job posting in a follow-up
# message. Keyed by chat id; in-memory (the poller is one long-running process).
_PENDING_CV: dict = {}
_PENDING_TTL = 1800.0  # 30 min; an older held CV is treated as stale and dropped.

# Chats ARMED via /cvnewbase for a base-CV replacement (chat id → arm timestamp).
# The next uploaded document in an armed chat replaces the on-disk base CV.
_AWAIT_BASE: dict = {}
_AWAIT_BASE_TTL = 1800.0  # 30 min; an old arm request is treated as stale and ignored.


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
    when it was set within the TTL; a stale arm request is dropped and ignored.
    """
    ts = _AWAIT_BASE.pop(chat_id, None)
    if ts is None:
        return False
    return (time.time() - ts) <= _AWAIT_BASE_TTL
# ``@ Company`` / ``company: X`` extractors for the optional company name in a caption.
_COMPANY_RES = (
    # A company NAME ends at a sentence/segment boundary; an unbounded tail
    # swallowed the whole posting when /cv carried a full multi-sentence text.
    re.compile(r"company\s*[:=]\s*(?P<name>[^\n.;|(]+)", re.IGNORECASE),
    re.compile(r"(?:^|\s)@\s*(?P<name>[^\n.;|(]+)"),
)



# ── the bridge ───────────────────────────────────────────────────────────────
def _parse_company(caption: str) -> str | None:
    """Pull an optional company name out of a caption.

    Recognises ``company: X`` / ``company=X`` and ``@ Company`` (the ``company:`` form
    wins). Returns the trimmed name, or ``None`` when neither pattern is present; the
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

    ``base_cv`` is passed straight through to :func:`run_flow`; a string (the
    uploaded CV's text) overrides the on-disk base CV, ``None`` keeps the disk base.
    ``record`` is ``None`` when the configured flow id is not stored (so the caller can
    say "not set up yet"). Never raises.

    The run goes through the deterministic org DELEGATION seam
    (:func:`lhassa.org.delegate.delegate_flow`) rather than calling ``run_flow``
    directly, so a flow flows down the command chain (Lhassa → owning department →
    flow) with an audit-trail episode at the hop; same return contract, $0 extra.
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
    one). We never send a broken file; we say what stopped us.
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
    # No gate failed; the flow ran but produced no file. When a LIVE brain in
    # the chain ASKED for something (observed 2026-07-30: "/cv <title>" → the
    # analyst asked for the full posting and its question cascaded), the owner
    # must SEE that ask; hiding it behind a generic line wastes the whole run.
    last_words = ""
    for step in reversed(record.get("steps", []) or []):
        if step.get("kind") == "task":
            out = step.get("output")
            if isinstance(out, dict):
                out = out.get("summary") or out.get("output") or ""
            last_words = str(out or "").strip()
            if last_words:
                break
    if last_words:
        return (
            "I couldn't produce a tailored CV, here is what the team said:\n\n"
            f"{last_words[:600]}"
        )
    return "I couldn't produce a tailored CV this time. Nothing was sent."


async def _reply_with_cv(
    record, flow_id: str, company: str | None, *, config, posting: str | None = None
) -> None:
    """Turn a CV run record into the owner's reply. Never raises.

    On success (the run reached its tex output) the tailored CV travels UP the org
    hierarchy via :func:`lhassa.org.deliver.deliver`; THE flagship of the
    sender-of-record seam (§org: "the hierarchy is the philosophy, not a feature"):
    the department that produced it (read off ``record["delegation"]``, the same
    chain :func:`lhassa.org.delegate.delegate_flow` resolved to run the flow) logs its
    hop, every intermediate coordinator logs its own, and Lhassa (root) sends. On a
    gate failure send a short text explaining what stopped it; a missing flow id or a
    broken record degrades to a clear message. Nothing broken is ever sent.
    """
    if record is None:
        await telegram.send(
            f"My CV work-flow ({flow_id}) isn't set up yet, create it first.",
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
        paths = [path]
        with contextlib.suppress(Exception):
            pdf = cv.compile_pdf(path)
            if pdf is not None:
                send_path = str(pdf)
                paths.append(str(pdf))

        from lhassa.org.deliver import deliver

        metadata: dict = {}
        if company:
            metadata["company"] = company
        if posting:
            metadata["posting"] = posting
        metadata["run_id"] = record.get("id")
        origin_department = (record.get("delegation") or {}).get("department") or "interns"

        delivered, _artifact_id = await deliver(
            document=send_path,
            paths=paths,
            caption=cap,
            kind="cv",
            metadata=metadata,
            origin_department=origin_department,
            config=config,
        )
        if not delivered:
            await telegram.send(
                "I tailored your CV but couldn't send the file, try again.",
                config=config,
            )
        return

    await telegram.send(_gate_failure_reply(record), config=config)


async def _answer_document(document: dict, caption, *, config, chat_id: str = "") -> None:
    """Handle an uploaded CV document: extract → run the flow → reply. Never raises.

    The ``caption`` (falling back to any message text) is the JOB POSTING / target
    role. With no caption we HOLD the extracted CV for this chat and ask for the role
    in a follow-up message (:func:`on_text` picks it up); many clients send a file
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
        await _reply_with_cv(record, flow_id, company, config=config, posting=cap)
    except Exception as exc:  # never raise into the poll loop
        logger.warning(f"Telegram inbound: CV document handling failed: {exc}")
        with contextlib.suppress(Exception):
            await telegram.send(_CV_ERROR, config=config)


async def _answer_cv_command(text: str, *, config) -> None:
    """The optional ``/cv <job posting>`` text command; tailor the on-disk base CV.

    No attachment: ``base_cv`` stays ``None`` so :func:`run_flow` loads the base CV
    from disk. Never raises.
    """
    try:
        posting = text.strip()[len("/cv"):].strip()
        if not posting:
            await telegram.send(
                "Usage: /cv <job posting or target role>, I'll tailor your base CV.",
                config=config,
            )
            return
        # Preflight ($0): a bare title can't feed a six-brain tailoring run; the
        # first analyst will just ask for the posting and (observed live,
        # 2026-07-30) its QUESTION then cascades downstream as if it were the
        # brief, wasting every turn after it. Ask here, before any brain wakes.
        if len(posting) < 200:
            await telegram.send(
                "That looks like just a title. Paste the FULL job posting after "
                "/cv (requirements included) and I'll tailor the CV to it, the "
                "more of the posting I see, the sharper the result.",
                config=config,
            )
            return
        company = _parse_company(posting)
        # A tailored run takes a while (a real model turn), ack immediately so the
        # owner knows it's working before the file lands.
        with contextlib.suppress(Exception):
            await telegram.send(
                f"Tailoring your CV to {company or 'the role'}… this takes ~2 min.",
                config=config,
            )
        record, flow_id = await _run_cv_flow(
            caption=posting, company=company, base_cv=None, config=config
        )
        await _reply_with_cv(record, flow_id, company, config=config, posting=posting)
    except Exception as exc:  # never raise into the poll loop
        logger.warning(f"Telegram inbound: /cv command failed: {exc}")
        with contextlib.suppress(Exception):
            await telegram.send(_CV_ERROR, config=config)


# ── the inbound hook ──────────────────────────────────────────────────────────
def _is_cv_command(stripped: str) -> bool:
    return stripped == "/cv" or stripped.startswith("/cv ") or stripped.startswith("/cv\n")


async def on_text(text: str, *, config, chat_id: str = "") -> bool:
    """The text hook. Returns ``True`` when the message was the cv pack's to answer.

    ``/cv`` replies with a file, so it is handled here rather than through the
    string-returning command registry. A plain message in a chat holding a CV from a
    caption-less upload IS the job posting. ``/cvnewbase`` ARMS the chat (the arm
    side-effect needs the chat id) and returns ``False`` so the registry command
    still sends its instruction reply.
    """
    stripped = text.strip()
    if _is_cv_command(stripped):
        await _answer_cv_command(text, config=config)
        return True

    if not stripped.startswith("/"):
        held = _pending_pop(chat_id)
        if held:
            company = _parse_company(stripped)
            record, flow_id = await _run_cv_flow(
                caption=stripped, company=company, base_cv=held["base_cv"], config=config
            )
            await _reply_with_cv(record, flow_id, company, config=config, posting=stripped)
            return True
        return False

    command_name, _ = commands.parse(stripped)
    if command_name == "cvnewbase":
        _arm_base(chat_id)
    return False


async def on_document(document: dict, caption, *, config, chat_id: str = "") -> bool:
    """The document hook: every uploaded document is treated as a CV."""
    await _answer_document(document, caption, config=config, chat_id=chat_id)
    return True


def reset() -> None:
    """Forget every held CV and armed base-CV request."""
    _PENDING_CV.clear()
    _AWAIT_BASE.clear()
