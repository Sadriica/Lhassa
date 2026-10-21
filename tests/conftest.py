"""Shared pytest fixtures."""

# Isolate the test suite from the owner's runtime overrides: config.py loads
# ~/.lhassa/settings.env (e.g. LHASSA_LLM_PROVIDER=claude) over .env, which would leak
# the live machine's settings into tests. Set BEFORE any lhassa import so config.py
# (imported during collection) skips that load. See lhassa/config.py.
import asyncio
from dataclasses import asdict
import os
import json

os.environ.setdefault("LHASSA_NO_RUNTIME_OVERRIDES", "1")
os.environ.setdefault("LHASSA_QUARANTINE_ISOLATION", "none")
# Hermetic embeddings: a LIVE local Ollama must never change test behavior or
# speed; the whole suite runs on the offline hashing embedder unless a test
# explicitly opts a config double into a remote backend.
os.environ["LHASSA_EMBEDDER_BACKEND"] = "hash"
# Neutralize any real external endpoints from the owner's .env: config.py's
# load_dotenv uses override=False, so a value set here BEFORE the lhassa import
# wins. The calendar feed URL especially must be blank; otherwise the agenda
# would fetch the owner's real calendar over the network during unit tests.
os.environ["LHASSA_CALENDAR_ICS_URL"] = ""
# Same reason for the named-calendar store: a test must never read the owner's
# real ~/.lhassa/calendars.json (which would fetch their real feeds over the
# network). Point it at a path that does not exist so the store degrades to [].
os.environ["LHASSA_CALENDARS_FILE"] = "/nonexistent/lhassa-test/calendars.json"
# Notifications must never LEAVE the test process. The desktop channel pops a
# real `notify-send` toast on the owner's screen and the Telegram channel would
# message their real chat; both were firing during the suite, because any test
# that exercises a notify path (e.g. the goal-loop turn cap in core/step.py)
# reaches the live dispatcher. The `log` channel is always configured and only
# writes to the logger, so fan-out still happens and nothing escapes.
os.environ["LHASSA_NOTIFY_CHANNELS"] = "log"
os.environ["LHASSA_TELEGRAM_BOT_TOKEN"] = ""
os.environ["LHASSA_TELEGRAM_CHAT_ID"] = ""
# The owner's .env may put the notification voice on the MODEL-written path
# (notify_style / digest_style = "persona", plus a language pin). A test that
# asserts on a notification's text would then be asserting on a live re-voicing:
# non-deterministic, billed, and written in whatever language the owner set (an
# English assertion met a Spanish sentence, which is how this was found). The
# suite always runs the $0 deterministic path; the persona pass has its own
# tests, which drive it through an explicit config double.
os.environ["LHASSA_NOTIFY_STYLE"] = "template"
os.environ["LHASSA_DIGEST_STYLE"] = "plain"
os.environ["LHASSA_NOTIFY_LANGUAGE"] = ""
os.environ["LHASSA_DIGEST_LANGUAGE"] = ""
# Document perception must never read the owner's real notes. With the owner's
# .env present, `ingest_knowledge` walked their live knowledge roots during the
# suite (hundreds of real files indexed, ~27s of it) and the temple test that
# asserts the empty-roots precondition met a successful ingest instead. Same
# reason as the calendar URL above: blank it BEFORE the lhassa import, because
# config.py's load_dotenv uses override=False.
os.environ["LHASSA_KNOWLEDGE_ROOTS"] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402,F401  (registers asyncio mode)

# fastapi ships with the api pack (lhassa-api); core alone has none, and then
# there is no TestClient to swap.
try:
    import fastapi.testclient as fastapi_testclient  # noqa: E402
except ImportError:  # core alone
    fastapi_testclient = None


class ASGISyncTestClient:
    __test__ = False

    """Small sync test client over httpx.ASGITransport.

    Starlette's synchronous TestClient can hang in this environment because its
    AnyIO blocking portal never completes. The async ASGI transport is stable, so
    sync API tests use it through this wrapper.
    """

    def __init__(self, app, base_url: str = "http://testclient", **_: object):
        self.app = app
        self.base_url = base_url

    def request(self, method: str, url: str, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url=self.base_url) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(run())

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def websocket_connect(self, url: str, **_: object):
        if url != "/ws":
            raise NotImplementedError(f"ASGISyncTestClient only fakes /ws, got {url!r}")
        return _SnapshotWebSocket()


class _SnapshotWebSocket:
    def __enter__(self):
        from lhassa.state import state

        state.load_audit_events()
        self._payload = {
            "type": "snapshot",
            "voice_active": state.is_voice_active,
            "metrics": state.metrics,
            "agents": [
                {
                    "id": r.id,
                    "name": r.name,
                    "status": r.status,
                    "task": r.task,
                    "output": r.output,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                }
                for r in state.agent_runs.values()
            ],
            "audit": [asdict(event) for event in state.audit_events],
        }
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def receive_text(self):
        return json.dumps(self._payload)


if fastapi_testclient is not None:
    fastapi_testclient.TestClient = ASGISyncTestClient


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect the SQLite DB to a temporary file for each test."""
    db_path = tmp_path / "test_memory.db"
    import lhassa.memory.store as store
    monkeypatch.setattr(store, "DB_PATH", db_path)
    return db_path


@pytest.fixture(autouse=True)
def _isolate_brain_db(tmp_path, monkeypatch):
    """Never let a test touch the real ~/.lhassa/brain.db.

    Every test gets a throwaway core DB by default; tests that care about the
    path still monkeypatch ``lhassa.core.db.DB_PATH`` themselves and simply
    override this. Guards against a WIP/unpatched test writing Episodes into
    the developer's live brain (it happened; see decisions.md 2026-07-03).
    """
    import lhassa.core.db as _core_db
    monkeypatch.setattr(_core_db, "DB_PATH", tmp_path / "conftest_brain.db")


@pytest.fixture(autouse=True)
def _reset_seen_telegram_updates():
    """Give every test a poller that has never seen an update before.

    The inbound poller de-dupes ``update_id`` in process-local state, which is
    correct for one long-lived poller and wrong for a suite where each test is
    a fresh poller reusing small ids: without this, the second test to use
    ``update_id`` 200 has its update silently dropped as a redelivery.

    Also resets the poller's other module-level state (held CVs, armed
    base-CV requests, chat history/turn counters, sticky topic): same
    process-local-state-outlives-a-poll hazard, just for different dicts.
    Without this, whichever test happened to run first for a given chat id
    left its conversation state behind for the next test that reused it,
    which is exactly the kind of order-dependent flake
    ``tests/test_telegram_inbound.py`` was seeing.
    """
    from lhassa.notify import telegram_inbound

    telegram_inbound.reset_seen_updates()
    telegram_inbound.reset_conversation_state()
    yield
    telegram_inbound.reset_seen_updates()
    telegram_inbound.reset_conversation_state()


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
