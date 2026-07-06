"""In-container runner: dashboard + REST API, without the voice pipeline.

main.py runs the voice pipeline as its primary task and the API server as a
background service. The voice pipeline needs host audio devices and the
Deepgram/Cartesia keys, so it is intentionally NOT started inside the
container (see doc/deployment.md). This runner starts exactly the non-voice
service that main.py would, so the dashboard and REST API run in the
container with no secrets required.

Reminders no longer need a separate poller: ``schedule_reminder`` mints a
``send_reminder`` task in the ``tasks`` table and the heartbeat's watchlist
sweeps it when due (see ``doc/scheduler.md``). The legacy ``scheduled_tasks``
migration below is one-shot and idempotent.
"""

import asyncio

from loguru import logger

from lhassa.config import Config


async def _main() -> None:
    config = Config()

    from lhassa import actionpack
    actionpack.register_all()

    # Initialize both data stores (the conversational memory DB and the brain
    # "grande"); both resolve under HOME=/data → the mounted volume.
    from lhassa.memory.store import init_db as init_memory_db
    from lhassa.core.db import init_db as init_core_db

    await init_memory_db()
    try:
        await init_core_db()
    except Exception as exc:  # never block startup on the spine DB init
        logger.warning(f"core DB init failed: {exc}")

    # One-shot, idempotent: copy any still-pending legacy reminders
    # (memory/store.py's scheduled_tasks) into the tasks table as
    # send_reminder tasks. Never blocks startup. See doc/scheduler.md.
    try:
        from lhassa.tools.handlers.schedule import migrate_legacy_reminders

        await migrate_legacy_reminders(config.instance_id)
    except Exception as exc:
        logger.warning(f"Legacy reminder migration failed: {exc}")

    from lhassa.state import state

    try:
        state.load_audit_events()
    except Exception as exc:
        logger.warning(f"audit events load failed: {exc}")

    from lhassa.api.server import run_api_server

    await run_api_server(host=config.api_host, port=config.api_port)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
