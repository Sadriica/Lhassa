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
                for task in tasks:
                    logger.info(f"[scheduler] Firing task '{task['name']}'")
                    await mark_task_done(task["id"])
                    await notify(task["message"], title="Potts — Reminder")
                    await state.broadcast({
                        "type": "scheduled_task_fired",
                        "task": task,
                    })
            except Exception as e:
                logger.error(f"[scheduler] Error processing tasks: {e}")

            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Scheduler stopped.")
        raise  # Let asyncio clean up properly
