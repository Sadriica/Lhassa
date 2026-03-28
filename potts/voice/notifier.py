"""
Notification router — speaks via TTS when the voice pipeline is active,
falls back to a desktop notify-send notification when idle.
"""

import asyncio
import subprocess
from loguru import logger


async def notify(message: str, title: str = "Potts"):
    """Send a notification through the best available channel."""
    from potts.state import state

    if state.is_voice_active and state.pipeline_task is not None:
        try:
            from pipecat.frames.frames import TTSSpeakFrame
            await state.pipeline_task.queue_frame(TTSSpeakFrame(text=message))
            return
        except Exception as e:
            logger.warning(f"TTS notify failed, falling back to desktop: {e}")

    await asyncio.to_thread(_desktop_notify, title, message)


def _desktop_notify(title: str, message: str):
    try:
        subprocess.run(
            ["notify-send", "--app-name=Potts", title, message],
            timeout=5,
            capture_output=True,
        )
    except FileNotFoundError:
        # notify-send not installed — print to terminal as fallback
        print(f"\033[1;33m[{title}]\033[0m {message}")
    except Exception as e:
        logger.warning(f"Desktop notification error: {e}")
