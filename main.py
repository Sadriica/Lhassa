import asyncio
import os
import sys
from pathlib import Path
from loguru import logger

from lhassa.config import Config
from lhassa.memory.session import close_session
from lhassa.memory.store import init_db
from lhassa.api.server import run_api_server
from lhassa.state import state

LOG_DIR = Path.home() / ".lhassa"

VOICE_INSTALL_HINT = "uv sync --package lhassa-voice --extra runtime"


def _load_voice_pipeline():
    """Return the voice pipeline's ``run`` or ``None`` with a clear log line.

    The pipeline ships in the ``lhassa-voice`` pack and needs its ``runtime``
    extra (pipecat, torch, pyaudio). Core alone has neither, so this entrypoint
    says which piece is missing instead of crashing on an import.
    """
    try:
        import lhassa.voice  # noqa: F401
    except ImportError:
        logger.error(f"The voice pack (lhassa-voice) is not installed. Install it with: {VOICE_INSTALL_HINT}")
        return None
    try:
        from lhassa.voice.pipeline import run
    except ImportError as exc:
        logger.error(
            f"The voice runtime is not installed ({exc}). Install it with: {VOICE_INSTALL_HINT}"
        )
        return None
    return run


def setup_logging(level: str):
    logger.remove()
    # Console — coloured, concise
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    # File — full detail, rotated at 10 MB, kept for 7 days
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        LOG_DIR / "lhassa.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


async def _close_http_clients():
    """Close all cached HTTP clients to release connection pools cleanly."""
    # httpx singletons in tool modules
    from lhassa.tools import search, github, graph
    for mod in (search, github, graph):
        client = getattr(mod, "_http", None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    # AsyncAnthropic / AsyncOpenAI singletons in the LLM provider cache
    from lhassa.llm import provider
    for client in provider._agent_clients.values():
        try:
            await client.aclose()
        except Exception:
            pass
    provider._agent_clients.clear()


async def _periodic_memory_snapshot(interval_s: int):
    """
    Periodically extract facts from the current conversation so they're not lost
    if Lhassa crashes before the normal shutdown path runs close_session().
    Only saves when new messages have accumulated since the last snapshot.
    """
    last_count = 0
    while True:
        await asyncio.sleep(interval_s)
        if state.llm_context is None:
            continue
        try:
            messages = state.llm_context.get_messages()
            if len(messages) <= last_count:
                continue  # nothing new since last snapshot
            last_count = len(messages)
            logger.debug("[memory] periodic snapshot — extracting facts...")
            await close_session(messages)
        except Exception as e:
            logger.warning(f"Periodic memory snapshot failed: {e}")


async def _run_all(config: Config, run_voice_pipeline):
    """
    Run voice pipeline as the primary task, with the API server as a background
    service that is cancelled cleanly when the voice pipeline exits. Saves
    session memory before returning so everything runs in the same event loop.
    """
    from lhassa import actionpack
    actionpack.register_all()

    await init_db()
    state.load_audit_events()
    config.validate()  # Check provider key is present before starting pipeline

    # One-shot, idempotent: copy any still-pending legacy reminders
    # (memory/store.py's scheduled_tasks) into the tasks table as
    # send_reminder tasks. Never blocks startup. See doc/scheduler.md.
    try:
        from lhassa.core.db import init_db as init_core_db
        from lhassa.tools.handlers.schedule import migrate_legacy_reminders

        await init_core_db()
        await migrate_legacy_reminders(config.instance_id)
    except Exception as exc:
        logger.warning(f"Legacy reminder migration failed: {exc}")

    api_task = asyncio.create_task(
        run_api_server(host=config.api_host, port=config.api_port),
        name="api-server",
    )
    snapshot_interval = max(60, int(os.getenv("LHASSA_MEMORY_SNAPSHOT_INTERVAL", "1800")))
    snapshot_task = asyncio.create_task(
        _periodic_memory_snapshot(snapshot_interval),
        name="memory-snapshot",
    )

    messages: list = []
    try:
        _context, messages = await run_voice_pipeline()
    except asyncio.CancelledError:
        # Pipeline was interrupted (Ctrl+C); try to recover messages from state
        if state.llm_context is not None:
            messages = state.llm_context.get_messages()
    except Exception as e:
        logger.error(f"Voice pipeline crashed: {e}")
        if state.llm_context is not None:
            messages = state.llm_context.get_messages()
    finally:
        await state.set_voice_active(False)
        state.pipeline_task = None
        api_task.cancel()
        snapshot_task.cancel()
        await asyncio.gather(api_task, snapshot_task, return_exceptions=True)
        await _close_http_clients()

    if messages:
        logger.info("Saving session to memory...")
        try:
            await close_session(messages)
            logger.info("Session saved.")
        except Exception as e:
            logger.warning(f"Could not save session: {e}")


def main():
    config = Config()
    setup_logging(config.log_level)
    logger.info("Starting Lhassa...")

    run_voice_pipeline = _load_voice_pipeline()
    if run_voice_pipeline is None:
        logger.info("For the headless roles use the CLI (`lhassa`) or docker/run_api.py.")
        sys.exit(1)

    try:
        asyncio.run(_run_all(config, run_voice_pipeline))
    except KeyboardInterrupt:
        print()  # newline after ^C
        logger.info("Lhassa shutting down.")
    except ConnectionError as e:
        logger.error(f"Connection error: {e}")
        logger.warning("Check your internet connection and API keys.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
