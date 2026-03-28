"""Genera y reproduce sonidos de feedback de Potts."""

import asyncio
import math
import struct
import pyaudio


def _generate_tone(freq: float, duration_ms: int, volume: float = 0.3, sample_rate: int = 44100) -> bytes:
    num_samples = int(sample_rate * duration_ms / 1000)
    # Fade in/out para evitar clicks
    fade = int(num_samples * 0.1)
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        val = math.sin(2 * math.pi * freq * t) * volume
        # Envelope
        if i < fade:
            val *= i / fade
        elif i > num_samples - fade:
            val *= (num_samples - i) / fade
        samples.append(int(val * 32767))
    return struct.pack(f"<{num_samples}h", *samples)


async def play_beep(freq: float = 880.0, duration_ms: int = 120, volume: float = 0.25):
    """Reproduce un tono corto de forma no bloqueante."""
    await asyncio.to_thread(_play_blocking, freq, duration_ms, volume)


def _play_blocking(freq: float, duration_ms: int, volume: float):
    p = pyaudio.PyAudio()
    try:
        # Buscar PipeWire
        pw_idx = None
        for i in range(p.get_device_count()):
            if "pipewire" in p.get_device_info_by_index(i)["name"].lower():
                pw_idx = i
                break

        data = _generate_tone(freq, duration_ms, volume)
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=44100,
            output=True,
            output_device_index=pw_idx,
        )
        stream.write(data)
        stream.stop_stream()
        stream.close()
    except Exception:
        pass  # El beep es cosmético — nunca crashear por él
    finally:
        p.terminate()
