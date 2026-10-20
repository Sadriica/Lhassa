# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Mute strategy that silences user audio while Lhassa is speaking,
EXCEPT when a wake phrase is detected; in that case the frame is allowed through
so WakePhraseUserTurnStartStrategy can trigger an interruption.
"""

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
)
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy

# Core wake variants; mishearings of "Lhassa" (LAH-sah) in any language
WAKE_PHRASES_CORE: set[str] = {
    "lhassa", "lhasa", "lasa", "lassa", "lasha", "laza", "lasso", "la sa",
    "hey lhassa", "hey lhasa", "hey lasa",
}

# Spanish-specific variants; only used when LHASSA_STT_LANGUAGE starts with "es"
WAKE_PHRASES_ES: set[str] = {
    "oye lhassa", "oye lhasa", "oye lasa",
    "a ver lhassa", "a ver lasa",
    "laza", "lasha", "lhaza",
}

# Full set used by the mute strategy (safety net; keeps all variants for interruption)
WAKE_VARIANTS: set[str] = WAKE_PHRASES_CORE | WAKE_PHRASES_ES


class WakeWordOnlyMuteStrategy(BaseUserMuteStrategy):
    """
    Mutes user input while Lhassa is speaking.
    Exception: if the transcription contains a wake phrase, it passes through
    so WakePhraseUserTurnStartStrategy can fire an interruption.
    """

    def __init__(self):
        super().__init__()
        self._bot_speaking = False

    async def reset(self):
        self._bot_speaking = False

    async def process_frame(self, frame: Frame) -> bool:
        await super().process_frame(frame)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

        # While bot is speaking, allow wake phrases through (return False = not muted)
        if self._bot_speaking and isinstance(frame, TranscriptionFrame):
            text = frame.text.lower().strip().rstrip(".,!?")
            if any(w in text for w in WAKE_VARIANTS):
                return False

        return self._bot_speaking
