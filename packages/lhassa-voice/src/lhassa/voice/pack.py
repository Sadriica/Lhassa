# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for the voice surface (``lhassa.plugins`` -> ``voice``).

Importing this module registers the pack and plugs the TTS speaker into the
core notifier seam (:func:`lhassa.tools.notifier.register_speaker`). It never
imports pipecat: the pipeline itself (``lhassa.voice.pipeline``) is reached
only from ``main.py``, and needs the ``runtime`` extra
(``uv sync --package lhassa-voice --extra runtime``).
"""

from __future__ import annotations

from lhassa.extensions import register_pack
from lhassa.tools.notifier import register_speaker
from lhassa.voice.speaker import speak

register_pack(
    "voice",
    "Voice surface: wake word, STT, brain turn, TTS (pipeline in the runtime extra)",
    module=__name__,
)

register_speaker(speak)
