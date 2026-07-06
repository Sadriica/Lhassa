"""Shell / privileged-command tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.llm_service import FunctionCallParams

import asyncio
import os
import subprocess

from loguru import logger

from lhassa.state import state
from lhassa.tools.guardrails import validate_shell_command
from lhassa.tools.handlers._shared import _bump_tool

_SHELL_OUTPUT_MAX = int(os.getenv("LHASSA_SHELL_OUTPUT_MAX", "2000"))  # Max chars returned from shell to voice pipeline


async def handle_shell(params: FunctionCallParams):
    command = params.arguments.get("command", "")
    logger.info(f"[tool] shell: {command!r}")
    print(f"\033[2m[⚡ running: {command}]\033[0m")
    _bump_tool()

    validation_error = validate_shell_command(command)
    if validation_error:
        await state.record_audit_event(
            action="shell",
            target=command[:200],
            source="voice_tool",
            status="blocked",
            detail=validation_error,
        )
        await params.result_callback(validation_error)
        return

    try:
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        if len(output) > _SHELL_OUTPUT_MAX:
            output = output[:_SHELL_OUTPUT_MAX] + "\n... (truncated)"
        await state.record_audit_event(
            action="shell",
            target=command[:200],
            source="voice_tool",
            status="ok" if result.returncode == 0 else "error",
            detail=f"exit_code={result.returncode}",
        )
        await params.result_callback(output)
    except subprocess.TimeoutExpired:
        await state.record_audit_event(
            action="shell",
            target=command[:200],
            source="voice_tool",
            status="error",
            detail="timeout",
        )
        await params.result_callback("Command timed out after 30 seconds and was cancelled.")
    except Exception as e:
        logger.error(f"shell error: {e}")
        await state.record_audit_event(
            action="shell",
            target=command[:200],
            source="voice_tool",
            status="error",
            detail=str(e)[:200],
        )
        await params.result_callback(f"Shell error: {e}")


async def handle_run_privileged(params: FunctionCallParams):
    command = params.arguments.get("command", "")
    logger.info(f"[tool] run_privileged: {command!r}")
    print(f"\033[2m[🔐 root: {command}]\033[0m")
    _bump_tool()

    try:
        from lhassa.tools.privileged import run_privileged
        result = await run_privileged(command, source="voice_tool")
        await params.result_callback(result)
    except Exception as e:
        logger.error(f"run_privileged error: {e}")
        await params.result_callback(f"Privileged command error: {e}")
