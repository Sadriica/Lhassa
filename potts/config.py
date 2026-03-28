import os
from dotenv import load_dotenv

load_dotenv()

_PROVIDERS = {"groq", "anthropic", "openai", "openrouter", "ollama", "cerebras"}


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
    # ── LLM provider ──────────────────────────────────────────────────────────
    # Which provider powers both the voice pipeline and the sub-agents.
    # Options: groq | anthropic | openai | openrouter | ollama | cerebras
    llm_provider: str = os.getenv("POTTS_LLM_PROVIDER", "groq").lower()

    # Model name — interpreted by whichever provider is selected
    model: str = os.getenv("POTTS_MODEL", "llama-3.3-70b-versatile")

    # ── Provider API keys (only the active provider's key is required) ────────
    groq_api_key: str       = os.getenv("GROQ_API_KEY", "")
    anthropic_api_key: str  = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str     = os.getenv("OPENAI_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    cerebras_api_key: str   = os.getenv("CEREBRAS_API_KEY", "")

    # Ollama — local, no key needed
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # ── STT ───────────────────────────────────────────────────────────────────
    deepgram_api_key: str = _require("DEEPGRAM_API_KEY")

    # ── TTS ───────────────────────────────────────────────────────────────────
    cartesia_api_key: str = _require("CARTESIA_API_KEY")
    voice_id: str = os.getenv("POTTS_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091")

    # ── Audio ─────────────────────────────────────────────────────────────────
    # Substring matched against PyAudio device names (case-insensitive).
    # Defaults to "pipewire" on Linux; set to "" to use system default.
    mic_name: str = os.getenv("POTTS_MIC_NAME", "pipewire")

    # ── General ───────────────────────────────────────────────────────────────
    log_level: str = os.getenv("POTTS_LOG_LEVEL", "INFO")
    debug: bool = os.getenv("POTTS_DEBUG", "").lower() in ("1", "true", "yes")

    # ── Dashboard API ─────────────────────────────────────────────────────────
    api_host: str = os.getenv("POTTS_API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("POTTS_API_PORT", "7860"))

    # ── Microsoft Graph — Outlook email + calendar (optional) ─────────────────
    ms_tenant_id: str = os.getenv("MS_TENANT_ID", "")
    ms_client_id: str = os.getenv("MS_CLIENT_ID", "")

    # ── Web search ────────────────────────────────────────────────────────────
    # Brave Search API key — optional, falls back to DuckDuckGo if not set
    brave_api_key: str = os.getenv("BRAVE_API_KEY", "")

    # ── GitHub (optional) ─────────────────────────────────────────────────────
    github_token: str = os.getenv("GITHUB_TOKEN", "")

    def validate(self):
        """Raise RuntimeError if the active provider has no API key configured."""
        provider = self.llm_provider
        if provider not in _PROVIDERS:
            raise RuntimeError(
                f"Unknown LLM provider: {provider!r}. "
                f"Valid options: {', '.join(sorted(_PROVIDERS))}"
            )

        key_map = {
            "groq": ("groq_api_key", "GROQ_API_KEY"),
            "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
            "openai": ("openai_api_key", "OPENAI_API_KEY"),
            "openrouter": ("openrouter_api_key", "OPENROUTER_API_KEY"),
            "cerebras": ("cerebras_api_key", "CEREBRAS_API_KEY"),
        }
        if provider in key_map:
            attr, env = key_map[provider]
            if not getattr(self, attr):
                raise RuntimeError(
                    f"Provider '{provider}' requires {env} — add it to your .env file."
                )
