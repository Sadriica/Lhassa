"""cv pack test fixtures: reuse the core suite's isolation, then load the pack.

The core ``tests/conftest.py`` only covers its own directory, and it does real work
at import time (it scrubs the owner's env before any ``lhassa`` import) plus three
autouse fixtures (throwaway brain DB, fresh Telegram poller state). Importing it
here gives the pack's tests the same isolation. The pack is imported explicitly so
its registrations exist even when nothing ran ``plugins.load_plugins()``.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.conftest import (  # noqa: E402,F401
    _isolate_brain_db,
    _reset_seen_telegram_updates,
    clean_env,
    tmp_db,
)

import lhassa.cv.pack  # noqa: E402,F401
