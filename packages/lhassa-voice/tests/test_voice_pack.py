"""The voice pack registers itself and its notifier speaker, with no pipecat."""

import importlib
import sys

from lhassa import extensions
from lhassa.state import state
from lhassa.tools import notifier


def test_pack_registers_idempotently():
    import lhassa.voice.pack as pack

    importlib.reload(pack)  # a second import must not raise
    assert extensions.PACKS.get("voice").module == "lhassa.voice.pack"
    from lhassa.voice.speaker import speak

    assert notifier._SPEAKERS.count(speak) == 1


def test_pack_import_does_not_pull_the_pipeline():
    import lhassa.voice.pack  # noqa: F401

    assert "lhassa.voice.pipeline" not in sys.modules


async def test_speaker_declines_while_voice_is_idle(monkeypatch):
    from lhassa.voice.speaker import speak

    monkeypatch.setattr(state, "is_voice_active", False)
    assert await speak("hi") is False


async def test_speaker_queues_a_frame_while_awake(monkeypatch):
    from lhassa.voice import speaker

    queued = []

    class _Task:
        async def queue_frame(self, frame):
            queued.append(frame)

    monkeypatch.setattr(state, "is_voice_active", True)
    monkeypatch.setattr(state, "pipeline_task", _Task())
    spoke = await speaker.speak("hello")
    try:
        import pipecat  # noqa: F401
    except ImportError:
        # Runtime extra absent: decline so the notifier uses the desktop.
        assert spoke is False and queued == []
    else:
        assert spoke is True and queued[0].text == "hello"
