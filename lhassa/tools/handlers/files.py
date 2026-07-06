"""File read / write tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.llm_service import FunctionCallParams

import os
from pathlib import Path

from loguru import logger

from lhassa.state import state
from lhassa.tools.guardrails import validate_write_path
from lhassa.tools.handlers._shared import _bump_tool

_FILE_CONTENT_MAX = int(os.getenv("LHASSA_FILE_CONTENT_MAX", "4000"))  # Max chars returned from read_file to voice pipeline


async def handle_read_file(params: FunctionCallParams):
    path = params.arguments.get("path", "")
    logger.info(f"[tool] read_file: {path!r}")
    print(f"\033[2m[📄 reading: {path}]\033[0m")
    _bump_tool()

    try:
        p = Path(path).expanduser()
        size = p.stat().st_size
        if size > 512 * 1024:  # 512 KB hard cap — avoid flooding the LLM context
            await params.result_callback(
                f"File is too large to read ({size // 1024} KB). "
                "Use shell with head/tail/grep to inspect specific parts."
            )
            return
        content = p.read_text(errors="replace")
        if len(content) > _FILE_CONTENT_MAX:
            content = content[:_FILE_CONTENT_MAX] + "\n... (truncated)"
        await params.result_callback(content)
    except FileNotFoundError:
        await params.result_callback(f"File not found: {path}")
    except Exception as e:
        await params.result_callback(f"Read error: {e}")


async def handle_write_file(params: FunctionCallParams):
    path = params.arguments.get("path", "")
    content = params.arguments.get("content", "")
    logger.info(f"[tool] write_file: {path!r}")
    print(f"\033[2m[✏️  writing: {path}]\033[0m")
    _bump_tool()

    try:
        p, validation_error = validate_write_path(path)
        if validation_error:
            await state.record_audit_event(
                action="write_file",
                target=path[:200],
                source="voice_tool",
                status="blocked",
                detail=validation_error,
            )
            await params.result_callback(validation_error)
            return
        assert p is not None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        await state.record_audit_event(
            action="write_file",
            target=str(p)[:200],
            source="voice_tool",
            status="ok",
            detail=f"bytes={len(content)}",
        )
        await params.result_callback(f"File written: {path}")
    except Exception as e:
        await state.record_audit_event(
            action="write_file",
            target=path[:200],
            source="voice_tool",
            status="error",
            detail=str(e)[:200],
        )
        await params.result_callback(f"Write error: {e}")
