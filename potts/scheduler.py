"""Background scheduler — polls the DB every 30 s for due tasks and fires notifications."""

import asyncio
from loguru import logger

from potts.memory.store import load_pending_tasks, mark_task_done
from potts.voice.notifier import notify
from potts.state import state


async def run_scheduler():
    """
    Polls for scheduled tasks every 30 seconds.
    Cancelled cleanly when the main task cancels it on shutdown.
    """
    logger.info("Scheduler started.")
    try:
        while True:
            try:
                tasks = await load_pending_tasks()
            except Exception as e:
                logger.error(f"[scheduler] Error loading tasks: {e}")
                await asyncio.sleep(30)
                continue

            for task in tasks:
                try:
                    logger.info(f"[scheduler] Firing task '{task['name']}'")
                    await notify(task["message"], title="Potts — Reminder")
                    await mark_task_done(task["id"])
                    try:
                        await state.broadcast({
                            "type": "scheduled_task_fired",
                            "task": {**task, "status": "done"},
                        })
                    except Exception as e:
                        logger.warning(
                            f"[scheduler] Reminder '{task['name']}' fired but broadcast failed: {e}"
                        )
                except Exception as e:
                    logger.error(f"[scheduler] Error processing task '{task['name']}': {e}")

            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Scheduler stopped.")
        raise  # Let asyncio clean up properly
