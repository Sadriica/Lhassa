"""In-container runner: dashboard + REST API + scheduler, without the voice pipeline.

main.py runs the voice pipeline as its primary task and the API server and
scheduler as background services. The voice pipeline needs host audio devices and
the Deepgram/Cartesia keys, so it is intentionally NOT started inside the
container (see doc/deployment.md). This runner starts exactly the non-voice
services that main.py would, so the dashboard, REST API and the scheduler all run
in the container with no secrets required.
"""

import asyncio

from loguru import logger

from lhassa.config import Config


async def _main() -> None:
    config = Config()

    # Initialize both data stores (the conversational memory DB and the brain
    # "grande"); both resolve under HOME=/data → the mounted volume.
    from lhassa.memory.store import init_db as init_memory_db
    from lhassa.core.db import init_db as init_core_db

    await init_memory_db()
    try:
        await init_core_db()
    except Exception as exc:  # never block startup on the spine DB init
        logger.warning(f"core DB init failed: {exc}")

    from lhassa.state import state

    try:
        state.load_audit_events()
    except Exception as exc:
        logger.warning(f"audit events load failed: {exc}")

    from lhassa.api.server import run_api_server
    from lhassa.scheduler import run_scheduler

    scheduler_task = asyncio.create_task(run_scheduler(), name="scheduler")
    try:
        await run_api_server(host=config.api_host, port=config.api_port)
    finally:
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
