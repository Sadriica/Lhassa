# SPDX-License-Identifier: AGPL-3.0-or-later
"""CV chat commands: /cvnewbase /cvbase (registered by :mod:`lhassa.cv.pack`).

Plain string-returning handlers for the notify ``COMMANDS`` registry. The ``/cv``
command itself replies with a file, so it is an inbound hook
(:mod:`lhassa.cv.surfaces.chat`), not a registry command.
"""

from __future__ import annotations

from loguru import logger


async def cmd_cvnewbase(args: str, *, config=None) -> str:
    """Arm a base-CV replacement; the reply text for ``/cvnewbase``.

    The arming side-effect (marking this chat so its next upload replaces the base CV)
    lives in the cv inbound hook, which has the chat id; this handler owns only the
    instruction text, so ``/cvnewbase`` appears in ``/help`` and the command menu.
    """
    return (
        "Send your new base CV as the next file (a .tex is best; a PDF stores "
        "extracted text)."
    )


async def cmd_cvbase(args: str, *, config=None) -> str:
    """Show the base CV on file (``cv_largo.tex``); LLM-free, never raises.

    Reports whether a base CV exists, its size and first line, and how to update it
    (arm ``/cvnewbase``, then send a document). Reads the file only; no model call.
    """
    try:
        from lhassa import cv

        info = cv.base_info()
        if not info.get("exists"):
            return (
                "No base CV on file yet. Use /cvnewbase, then send your CV as a "
                "document to save it as the base; /cv <job posting> will tailor it."
            )
        first = info.get("first_line") or ""
        first_line = f"\nFirst line: {first}" if first else ""
        return (
            f"Base CV on file ({info.get('chars', 0)} chars).{first_line}\n"
            f"Path: {info.get('path')}\n"
            "Use /cvnewbase and then send a document to replace it."
        )
    except Exception as exc:
        logger.warning(f"commands: /cvbase failed: {exc}")
        return "I couldn't read the base CV info right now."


#: name -> handler; :mod:`lhassa.cv.pack` registers each one.
HANDLERS = {
    "cvnewbase": cmd_cvnewbase,
    "cvbase": cmd_cvbase,
}
