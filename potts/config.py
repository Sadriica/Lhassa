import os
from dotenv import load_dotenv

load_dotenv()


def require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


class Config:
    # LLM — Groq (free) by default, swap to Anthropic when ready
    groq_api_key: str = require("GROQ_API_KEY")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # STT
    deepgram_api_key: str = require("DEEPGRAM_API_KEY")

    # TTS
    cartesia_api_key: str = require("CARTESIA_API_KEY")
    voice_id: str = os.getenv("POTTS_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091")

    # General
    log_level: str = os.getenv("POTTS_LOG_LEVEL", "INFO")
    model: str = os.getenv("POTTS_MODEL", "llama-3.3-70b-versatile")
