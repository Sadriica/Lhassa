# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
from datetime import datetime
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
    TTSSpeakFrame,
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
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.turns.user_start.wake_phrase_user_turn_start_strategy import (
    WakePhraseUserTurnStartStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies, default_user_turn_start_strategies

from lhassa.config import Config
from lhassa.llm.prompt import build_system_prompt
from lhassa.llm.provider import get_pipeline_llm
from lhassa.memory.store import build_memory_context
from lhassa.state import state
from lhassa.tools.registry import TOOLS, register_tools
from lhassa.voice.beep import play_beep
from lhassa.voice.mute_strategy import WAKE_PHRASES_CORE, WAKE_PHRASES_ES, WakeWordOnlyMuteStrategy


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
        self._lhassa_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            print(f"\n\033[1;36mYou:\033[0m {frame.text.strip()}")
            print("\033[1;35mLhassa:\033[0m ", end="", flush=True)

        elif isinstance(frame, LLMFullResponseStartFrame):
            self._lhassa_speaking = True

        elif isinstance(frame, LLMTextFrame) and self._lhassa_speaking:
            print(frame.text, end="", flush=True)

        elif isinstance(frame, LLMFullResponseEndFrame) and self._lhassa_speaking:
            print("\n", flush=True)
            self._lhassa_speaking = False

        await self.push_frame(frame, direction)


_GREETINGS: dict[str, tuple[str, str, str]] = {
    # lang_code: (morning, afternoon, evening)
    "es": ("Buenos días. Di Lhassa cuando me necesites.",
           "Buenas tardes. Di Lhassa cuando me necesites.",
           "Buenas noches. Di Lhassa cuando me necesites."),
    "fr": ("Bonjour. Dites Lhassa quand vous avez besoin de moi.",
           "Bon après-midi. Dites Lhassa quand vous avez besoin de moi.",
           "Bonsoir. Dites Lhassa quand vous avez besoin de moi."),
    "de": ("Guten Morgen. Sag Lhassa, wenn du mich brauchst.",
           "Guten Tag. Sag Lhassa, wenn du mich brauchst.",
           "Guten Abend. Sag Lhassa, wenn du mich brauchst."),
    "pt": ("Bom dia. Diga Lhassa quando precisar de mim.",
           "Boa tarde. Diga Lhassa quando precisar de mim.",
           "Boa noite. Diga Lhassa quando precisar de mim."),
}
_GREETINGS_EN = (
    "Good morning. Say Lhassa when you need me.",
    "Good afternoon. Say Lhassa when you need me.",
    "Good evening. Say Lhassa when you need me.",
)


async def _play_greeting(task, lang: str = "en"):
    """Speak a time-of-day greeting a moment after the pipeline starts."""
    await asyncio.sleep(1.5)  # give the pipeline a moment to initialise
    hour = datetime.now().hour
    idx = 0 if hour < 12 else (1 if hour < 18 else 2)
    lang_code = lang.lower().split("-")[0]
    morning, afternoon, evening = _GREETINGS.get(lang_code, _GREETINGS_EN)
    greeting = (morning, afternoon, evening)[idx]
    try:
        await task.queue_frame(TTSSpeakFrame(text=greeting))
    except Exception:
        pass  # Pipeline not ready yet — skip greeting silently


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
    system_prompt = build_system_prompt(memory_context, stt_language=config.stt_language)
    if memory_context:
        logger.info("Memory loaded.")
    else:
        logger.info("No prior memory — first session.")

    pw = None
    if config.mic_name:
        try:
            pw = find_device(config.mic_name)
            logger.info(f"Using audio device [{pw}] '{config.mic_name}'")
        except RuntimeError:
            logger.warning(f"Audio device '{config.mic_name}' not found — using system default")

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
            language=config.stt_language,
            keyterm=["Lhassa"],
        ),
    )

    llm = get_pipeline_llm(config)

    tts = CartesiaTTSService(
        api_key=config.cartesia_api_key,
        settings=CartesiaTTSService.Settings(voice=config.voice_id),
    )

    register_tools(llm)

    # Phase 6 voice bridge: when enabled, the assistant turn is answered by the
    # brain (memory + identity + provider + episode) via a drop-in FrameProcessor
    # that replaces the LLM service. Default path (voice_enabled false) unchanged.
    turn_processor = llm
    if getattr(config, "voice_enabled", False):
        from lhassa.voice.brain_bridge import make_brain_processor

        turn_processor = make_brain_processor(config)
        logger.info("Voice turns routed through the brain (memory + identity + episode).")

    context = LLMContext(
        messages=[{"role": "system", "content": system_prompt}],
        tools=TOOLS,
    )

    # Wake phrases — language-aware; Spanish variants only added for es-* languages
    lang_extras = WAKE_PHRASES_ES if config.stt_language.lower().startswith("es") else set()
    wake_phrases = sorted(WAKE_PHRASES_CORE | lang_extras)

    wake_strategy = WakePhraseUserTurnStartStrategy(
        phrases=wake_phrases,
        timeout=config.wake_timeout,
    )

    @wake_strategy.event_handler("on_wake_phrase_detected")
    async def on_wake(strategy, phrase):
        await state.set_voice_active(True)
        print("\033[1;33m[Lhassa active]\033[0m", flush=True)
        asyncio.create_task(play_beep(freq=880, duration_ms=120))

    @wake_strategy.event_handler("on_wake_phrase_timeout")
    async def on_sleep(strategy):
        await state.set_voice_active(False)
        asyncio.create_task(play_beep(freq=440, duration_ms=80))
        print("\033[2m[Lhassa sleeping — say 'Lhassa' to wake]\033[0m", flush=True)

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
            turn_processor,
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
    await state.set_voice_active(False)

    logger.info("Lhassa ready. Say 'Lhassa' to activate.\n")
    print("\033[2m[Lhassa sleeping — say 'Lhassa' to wake]\033[0m\n")

    # Startup greeting — injected after pipeline is running
    asyncio.create_task(_play_greeting(task, lang=config.stt_language))

    runner = PipelineRunner()

    try:
        await runner.run(task)
    except asyncio.CancelledError:
        pass  # Clean shutdown from main
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
    finally:
        await state.set_voice_active(False)
        state.pipeline_task = None
        # Keep llm_context alive so _run_all can read messages after cancellation

    return context, context.get_messages()
