"""
Mute strategy that silences user audio while Potts is speaking,
EXCEPT when a wake phrase is detected — in that case the frame is allowed through
so WakePhraseUserTurnStartStrategy can trigger an interruption.
"""

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
)
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy

# Canonical set of wake phrase variants (covers common mishearings of "Potts")
WAKE_VARIANTS: set[str] = {
    "potts", "pots", "pods", "pot",
    "hey potts", "hey pots", "hey pods",
    "oye potts", "oye pots", "oye pods",
    "pos", "pues", "box", "bots", "bot",
    "voz", "vos", "boz", "pox",
    "a ver potts", "a ver pots",
}


class WakeWordOnlyMuteStrategy(BaseUserMuteStrategy):
    """
    Mutes user input while Potts is speaking.
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
