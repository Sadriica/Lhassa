"""Tests for potts.config — provider detection, model defaults, validation."""

import pytest


def _reload_config():
    """Force re-evaluation of Config class-level attributes after env changes.

    Patches dotenv.load_dotenv (the source) to a no-op so that the
    'from dotenv import load_dotenv' re-executed during importlib.reload
    gets the mock, preventing the .env file from overriding the test env.
    """
    import importlib
    import unittest.mock as mock
    import potts.config as mod
    with mock.patch("dotenv.load_dotenv"):
        importlib.reload(mod)
    return mod.Config


# ── Provider auto-detection ────────────────────────────────────────────────────

def test_detect_provider_anthropic(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    Config = _reload_config()
    assert Config.llm_provider == "anthropic"


def test_detect_provider_openai(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    Config = _reload_config()
    assert Config.llm_provider == "openai"


def test_detect_provider_groq(clean_env):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    Config = _reload_config()
    assert Config.llm_provider == "groq"


def test_detect_provider_priority(clean_env):
    """Anthropic wins over Groq when both keys are set."""
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    Config = _reload_config()
    assert Config.llm_provider == "anthropic"


def test_detect_provider_explicit_override(clean_env):
    """POTTS_LLM_PROVIDER overrides auto-detection."""
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    clean_env.setenv("POTTS_LLM_PROVIDER", "openrouter")
    Config = _reload_config()
    assert Config.llm_provider == "openrouter"


# ── Model defaults ─────────────────────────────────────────────────────────────

def test_default_model_groq(clean_env):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    Config = _reload_config()
    assert Config.model == "llama-3.3-70b-versatile"


def test_default_model_anthropic(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    Config = _reload_config()
    assert Config.model == "claude-haiku-4-5-20251001"


def test_default_model_openai(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    Config = _reload_config()
    assert Config.model == "gpt-4o-mini"


def test_model_override(clean_env):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    clean_env.setenv("POTTS_MODEL", "llama-3.1-8b-instant")
    Config = _reload_config()
    assert Config.model == "llama-3.1-8b-instant"


# ── _safe_int ──────────────────────────────────────────────────────────────────

def test_safe_int_valid(clean_env):
    from potts.config import _safe_int
    assert _safe_int("8080", 7860) == 8080


def test_safe_int_invalid(clean_env):
    from potts.config import _safe_int
    assert _safe_int("not-a-number", 7860) == 7860


def test_safe_int_empty(clean_env):
    from potts.config import _safe_int
    assert _safe_int("", 7860) == 7860


# ── POTTS_WAKE_TIMEOUT ─────────────────────────────────────────────────────────

def test_wake_timeout_default(clean_env):
    Config = _reload_config()
    assert Config.wake_timeout == 300.0


def test_wake_timeout_custom(clean_env):
    clean_env.setenv("POTTS_WAKE_TIMEOUT", "60")
    Config = _reload_config()
    assert Config.wake_timeout == 60.0


# ── POTTS_MAX_AGENT_ITERATIONS ─────────────────────────────────────────────────

def test_max_agent_iterations_default(clean_env):
    _reload_config()
    # MAX_ITERATIONS is at module level in base.py, not Config — just check it parses
    clean_env.setenv("POTTS_MAX_AGENT_ITERATIONS", "10")
    import importlib
    import potts.agents.base as base_mod
    importlib.reload(base_mod)
    assert base_mod.MAX_ITERATIONS == 10


def test_max_agent_iterations_custom(clean_env):
    clean_env.setenv("POTTS_MAX_AGENT_ITERATIONS", "20")
    import importlib
    import potts.agents.base as base_mod
    importlib.reload(base_mod)
    assert base_mod.MAX_ITERATIONS == 20


# ── validate() — required keys ─────────────────────────────────────────────────

def test_validate_raises_on_missing_deepgram(clean_env):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    clean_env.setenv("CARTESIA_API_KEY", "cart_test")
    Config = _reload_config()
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
        Config().validate()


def test_validate_raises_on_missing_cartesia(clean_env):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    clean_env.setenv("DEEPGRAM_API_KEY", "dg_test")
    Config = _reload_config()
    with pytest.raises(RuntimeError, match="CARTESIA_API_KEY"):
        Config().validate()


def test_validate_raises_on_unknown_provider(clean_env):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    clean_env.setenv("DEEPGRAM_API_KEY", "dg_test")
    clean_env.setenv("CARTESIA_API_KEY", "cart_test")
    clean_env.setenv("POTTS_LLM_PROVIDER", "fakeprovider")
    Config = _reload_config()
    with pytest.raises(RuntimeError, match="Unknown LLM provider"):
        Config().validate()


def test_validate_raises_on_missing_llm_key(clean_env):
    clean_env.setenv("DEEPGRAM_API_KEY", "dg_test")
    clean_env.setenv("CARTESIA_API_KEY", "cart_test")
    clean_env.setenv("POTTS_LLM_PROVIDER", "groq")
    # No GROQ_API_KEY set
    Config = _reload_config()
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        Config().validate()
