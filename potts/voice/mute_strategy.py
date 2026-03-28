"""
Estrategia de mute que silencia al usuario mientras Potts habla,
EXCEPTO cuando detecta la wake word — en ese caso deja pasar el frame
para que WakePhraseUserTurnStartStrategy dispare la interrupción.
"""

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
)
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy

WAKE_VARIANTS = {
    "potts", "pots", "pods", "pot",
    "hey potts", "hey pots", "hey pods",
    "oye potts", "oye pots", "oye pods",
    "pos", "pues", "box", "bots", "bot",
    "voz", "vos", "boz", "pox",
    "a ver potts", "a ver pots",
}


class WakeWordOnlyMuteStrategy(BaseUserMuteStrategy):
    """
    Mute mientras Potts habla.
    Excepción: si la transcripción contiene la wake word, pasa — esto
    permite que WakePhraseUserTurnStartStrategy dispare la interrupción.
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

        # Mientras Potts habla, deja pasar solo si contiene wake word
        if self._bot_speaking and isinstance(frame, TranscriptionFrame):
            text = frame.text.lower().strip().rstrip(".,!?")
            if any(w in text for w in WAKE_VARIANTS):
                return False  # no muted → interrupción permitida

        return self._bot_speaking
