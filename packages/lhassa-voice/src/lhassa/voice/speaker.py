# SPDX-License-Identifier: AGPL-3.0-or-later
"""The voice pack's speaker for :mod:`lhassa.tools.notifier`.

Registered by ``pack.py``. It speaks a notification as TTS only while the
voice pipeline is running and awake (``state.is_voice_active`` plus a live
``state.pipeline_task``); otherwise it returns False and the notifier falls
back to the desktop. pipecat is imported lazily, so this module loads on an
install without the ``runtime`` extra.
"""

from __future__ import annotations

from loguru import logger

from lhassa.state import state


async def speak(message: str) -> bool:
    """Queue ``message`` as a TTS frame; True only when it was queued."""
    if not (state.is_voice_active and state.pipeline_task is not None):
        return False
    try:
        from pipecat.frames.frames import TTSSpeakFrame
    except ImportError:
        logger.warning("voice runtime not installed; falling back to desktop notify")
        return False
    await state.pipeline_task.queue_frame(TTSSpeakFrame(text=message))
    return True
