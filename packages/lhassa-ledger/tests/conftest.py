# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack tests share core's hermetic fixtures (env pins, throwaway brain DB).

``tests/conftest.py`` only reaches files under ``tests/``; importing it here
applies its module-level isolation and re-exports its fixtures, including the
autouse ones that keep every test off ``~/.lhassa/brain.db``.
"""

from tests.conftest import (  # noqa: F401
    _isolate_brain_db,
    _reset_seen_telegram_updates,
    clean_env,
    tmp_db,
)

import pytest


@pytest.fixture(autouse=True)
def _ledger_env(monkeypatch):
    """No test reads the owner's real ledger, price table or never-keep file."""
    for name in ("AGENT_LEDGER_NEVER_KEEP", "AGENT_LEDGER_DB", "AGENT_LEDGER_PRICES"):
        monkeypatch.delenv(name, raising=False)
