# Voice Interaction

This document explains how to talk to Lhassa: how to wake it, how to speak to it, how to interrupt it, and how to get the most reliable recognition.

---

## The Wake Word System

Lhassa uses a **wake word strategy**: it is always listening, but it only processes what you say after it hears its name. This is how it avoids reacting to background conversation, television audio, or other ambient sound.

The primary wake word is **"Lhassa"**.

### Wake Word Variants

Because Deepgram's STT is configured for Spanish (the language the developer converses in), and because "Lhassa" is an unusual English proper noun, the system accepts a wide range of phonetically similar words to reduce missed wake-ups:

| Variant | Why it's included |
|---|---|
| `lhassa` | Primary (canonical) |
| `pots` | Common anglophone mishearing |
| `pods` | Common mishearing |
| `pot` | Short form |
| `hey lhassa` | With greeting prefix |
| `hey pots` | With greeting, alternate spelling |
| `hey pods` | With greeting |
| `oye lhassa` | Spanish greeting ("hey Lhassa") |
| `oye pots` | Spanish greeting, alternate |
| `oye pods` | Spanish greeting, alternate |
| `pos` | Spanish informal word, sounds like "Lhassa" |
| `pues` | Spanish filler word |
| `box` | Phonetic near-match |
| `bots` | Phonetic near-match |
| `bot` | Short phonetic near-match |
| `voz` | Spanish word for "voice" |
| `vos` | Spanish pronoun |
| `boz` | Phonetic near-match |
| `pox` | Phonetic near-match |
| `a ver lhassa` | Spanish "come on, Lhassa" |
| `a ver pots` | Alternate spelling |

You don't need to remember this list. Just say "Lhassa" naturally. If recognition is unreliable, see the tips section below.

---

## Audio Feedback: The Beep Sounds

Lhassa uses short audio tones to signal its state without interrupting the flow of conversation.

| Tone | Frequency | Duration | Meaning |
|---|---|---|---|
| High beep | 880 Hz | 120 ms | **Lhassa just woke up** — it heard your wake word and is now listening for your request |
| Low beep | 440 Hz | 80 ms | **Lhassa went to sleep** — the 5-minute inactivity timeout fired |

If you hear nothing when you say "Lhassa", the wake word was not recognised. Try again, or check your microphone.

---

## The 5-Minute Auto-Sleep Timeout

After Lhassa wakes up, if it doesn't detect you speaking for **5 minutes**, it automatically goes back to sleep. You'll hear the low beep and see:

```
[Lhassa sleeping — say 'Lhassa' to wake]
```

This prevents the LLM from receiving accidental transcriptions of background audio. To use Lhassa again, say the wake word.

The timeout counter resets every time Lhassa finishes speaking a response — so as long as the conversation is active, it won't time out mid-session.

---

## Interrupting Lhassa While It's Speaking

If Lhassa is speaking and you want to stop it and ask something else, say the **wake word again**. The mute strategy used by the pipeline passes wake word transcriptions through even while the bot is speaking, which triggers an interruption and starts a new turn.

The pipeline has `allow_interruptions=True`, so audio playback stops immediately when the wake word is detected mid-speech.

Example:

```
You: Lhassa, tell me everything about the history of the Roman Empire.
Lhassa: The Roman Empire was one of the largest...
You: Lhassa, actually, just give me the founding date.
Lhassa: [stops immediately] Rome was traditionally founded in 753 BC.
```

---

## Language

Deepgram STT is configured with `language="es"` (Spanish) and `keyterm=["Lhassa"]` for boosted recognition of the wake word. This means:

- **Speech recognition performs best with Spanish** — but it handles code-switching (mixing Spanish and English) well.
- **Lhassa responds in whatever language you speak to it.** The system prompt does not impose a language on the LLM. If you speak English, Lhassa answers in English. If you speak Spanish, it answers in Spanish.
- The Spanish STT setting is why some wake word variants are Spanish words — they're phonetic near-matches that the model might output for the sound "Lhassa."

If you primarily speak English and are experiencing wake word issues, you can experiment with changing the `language` setting in `lhassa/voice/pipeline.py`, but be aware this may affect recognition quality for Spanish.

---

## WakeDebug Mode: Seeing Live STT Output

If you're having trouble with wake word recognition, enable debug mode to see exactly what the STT is transcribing in real time.

**Method 1 — Makefile:**
```bash
make dev
```

**Method 2 — Environment variable:**
```bash
LHASSA_DEBUG=1 uv run lhassa
```

**Method 3 — `.env` file:**
```
LHASSA_DEBUG=1
```

With debug mode on, every transcription frame prints to the terminal as it arrives:

```
[i] 'po'
[i] 'pots'
[F] 'Lhassa, what time is it?'
```

`[i]` = interim (partial) transcription, `[F]` = final transcription. This lets you see exactly what the STT model is outputting so you can adjust your pronunciation or microphone accordingly.

---

## Tips for Reliable Wake Word Recognition

### Microphone position and gain
- Use a dedicated microphone rather than a built-in laptop mic if possible.
- Position it so your mouth is within 30–60 cm.
- Check your input level in PipeWire with `pavucontrol` — you want to be hitting roughly 50–70% on the input meter when speaking normally.

### Environment
- Reduce background noise. Fans, music, and TV audio all degrade transcription quality.
- If you're in a noisy environment, speak slightly louder or move closer to the mic.

### Pronunciation
- The STT is tuned for Spanish. Saying "Lhassa" with a Spanish accent — somewhere between "pots" and "pods" — often improves recognition.
- A short, clear utterance works better than drawing out the word.
- You don't need a specific tone of voice; a normal conversational volume is fine.

### After the wake beep
- Wait until you hear the 880 Hz beep before speaking your actual request. The wake word detection and the voice activity detection (VAD) have a brief handoff moment.
- Speak at a natural pace. Silero VAD detects end-of-speech automatically and sends the full utterance to the LLM once you stop talking.

### If recognition is consistently failing
- Run `make dev` to see live STT output.
- Check that your `.env` has valid API keys (Deepgram will silently fail with invalid keys).
- Verify your microphone is being picked up: `arecord -d 3 -f cd /tmp/test.wav && aplay /tmp/test.wav`.
