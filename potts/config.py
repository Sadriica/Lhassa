import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    """Read a required env var; raise at startup if missing."""
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {key}\n"
            f"Add it to your .env file."
        )
    return val


class Config:
    # LLM
    groq_api_key: str = _require("GROQ_API_KEY")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # STT
    deepgram_api_key: str = _require("DEEPGRAM_API_KEY")

    # TTS
    cartesia_api_key: str = _require("CARTESIA_API_KEY")
    voice_id: str = os.getenv("POTTS_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091")

    # General
    log_level: str = os.getenv("POTTS_LOG_LEVEL", "INFO")
    debug: bool = os.getenv("POTTS_DEBUG", "").lower() in ("1", "true", "yes")
    model: str = os.getenv("POTTS_MODEL", "llama-3.3-70b-versatile")

    # Dashboard API
    api_host: str = os.getenv("POTTS_API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("POTTS_API_PORT", "7860"))

    # Microsoft Graph — Outlook email + calendar (optional)
    ms_tenant_id: str = os.getenv("MS_TENANT_ID", "")
    ms_client_id: str = os.getenv("MS_CLIENT_ID", "")

    # GitHub (optional)
    github_token: str = os.getenv("GITHUB_TOKEN", "")
