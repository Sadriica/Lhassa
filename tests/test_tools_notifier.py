"""The desktop notifier's speaker seam (``lhassa.tools.notifier``).

Core owns the notifier; a surface that can talk (the voice pack) plugs in
through ``register_speaker``. With no speaker, or one that declines or raises,
the notification goes to the desktop.
"""

import pytest

from lhassa.tools import notifier


@pytest.fixture
def desktop(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notifier, "_desktop_notify", lambda t, m: sent.append((t, m)))
    monkeypatch.setattr(notifier, "_SPEAKERS", [])
    return sent


async def test_no_speaker_goes_to_desktop(desktop):
    await notifier.notify("hello", title="T")
    assert desktop == [("T", "hello")]


async def test_a_speaker_that_speaks_wins(desktop):
    spoken: list[str] = []

    async def speak(msg):
        spoken.append(msg)
        return True

    notifier.register_speaker(speak)
    notifier.register_speaker(speak)  # idempotent
    assert notifier._SPEAKERS == [speak]
    await notifier.notify("hi")
    assert spoken == ["hi"] and desktop == []


async def test_declining_or_raising_speaker_falls_back(desktop):
    async def decline(msg):
        return False

    async def boom(msg):
        raise RuntimeError("no audio")

    notifier.register_speaker(decline)
    notifier.register_speaker(boom)
    await notifier.notify("x")
    assert desktop == [("Lhassa", "x")]
    notifier.unregister_speaker(boom)
    notifier.unregister_speaker(boom)  # absent: no raise
    assert notifier._SPEAKERS == [decline]


async def test_cli_tool_loop_runs_a_handler_without_pipecat():
    """``cli/agent._execute_tool`` must work on a core-only install."""
    from lhassa.cli import agent

    async def handler(params):
        await params.result_callback(f"got {params.arguments['q']}")

    out = await agent._execute_tool({"t": handler}, "t", {"q": "it"})
    assert out == "got it"
