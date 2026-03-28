import asyncio
import pyaudio
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
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
from pipecat.turns.user_start.wake_phrase_user_turn_start_strategy import (
    WakePhraseUserTurnStartStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies, default_user_turn_start_strategies

from potts.config import Config
from potts.llm.prompt import build_system_prompt
from potts.memory.store import build_memory_context
from potts.state import state
from potts.tools.registry import TOOLS, register_tools
from potts.voice.beep import play_beep
from potts.voice.mute_strategy import WAKE_VARIANTS, WakeWordOnlyMuteStrategy


class WakeDebug(FrameProcessor):
    """Prints live STT transcriptions to the terminal — useful during development."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            tag = "F" if isinstance(frame, TranscriptionFrame) else "i"
            print(f"\033[2m[{tag}] {frame.text!r}\033[0m", flush=True)
        await self.push_frame(frame, direction)


class ChatPrinter(FrameProcessor):
    """Streams LLM tokens to the terminal in real time."""

    def __init__(self):
        super().__init__()
        self._potts_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            print(f"\n\033[1;36mYou:\033[0m {frame.text.strip()}")
            print(f"\033[1;35mPotts:\033[0m ", end="", flush=True)

        elif isinstance(frame, LLMFullResponseStartFrame):
            self._potts_speaking = True

        elif isinstance(frame, LLMTextFrame) and self._potts_speaking:
            print(frame.text, end="", flush=True)

        elif isinstance(frame, LLMFullResponseEndFrame) and self._potts_speaking:
            print("\n", flush=True)
            self._potts_speaking = False

        await self.push_frame(frame, direction)


def find_device(name: str) -> int:
    """Return the index of the first audio device whose name contains `name`."""
    p = pyaudio.PyAudio()
    try:
        for i in range(p.get_device_count()):
            if name.lower() in p.get_device_info_by_index(i)["name"].lower():
                return i
    finally:
        p.terminate()
    raise RuntimeError(f"Audio device '{name}' not found")


async def run():
    """Build and run the full Pipecat voice pipeline. Returns (context, messages)."""
    config = Config()

    memory_context = await build_memory_context()
    system_prompt = build_system_prompt(memory_context)
    if memory_context:
        logger.info("Memory loaded.")
    else:
        logger.info("No prior memory — first session.")

    try:
        pw = find_device("pipewire")
        logger.info(f"Using audio device [{pw}] PipeWire")
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

    stt = DeepgramSTTService(
        api_key=config.deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model="nova-3-general",
            language="es",
            keyterm=["Potts"],
        ),
    )

    llm = GroqLLMService(
        api_key=config.groq_api_key,
        settings=GroqLLMService.Settings(model=config.model),
    )

    tts = CartesiaTTSService(
        api_key=config.cartesia_api_key,
        settings=CartesiaTTSService.Settings(voice=config.voice_id),
    )

    register_tools(llm)

    context = LLMContext(
        messages=[{"role": "system", "content": system_prompt}],
        tools=TOOLS,
    )

    # Wake phrases cover common mishearings of "Potts" in Spanish accent
    wake_phrases = [
        "potts", "pots", "pods", "pot",
        "hey potts", "hey pots", "hey pods",
        "oye potts", "oye pots", "oye pods",
        "pos", "pues", "box", "bots", "bot",
        "voz", "vos", "boz", "pox",
        "a ver potts", "a ver pots",
    ]

    wake_strategy = WakePhraseUserTurnStartStrategy(
        phrases=wake_phrases,
        timeout=300.0,  # 5 minutes before auto-sleep
    )

    @wake_strategy.event_handler("on_wake_phrase_detected")
    async def on_wake(strategy, phrase):
        print(f"\033[1;33m[Potts active]\033[0m", flush=True)
        asyncio.create_task(play_beep(freq=880, duration_ms=120))

    @wake_strategy.event_handler("on_wake_phrase_timeout")
    async def on_sleep(strategy):
        asyncio.create_task(play_beep(freq=440, duration_ms=80))
        print(f"\033[2m[Potts sleeping — say 'Potts' to wake]\033[0m", flush=True)

    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_mute_strategies=[WakeWordOnlyMuteStrategy()],
            user_turn_strategies=UserTurnStrategies(
                start=[wake_strategy, *default_user_turn_start_strategies()]
            ),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            *([WakeDebug()] if config.debug else []),
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

    # Expose refs so notifier can inject TTS frames and main can recover messages on shutdown
    state.pipeline_task = task
    state.llm_context = context

    logger.info("Potts ready. Say 'Potts' to activate.\n")
    print("\033[2m[Potts sleeping — say 'Potts' to wake]\033[0m\n")

    runner = PipelineRunner()

    try:
        await runner.run(task)
    except asyncio.CancelledError:
        pass  # Clean shutdown from main
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
    finally:
        state.pipeline_task = None
        # Keep llm_context alive so _run_all can read messages after cancellation

    return context, context.get_messages()
