# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Voice ↔ brain bridge (§3-Fase 6 — "voz reutilizando el esqueleto existente").

The existing pipecat pipeline (``voice/pipeline.py``) answers a spoken turn with a
generic LLM context. This module routes that turn through THE BRAIN's think path
instead, so a voice answer carries the brain's memory, identity, and episode log —
exactly like ``core/step``'s judgment branch and ``cli/agent``'s one-turn loop,
but for a spoken turn.

Two pieces:

  * :func:`answer` — ONE spoken turn → brain reply. It loads the persona identity
    and the layered memory context (``memory.load.load(query=text)``), lets the
    router pick a provider+model (the routine/heartbeat path, so the local/opencode
    provider is preferred), calls ``provider.complete(...)``, records an ``Episode``
    (``despertar_por="voz"``), and returns the spoken text. It NEVER raises into the
    audio pipeline: on a missing provider / spent budget (or any error) it returns a
    short spoken fallback and still logs the episode.

  * :func:`make_brain_processor` — a thin pipecat ``FrameProcessor`` adapter that
    drops into the pipeline in place of the LLM service when ``config.voice_enabled``
    is true, calling :func:`answer` for the assistant turn. pipecat is imported
    LAZILY inside the factory so importing this module for tests needs no audio
    hardware or pipecat runtime.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import db, watchlist
from lhassa.core.episode import Episode
from lhassa.llm import router
from lhassa.llm.gateway import BudgetExceeded
from lhassa.llm.provider import ProviderNotConfigured
from lhassa.memory import load as memory_load
from lhassa.persona import load_identity

if TYPE_CHECKING:
    from lhassa.memory.load import Context

# A short, spoken fallback when the brain can't reach a model. No markdown — this
# is read aloud by TTS.
FALLBACK_REPLY = "I can't reach my model right now — give it a moment and ask me again."

# A wake-for-a-spoken-turn is a short answer, not a long job.
_VOICE_MAX_TOKENS = 512

# Marker for an episode whose model could not run (mirrors core/step).
_DEFERRED_DECISION = "deferred: voz needs judgment"

# Voice-appropriate instruction layered on the persona (spoken, so NO markdown).
VOICE_INSTRUCTION = (
    "You are answering out loud through a voice interface. Reply in short, "
    "natural spoken sentences. No markdown, no bullet points, no code blocks, "
    "no emoji — it is read aloud. Lead with the answer and keep it brief."
)


def _system_prompt(instance_id: Optional[str], ctx: "Context") -> str:
    """Persona identity + layered memory + the spoken-output instruction."""
    identity = load_identity(instance_id)
    parts: list[str] = [identity.persona_block]
    memory_block = ctx.to_prompt()
    if memory_block:
        parts.append(memory_block)
    parts.append(VOICE_INSTRUCTION)
    return "\n\n".join(parts)


async def answer(
    text: str, *, instance_id: Optional[str] = None, config: Optional[Config] = None
) -> str:
    """Answer one spoken turn through the brain. Returns the reply to speak.

    Loads identity + layered memory (with ``text`` as the semantic query), routes
    through the router's routine path (provider+model), thinks, and records a
    ``voz`` episode. Degrades to :data:`FALLBACK_REPLY` on a missing provider, a
    spent budget, or any unexpected error — it never raises into the pipeline.
    """
    config = config or Config()
    instance_id = instance_id or getattr(config, "instance_id", None) or Config.instance_id

    # Idempotent — production callers (heartbeat/main) usually init the DB, but a
    # voice turn must work even if it wakes the brain first.
    await db.init_db()

    ep = Episode.begin(despertar_por="voz", instance_id=instance_id)
    reply = FALLBACK_REPLY
    try:
        text = (text or "").strip()
        if not text:
            ep.finish(decision="voz: empty turn", accion="responder", resultado="")
            return ""

        # Layer the memory (semantic query = the spoken text; layer 4 only fires
        # when enabled + indexed, otherwise it's the unchanged pinned/recent/tags).
        ctx = await memory_load.load(
            instance_id,
            token_budget=getattr(config, "context_token_budget", 2000),
            query=text,
        )

        # Routine path: not delegated code work, not flagged hard → the router
        # prefers the local/opencode heartbeat provider (§3-Fase 6).
        trigger = watchlist.Trigger(kind="voice", action="responder", params={})
        decision = router.decide(trigger, ctx, config=config)
        provider = decision.provider
        if provider is None:
            raise ProviderNotConfigured("router resolved no provider for voice turn")

        system = _system_prompt(instance_id, ctx)
        messages = [{"role": "user", "content": text}]
        result = await provider.complete(
            messages,
            model=decision.model,
            max_tokens=_VOICE_MAX_TOKENS,
            system=system,
        )

        reply = (result.text or "").strip() or FALLBACK_REPLY
        ep.finish(
            decision=decision.reason,
            accion="responder",
            resultado=reply,
            costo_usd=result.cost_usd,
        )
        return reply

    except (ProviderNotConfigured, BudgetExceeded) as exc:
        # Known degradation — defer, speak the fallback, keep the episode.
        ep.finish(
            decision=_DEFERRED_DECISION,
            accion="responder",
            resultado=f"deferred ({type(exc).__name__}): {exc}",
        )
        return FALLBACK_REPLY
    except Exception as exc:  # never raise into the audio pipeline
        logger.error(f"brain_bridge.answer failed: {exc}")
        ep.finish(
            decision="voz: error",
            accion="responder",
            resultado=f"error: {type(exc).__name__}: {exc}",
        )
        return FALLBACK_REPLY
    finally:
        # Always log the episode; a logging failure must not break the turn.
        with contextlib.suppress(Exception):
            await ep.append()


# ── pipecat adapter (lazy: imported only when the voice pipeline builds it) ───────


def _extract_user_text(frame: Any) -> str:
    """Pull the latest user-turn text out of a pipecat frame.

    Handles both shapes the pipeline can hand us, by duck typing so this module
    never has to import pipecat:
      * a context-aggregation frame (``frame.context.get_messages()``) — what
        ``LLMContextAggregatorPair.user()`` pushes at the end of a turn;
      * a raw ``TranscriptionFrame`` (``frame.text``) as a fallback.
    Returns "" for any other frame (which the caller passes through untouched).
    """
    ctx = getattr(frame, "context", None)
    if ctx is not None and hasattr(ctx, "get_messages"):
        try:
            messages = ctx.get_messages()
        except Exception:
            messages = []
        for msg in reversed(list(messages)):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role == "user":
                content = (
                    msg.get("content") if isinstance(msg, dict)
                    else getattr(msg, "content", "")
                )
                return _content_to_text(content)
        return ""

    # Fallback: a final transcription frame carries the spoken text directly.
    if type(frame).__name__ == "TranscriptionFrame":
        return (getattr(frame, "text", "") or "").strip()
    return ""


def _content_to_text(content: Any) -> str:
    """Flatten an LLM message ``content`` (str or list of parts) to plain text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                out.append(part["text"])
            elif isinstance(part, str):
                out.append(part)
        return " ".join(out).strip()
    return ""


def make_brain_processor(config: Optional[Config] = None):
    """Build a pipecat ``FrameProcessor`` that answers turns through the brain.

    Drop-in replacement for the pipeline's LLM service: it consumes the completed
    user turn, calls :func:`answer`, and emits the standard LLM response frames
    (start → text → end) so TTS, ``ChatPrinter``, and the assistant aggregator
    behave exactly as with a real LLM service. pipecat is imported here, lazily, so
    importing this module needs no audio runtime.
    """
    from pipecat.frames.frames import (
        Frame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class BrainBridgeProcessor(FrameProcessor):
        """Routes the user turn through the brain instead of a generic LLM."""

        def __init__(self, config: Optional[Config] = None):
            super().__init__()
            self._config = config

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)

            spoken = _extract_user_text(frame)
            if spoken:
                reply = await answer(spoken, config=self._config)
                # Mimic an LLM service's output so the rest of the pipeline is
                # unchanged: start → text → end (TTS speaks, ChatPrinter prints,
                # the assistant aggregator closes the turn).
                await self.push_frame(LLMFullResponseStartFrame(), direction)
                await self.push_frame(LLMTextFrame(reply), direction)
                await self.push_frame(LLMFullResponseEndFrame(), direction)
                return

            await self.push_frame(frame, direction)

    return BrainBridgeProcessor(config)
