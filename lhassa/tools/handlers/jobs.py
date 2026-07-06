"""Job/internship application tracking tool handler (postulaciones)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.llm_service import FunctionCallParams

from loguru import logger

from lhassa.tools.handlers._shared import _bump_tool


async def handle_track_postulaciones(params: FunctionCallParams):
    op = params.arguments.get("op", "list")
    logger.info(f"[tool] track_postulaciones op={op!r}")
    print(f"\033[2m[📋 postulaciones: {op}]\033[0m")
    _bump_tool()

    try:
        from lhassa.core.actions import tracking_postulaciones
        summary = await tracking_postulaciones(dict(params.arguments or {}))
        await params.result_callback(summary)
    except Exception as e:
        logger.error(f"track_postulaciones error: {e}")
        await params.result_callback(f"track_postulaciones error: {e}")
