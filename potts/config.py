import os
import platform
import shutil
from dotenv import load_dotenv

load_dotenv()

_PROVIDERS = {"groq", "anthropic", "openai", "openrouter", "ollama", "cerebras"}

# Detect the claude CLI once at import time so the default is resolved before
# Config is instantiated and the result is stable across the process lifetime.
_CLAUDE_AVAILABLE = shutil.which("claude") is not None

# Default models per provider — used when POTTS_MODEL is not set.
_DEFAULT_MODELS = {
    "anthropic":  "claude-haiku-4-5-20251001",
    "openai":     "gpt-4o-mini",
    "groq":       "llama-3.3-70b-versatile",
    "openrouter": "anthropic/claude-haiku-4-5-20251001",
    "cerebras":   "llama3.3-70b",
    "ollama":     "llama3.2",
}


def _detect_provider() -> str:
    """
    Resolve POTTS_LLM_PROVIDER.

    If POTTS_LLM_PROVIDER is set explicitly, use it.
    Otherwise, auto-detect from whichever API key is present:
      ANTHROPIC_API_KEY → anthropic
      OPENAI_API_KEY    → openai
      GROQ_API_KEY      → groq
      OPENROUTER_API_KEY → openrouter
      CEREBRAS_API_KEY  → cerebras
    Falls back to "groq" (will fail validate() if no key is set).
    """
    explicit = os.getenv("POTTS_LLM_PROVIDER")
    if explicit:
        return explicit.lower()
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.getenv("CEREBRAS_API_KEY"):
        return "cerebras"
    return "groq"  # fallback; validate() will catch the missing key


def _detect_model(provider: str) -> str:
    """
    Resolve POTTS_MODEL. Returns the explicit value if set,
    otherwise the sensible default for the given provider.
    """
    explicit = os.getenv("POTTS_MODEL")
    if explicit:
        return explicit
    return _DEFAULT_MODELS.get(provider, "llama-3.3-70b-versatile")


def _safe_int(val: str, default: int) -> int:
    """Parse val as int, returning default if it's not a valid integer."""
    try:
        return int(val)
    except ValueError:
        return default


class Config:
    # ── LLM provider (voice pipeline) ─────────────────────────────────────────
    # Which provider powers the voice pipeline and the built-in agent tool loop.
    # Options: groq | anthropic | openai | openrouter | ollama | cerebras
    #
    # Auto-detected from whichever API key is present if POTTS_LLM_PROVIDER is
    # not set. Priority: anthropic → openai → groq → openrouter → cerebras.
    # This means setting only ANTHROPIC_API_KEY is enough — no POTTS_LLM_PROVIDER
    # needed, and vision tools will also work automatically.
    llm_provider: str = _detect_provider()

    # Model name — interpreted by whichever provider is selected.
    # Auto-set to the provider default if POTTS_MODEL is not set:
    #   anthropic  → claude-haiku-4-5-20251001
    #   openai     → gpt-4o-mini
    #   groq       → llama-3.3-70b-versatile
    #   openrouter → anthropic/claude-haiku-4-5-20251001
    #   cerebras   → llama3.3-70b
    #   ollama     → llama3.2
    model: str = _detect_model(llm_provider)

    # ── Provider API keys (only the active provider's key is required) ────────
    groq_api_key: str       = os.getenv("GROQ_API_KEY", "")
    anthropic_api_key: str  = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str     = os.getenv("OPENAI_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    cerebras_api_key: str   = os.getenv("CEREBRAS_API_KEY", "")

    # Ollama — local, no key needed.
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # ── STT ───────────────────────────────────────────────────────────────────
    deepgram_api_key: str = os.getenv("DEEPGRAM_API_KEY", "")
    # BCP-47 language code for speech recognition. Deepgram nova-3-general
    # supports: en, es, fr, de, pt, nl, hi, ja, and more.
    stt_language: str = os.getenv("POTTS_STT_LANGUAGE", "en")

    # ── TTS ───────────────────────────────────────────────────────────────────
    cartesia_api_key: str = os.getenv("CARTESIA_API_KEY", "")
    voice_id: str = os.getenv("POTTS_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091")

    # ── Audio ─────────────────────────────────────────────────────────────────
    # Substring matched against PyAudio device names (case-insensitive).
    # Set to "" to use the system default device.
    # Defaults to "pipewire" on Linux (common audio server), empty elsewhere.
    mic_name: str = os.getenv(
        "POTTS_MIC_NAME",
        "pipewire" if platform.system() == "Linux" else "",
    )

    # ── General ───────────────────────────────────────────────────────────────
    log_level: str = os.getenv("POTTS_LOG_LEVEL", "INFO")
    debug: bool = os.getenv("POTTS_DEBUG", "").lower() in ("1", "true", "yes")

    # Seconds of silence before Potts goes back to sleep after being woken.
    # Default: 300 (5 minutes). Set lower to save API costs on idle sessions.
    wake_timeout: float = float(os.getenv("POTTS_WAKE_TIMEOUT", "300"))

    # ── LLM gateway (cost control) ────────────────────────────────────────────
    # Single chokepoint for cloud-LLM calls: cache + budget + cost ledger.
    # Daily output-token budget; 0 = unlimited. When exceeded, the gateway
    # refuses further calls until the next day (UTC).
    llm_daily_token_budget: int = _safe_int(
        os.getenv("POTTS_LLM_DAILY_TOKEN_BUDGET", "0"), 0
    )
    # Cache identical prompts so repeat questions never hit the cloud.
    llm_cache_enabled: bool = os.getenv("POTTS_LLM_CACHE", "1").lower() in (
        "1", "true", "yes",
    )

    # ── Dashboard API ─────────────────────────────────────────────────────────
    api_host: str = os.getenv("POTTS_API_HOST", "0.0.0.0")
    api_port: int = _safe_int(os.getenv("POTTS_API_PORT", "7860"), 7860)

    # ── Microsoft Graph — Outlook email + calendar (optional) ─────────────────
    ms_tenant_id: str = os.getenv("MS_TENANT_ID", "")
    ms_client_id: str = os.getenv("MS_CLIENT_ID", "")

    # ── Web search ────────────────────────────────────────────────────────────
    # Brave Search API key — optional, falls back to DuckDuckGo if not set.
    brave_api_key: str = os.getenv("BRAVE_API_KEY", "")

    # ── Agent backend ─────────────────────────────────────────────────────────
    # "claude_code" — agents run via `claude --print` (your Claude subscription).
    # "builtin"     — agents use the internal LLM tool loop (POTTS_LLM_PROVIDER).
    #
    # If POTTS_AGENT_BACKEND is not set, Potts auto-detects:
    #   - claude CLI found in PATH  → claude_code
    #   - claude CLI not found      → builtin
    agent_backend: str = os.getenv(
        "POTTS_AGENT_BACKEND",
        "claude_code" if _CLAUDE_AVAILABLE else "builtin",
    ).lower()

    # Working directory for `claude --print`.
    # Only used when agent_backend=claude_code.
    # Defaults to the cwd where Potts was launched if not set.
    agent_dir: str = os.getenv("POTTS_AGENT_DIR", "")

    # ── Vision tools ──────────────────────────────────────────────────────────
    # Provider for analyze_screen / analyze_image.
    # "auto"       — use the active LLM provider if it supports vision, else
    #                fall back to ANTHROPIC_API_KEY → OPENAI_API_KEY
    # "anthropic"  — Claude Haiku (fast, great vision)
    # "openai"     — GPT-4o-mini
    # "openrouter" — any OpenRouter vision model
    vision_provider: str = os.getenv("POTTS_VISION_PROVIDER", "auto").lower()

    # Vision model override. Leave empty to use the provider default:
    #   anthropic  → claude-haiku-4-5-20251001
    #   openai     → gpt-4o-mini
    #   openrouter → anthropic/claude-haiku-4-5-20251001
    vision_model: str = os.getenv("POTTS_VISION_MODEL", "")

    # ── GitHub (optional) ─────────────────────────────────────────────────────
    github_token: str = os.getenv("GITHUB_TOKEN", "")

    # ── Tool guardrails ───────────────────────────────────────────────────────
    # If true, reject obviously destructive shell commands unless explicitly
    # opted out via POTTS_ALLOW_DESTRUCTIVE_SHELL=1.
    allow_destructive_shell: bool = os.getenv(
        "POTTS_ALLOW_DESTRUCTIVE_SHELL", ""
    ).lower() in ("1", "true", "yes")

    # Optional filesystem root for write_file. If set, write_file may only
    # create or modify files inside this directory tree.
    file_write_root: str = os.getenv("POTTS_FILE_WRITE_ROOT", "")

    def validate(self):
        """
        Check all active configuration at startup. Raises RuntimeError with a
        clear, actionable message on the first problem found.
        Logs the resolved agent backend so users always know what's active.
        """
        from loguru import logger

        # ── Required keys ─────────────────────────────────────────────────────
        if not self.deepgram_api_key:
            raise RuntimeError(
                "Missing required environment variable: DEEPGRAM_API_KEY\n"
                "Sign up at console.deepgram.com and add the key to your .env file."
            )
        if not self.cartesia_api_key:
            raise RuntimeError(
                "Missing required environment variable: CARTESIA_API_KEY\n"
                "Sign up at console.cartesia.ai and add the key to your .env file."
            )

        # ── Port ───────────────────────────────────────────────────────────────
        port_raw = os.getenv("POTTS_API_PORT", "7860")
        try:
            int(port_raw)
        except ValueError:
            raise RuntimeError(
                f"POTTS_API_PORT={port_raw!r} is not a valid port number.\n"
                "Set it to an integer between 1024 and 65535."
            )

        # ── LLM provider ──────────────────────────────────────────────────────
        provider = self.llm_provider
        auto_provider = not os.getenv("POTTS_LLM_PROVIDER")
        if auto_provider:
            logger.info(f"LLM provider: {provider} (auto-detected from API key)  model={self.model}")
        else:
            logger.info(f"LLM provider: {provider}  model={self.model}")

        if provider not in _PROVIDERS:
            raise RuntimeError(
                f"Unknown LLM provider: {provider!r}. "
                f"Valid options: {', '.join(sorted(_PROVIDERS))}"
            )

        key_map = {
            "groq":       ("groq_api_key",       "GROQ_API_KEY"),
            "anthropic":  ("anthropic_api_key",  "ANTHROPIC_API_KEY"),
            "openai":     ("openai_api_key",     "OPENAI_API_KEY"),
            "openrouter": ("openrouter_api_key", "OPENROUTER_API_KEY"),
            "cerebras":   ("cerebras_api_key",   "CEREBRAS_API_KEY"),
        }
        if provider in key_map:
            attr, env = key_map[provider]
            if not getattr(self, attr):
                raise RuntimeError(
                    f"POTTS_LLM_PROVIDER={provider!r} but {env} is not set.\n"
                    f"Add it to your .env file, or change POTTS_LLM_PROVIDER."
                )

        # ── Agent backend ──────────────────────────────────────────────────────
        backend = self.agent_backend

        if backend not in ("claude_code", "builtin"):
            raise RuntimeError(
                f"Unknown POTTS_AGENT_BACKEND={backend!r}. "
                f"Valid options: claude_code, builtin"
            )

        if backend == "claude_code":
            if not _CLAUDE_AVAILABLE:
                raise RuntimeError(
                    "POTTS_AGENT_BACKEND=claude_code but the 'claude' CLI was not found in PATH.\n"
                    "Install Claude Code: https://claude.ai/code\n"
                    "Or set POTTS_AGENT_BACKEND=builtin to use the built-in LLM tool loop."
                )
            cwd = os.getcwd()
            if not self.agent_dir:
                logger.warning(
                    f"POTTS_AGENT_DIR is not set — agents will run in {cwd}. "
                    "Add POTTS_AGENT_DIR=/path/to/your/project to .env."
                )
            detected = " (auto-detected)" if not os.getenv("POTTS_AGENT_BACKEND") else ""
            dir_info = self.agent_dir or cwd
            logger.info(f"Agent backend: claude_code{detected}  dir={dir_info}")
        else:
            detected = " (auto-detected: claude CLI not found)" if not os.getenv("POTTS_AGENT_BACKEND") else ""
            logger.info(f"Agent backend: builtin{detected}  provider={provider}  model={self.model}")

        # ── Vision provider ────────────────────────────────────────────────────
        _VISION_CAPABLE = {"anthropic", "openai", "openrouter"}
        _vision_key_map = {
            "anthropic":  self.anthropic_api_key,
            "openai":     self.openai_api_key,
            "openrouter": self.openrouter_api_key,
        }

        vp = self.vision_provider
        if vp not in (*_VISION_CAPABLE, "auto"):
            raise RuntimeError(
                f"Unknown POTTS_VISION_PROVIDER={vp!r}. "
                f"Valid options: auto, anthropic, openai, openrouter"
            )

        if vp != "auto":
            if not _vision_key_map.get(vp):
                raise RuntimeError(
                    f"POTTS_VISION_PROVIDER={vp!r} but the matching API key is not set.\n"
                    f"Add {vp.upper()}_API_KEY to .env."
                )
            resolved_vp = vp
        elif provider in _VISION_CAPABLE and _vision_key_map.get(provider):
            resolved_vp = provider  # active LLM provider supports vision
        elif self.anthropic_api_key:
            resolved_vp = "anthropic"
        elif self.openai_api_key:
            resolved_vp = "openai"
        else:
            resolved_vp = None  # vision tools will fail at call time with a clear message

        if resolved_vp:
            vm = self.vision_model or {"anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini", "openrouter": "anthropic/claude-haiku-4-5-20251001"}.get(resolved_vp, "")
            auto_note = " (auto-detected)" if vp == "auto" else ""
            logger.info(f"Vision provider: {resolved_vp}{auto_note}  model={vm}")
        else:
            logger.warning(
                "Vision tools disabled — no vision-capable API key found. "
                "Set ANTHROPIC_API_KEY or OPENAI_API_KEY to enable analyze_screen / analyze_image."
            )
