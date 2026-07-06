"""Monitor tool handlers (watch process / file / command)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.llm_service import FunctionCallParams

from loguru import logger

from lhassa.tools.handlers._shared import _bump_tool


async def handle_watch_process(params: FunctionCallParams):
    pid = params.arguments.get("pid")
    name = params.arguments.get("name")
    label = params.arguments.get("label")
    logger.info(f"[tool] watch_process pid={pid} name={name!r}")
    _bump_tool()

    try:
        from lhassa.tools.monitor import watch_process
        result = await watch_process(pid=pid, name=name, label=label)
        await params.result_callback(result)
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        logger.error(f"watch_process error: {e}")
        await params.result_callback(f"Monitor error: {e}")


async def handle_watch_file(params: FunctionCallParams):
    path = params.arguments.get("path", "")
    pattern = params.arguments.get("pattern")
    label = params.arguments.get("label")
    logger.info(f"[tool] watch_file: {path!r} pattern={pattern!r}")
    _bump_tool()

    try:
        from lhassa.tools.monitor import watch_file
        result = await watch_file(path=path, pattern=pattern, label=label)
        await params.result_callback(result)
    except Exception as e:
        logger.error(f"watch_file error: {e}")
        await params.result_callback(f"Monitor error: {e}")


async def handle_watch_command(params: FunctionCallParams):
    command = params.arguments.get("command", "")
    interval = params.arguments.get("interval", 30)
    until_output_contains = params.arguments.get("until_output_contains")
    label = params.arguments.get("label")
    logger.info(f"[tool] watch_command: {command!r}")
    _bump_tool()

    try:
        from lhassa.tools.monitor import watch_command
        result = await watch_command(
            command=command,
            interval=interval,
            until_output_contains=until_output_contains,
            label=label,
        )
        await params.result_callback(result)
    except Exception as e:
        logger.error(f"watch_command error: {e}")
        await params.result_callback(f"Monitor error: {e}")
