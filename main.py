import asyncio
import sys
from pathlib import Path
from loguru import logger

from potts.config import Config
from potts.voice.pipeline import run as run_voice_pipeline
from potts.memory.session import close_session
from potts.memory.store import init_db
from potts.scheduler import run_scheduler
from potts.api.server import run_api_server
from potts.state import state

LOG_DIR = Path.home() / ".potts"


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
        LOG_DIR / "potts.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


async def _run_all(config: Config) -> list:
    """
    Run voice pipeline as the primary task, with API server and scheduler as
    background services that are cancelled cleanly when the voice pipeline exits.
    """
    await init_db()

    api_task = asyncio.create_task(
        run_api_server(host=config.api_host, port=config.api_port),
        name="api-server",
    )
    scheduler_task = asyncio.create_task(
        run_scheduler(),
        name="scheduler",
    )

    messages: list = []
    state.is_voice_active = True
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
        state.is_voice_active = False
        state.pipeline_task = None
        api_task.cancel()
        scheduler_task.cancel()
        await asyncio.gather(api_task, scheduler_task, return_exceptions=True)

    return messages


def main():
    config = Config()
    setup_logging(config.log_level)
    logger.info("Starting Potts...")

    messages: list = []

    try:
        messages = asyncio.run(_run_all(config))
    except KeyboardInterrupt:
        print()  # newline after ^C
        logger.info("Potts shutting down.")
        # On hard interrupt, try one more time to read messages from state
        if not messages and state.llm_context is not None:
            messages = state.llm_context.get_messages()
    except ConnectionError as e:
        logger.error(f"Connection error: {e}")
        logger.warning("Check your internet connection and API keys.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

    if messages:
        logger.info("Saving session to memory...")
        try:
            asyncio.run(close_session(config.groq_api_key, config.model, messages))
            logger.info("Session saved.")
        except Exception as e:
            logger.warning(f"Could not save session: {e}")


if __name__ == "__main__":
    main()
