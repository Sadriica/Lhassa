"""Shared pytest fixtures."""

# Isolate the test suite from the owner's runtime overrides: config.py loads
# ~/.lhassa/settings.env (e.g. LHASSA_LLM_PROVIDER=claude) over .env, which would leak
# the live machine's settings into tests. Set BEFORE any lhassa import so config.py
# (imported during collection) skips that load. See lhassa/config.py.
import os

os.environ.setdefault("LHASSA_NO_RUNTIME_OVERRIDES", "1")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402,F401  (registers asyncio mode)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect the SQLite DB to a temporary file for each test."""
    db_path = tmp_path / "test_memory.db"
    import lhassa.memory.store as store
    monkeypatch.setattr(store, "DB_PATH", db_path)
    return db_path


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all LHASSA_* and provider keys from the environment."""
    keys = [
        "LHASSA_LLM_PROVIDER", "LHASSA_MODEL", "LHASSA_STT_LANGUAGE",
        "LHASSA_AGENT_BACKEND", "LHASSA_AGENT_DIR", "LHASSA_DEBUG",
        "LHASSA_LOG_LEVEL", "LHASSA_API_PORT", "LHASSA_API_HOST",
        "LHASSA_MIC_NAME", "LHASSA_VISION_PROVIDER", "LHASSA_VISION_MODEL",
        "LHASSA_WAKE_TIMEOUT", "LHASSA_MAX_AGENT_ITERATIONS",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
        "OPENROUTER_API_KEY", "CEREBRAS_API_KEY",
        "DEEPGRAM_API_KEY", "CARTESIA_API_KEY",
        "BRAVE_API_KEY", "GITHUB_TOKEN",
        "MS_TENANT_ID", "MS_CLIENT_ID",
        "LHASSA_ALLOW_DESTRUCTIVE_SHELL", "LHASSA_FILE_WRITE_ROOT",
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch
