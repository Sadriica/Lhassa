# SPDX-License-Identifier: AGPL-3.0-or-later
"""DEPRECATED entrypoint: ``python -m lhassa.notify.telegram_inbound``.

The Telegram poller moved to the telegram pack (``lhassa-telegram``) and runs
as ``python -m lhassa.telegram.poller``; the chat engine it drives is
:mod:`lhassa.chat`. This alias exists for ONE release so an already-installed
``lhassa-telegram`` unit keeps running until it is reinstalled
(``make install-telegram`` copies the new unit); delete it after that.

Core never imports a pack, so this module does not import the poller: it loads
the plugins (which registers the pack when it is installed), checks the pack
registry, and hands the process to the pack's module by NAME through
:func:`runpy.run_module`. Without the pack it logs how to install it and exits
non-zero. Importing this module does nothing.
"""

from __future__ import annotations

import sys

POLLER_MODULE = "lhassa.telegram.poller"


def main() -> int:
    from loguru import logger

    from lhassa import actionpack
    from lhassa.extensions import PACKS

    logger.warning(
        "python -m lhassa.notify.telegram_inbound is deprecated; the poller is "
        f"now `python -m {POLLER_MODULE}` (reinstall the unit: make install-telegram)."
    )
    actionpack.register_all()  # loads the plugins; the telegram pack registers itself
    if "telegram" not in PACKS:
        logger.error(
            "The Telegram poller ships in the lhassa-telegram pack, which is not "
            "installed. Install it with `uv sync --package lhassa-telegram` "
            "(or `uv sync --all-packages`)."
        )
        return 1

    import runpy

    runpy.run_module(POLLER_MODULE, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
