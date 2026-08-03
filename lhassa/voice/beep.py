# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generates and plays short audio feedback tones for Lhassa UI events."""

import asyncio
import math
import platform
import struct
import pyaudio

_SYSTEM = platform.system()


def _generate_tone(
    freq: float,
    duration_ms: int,
    volume: float = 0.3,
    sample_rate: int = 44100,
) -> bytes:
    """Return PCM bytes for a sine wave with a short fade-in/out envelope."""
    num_samples = int(sample_rate * duration_ms / 1000)
    fade = int(num_samples * 0.1)
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        val = math.sin(2 * math.pi * freq * t) * volume
        # Linear fade-in / fade-out to avoid clicks
        if i < fade:
            val *= i / fade
        elif i > num_samples - fade:
            val *= (num_samples - i) / fade
        samples.append(int(val * 32767))
    return struct.pack(f"<{num_samples}h", *samples)


# Cache PipeWire device index — scan only once instead of on every beep
_pw_scanned: bool = False
_pw_idx: int | None = None


async def play_beep(freq: float = 880.0, duration_ms: int = 120, volume: float = 0.25):
    """Play a short tone non-blocking (runs in a thread pool)."""
    await asyncio.to_thread(_play_blocking, freq, duration_ms, volume)


def _play_blocking(freq: float, duration_ms: int, volume: float):
    global _pw_scanned, _pw_idx
    p = pyaudio.PyAudio()
    try:
        # PipeWire device lookup is Linux-only; skip the scan on macOS/Windows
        if not _pw_scanned:
            _pw_scanned = True
            if _SYSTEM == "Linux":
                for i in range(p.get_device_count()):
                    if "pipewire" in p.get_device_info_by_index(i)["name"].lower():
                        _pw_idx = i
                        break

        data = _generate_tone(freq, duration_ms, volume)
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=44100,
            output=True,
            output_device_index=_pw_idx,
        )
        try:
            stream.write(data)
        finally:
            stream.stop_stream()
            stream.close()
    except Exception:
        pass  # Beep is cosmetic — never crash because of it
    finally:
        p.terminate()
