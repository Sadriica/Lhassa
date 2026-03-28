import asyncio
import pyaudio
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from potts.config import Config
from potts.llm.prompt import SYSTEM_PROMPT


class ChatPrinter(FrameProcessor):
    """Intercepts transcription and LLM text frames and prints them as a chat."""

    def __init__(self):
        super().__init__()
        self._potts_buffer = []
        self._potts_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            print(f"\n\033[1;36mYou:\033[0m {frame.text.strip()}")

        elif isinstance(frame, LLMFullResponseStartFrame):
            self._potts_buffer = []
            self._potts_speaking = True

        elif isinstance(frame, LLMTextFrame) and self._potts_speaking:
            self._potts_buffer.append(frame.text)

        elif isinstance(frame, LLMFullResponseEndFrame) and self._potts_speaking:
            full = "".join(self._potts_buffer).strip()
            if full:
                print(f"\033[1;35mPotts:\033[0m {full}\n")
            self._potts_buffer = []
            self._potts_speaking = False

        await self.push_frame(frame, direction)


def find_device(name: str) -> int:
    """Return the first PyAudio device index whose name contains `name`."""
    p = pyaudio.PyAudio()
    try:
        for i in range(p.get_device_count()):
            if name.lower() in p.get_device_info_by_index(i)["name"].lower():
                return i
    finally:
        p.terminate()
    raise RuntimeError(f"Audio device '{name}' not found")


async def run():
    config = Config()

    try:
        pw = find_device("pipewire")
        logger.info(f"Using audio device [{pw}] pipewire")
    except RuntimeError:
        pw = None
        logger.warning("PipeWire not found — using system default audio device")

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            input_device_index=pw,
            output_device_index=pw,
        )
    )

    stt = DeepgramSTTService(api_key=config.deepgram_api_key)

    llm = GroqLLMService(
        api_key=config.groq_api_key,
        settings=GroqLLMService.Settings(model=config.model),
    )

    tts = CartesiaTTSService(
        api_key=config.cartesia_api_key,
        settings=CartesiaTTSService.Settings(voice=config.voice_id),
    )

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            ChatPrinter(),
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),
    )

    logger.info("Potts is online. Listening...\n")

    runner = PipelineRunner()
    await runner.run(task)
