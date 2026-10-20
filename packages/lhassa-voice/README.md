# lhassa-voice

The voice surface as a capability pack: wake word, Deepgram STT, a brain turn
(`brain_bridge`, when `LHASSA_VOICE_ENABLED=1`), Cartesia TTS over the local
audio device. Entrypoint: `main.py` at the repo root (`make run` / `make dev`).
Guide: `doc/voice-interaction.md`.

## Install

```bash
uv sync --package lhassa-voice --extra runtime   # core + this pack, with the audio stack
uv sync --all-packages --extra runtime           # core + every pack, with the audio stack (make install)
uv sync --all-packages                           # every pack, voice registered but no audio stack
```

In a uv workspace `--extra runtime` selects the extra on every selected member
that defines it; with `--package lhassa-voice` that is only this pack. A sync
is exact, so add `--extra dev` when you also want the test tools.

## What is light and what is heavy

- Base install (`lhassa-voice`, no extra): `pack.py`, `speaker.py`,
  `brain_bridge.py` and `llm_service.py` import without pipecat. The pack
  registers itself and a TTS speaker on core's notifier seam
  (`lhassa.tools.notifier.register_speaker`); the speaker declines (desktop
  notification instead) unless the pipeline is running and awake.
- `runtime` extra (`pipecat-ai[...]`, `torch`): needed by `pipeline.py`,
  `mute_strategy.py` and `beep.py`, which import pipecat/pyaudio at module
  load. `main.py` loads the pipeline lazily and exits with an install hint
  when the pack or its runtime is missing.

## Tests

`packages/lhassa-voice/tests/test_voice_*.py`: no pipecat, no audio hardware.
