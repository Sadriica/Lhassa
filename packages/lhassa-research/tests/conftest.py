"""Pack tests share core's hermetic fixtures (env pins, throwaway brain DB).

``tests/conftest.py`` only reaches files under ``tests/``; importing it here
applies its module-level isolation (no live notify channels, no runtime
overrides, the sync ASGI TestClient) and re-exports its fixtures, including
the autouse ones that keep every test off ``~/.lhassa/brain.db``.
"""

from tests.conftest import (  # noqa: F401
    _isolate_brain_db,
    _reset_seen_telegram_updates,
    clean_env,
    tmp_db,
)

import lhassa.research.pack  # noqa: E402,F401  (register the pack deterministically)
