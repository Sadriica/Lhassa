"""Shared pytest fixtures."""


import pytest
import pytest_asyncio  # noqa: F401  (registers asyncio mode)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect the SQLite DB to a temporary file for each test."""
    db_path = tmp_path / "test_memory.db"
    import potts.memory.store as store
    monkeypatch.setattr(store, "DB_PATH", db_path)
    return db_path


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all POTTS_* and provider keys from the environment."""
    keys = [
        "POTTS_LLM_PROVIDER", "POTTS_MODEL", "POTTS_STT_LANGUAGE",
        "POTTS_AGENT_BACKEND", "POTTS_AGENT_DIR", "POTTS_DEBUG",
        "POTTS_LOG_LEVEL", "POTTS_API_PORT", "POTTS_API_HOST",
        "POTTS_MIC_NAME", "POTTS_VISION_PROVIDER", "POTTS_VISION_MODEL",
        "POTTS_WAKE_TIMEOUT", "POTTS_MAX_AGENT_ITERATIONS",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
        "OPENROUTER_API_KEY", "CEREBRAS_API_KEY",
        "DEEPGRAM_API_KEY", "CARTESIA_API_KEY",
        "BRAVE_API_KEY", "GITHUB_TOKEN",
        "MS_TENANT_ID", "MS_CLIENT_ID",
        "POTTS_ALLOW_DESTRUCTIVE_SHELL", "POTTS_FILE_WRITE_ROOT",
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch
