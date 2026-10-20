"""Calendar pack test setup: reuse core's shared fixtures, then load the pack.

Core's ``tests/conftest.py`` only covers ``tests/``; its environment isolation
(no real calendars.json, no real feed url, log-only notifications) and its
autouse fixtures (a throwaway brain DB per test, fresh Telegram poller state)
must bind the pack's tests too, so the module is loaded here by path and its
fixtures re-exported. Importing ``lhassa.calendar.pack`` afterwards registers
the pack exactly as ``load_plugins`` does in a real process.
"""

import importlib.util
from pathlib import Path

_ROOT_CONFTEST = Path(__file__).resolve().parents[3] / "tests" / "conftest.py"
_spec = importlib.util.spec_from_file_location("lhassa_core_conftest", _ROOT_CONFTEST)
_core_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core_conftest)
globals().update(
    {k: v for k, v in vars(_core_conftest).items() if not k.startswith("__")}
)

import lhassa.calendar.pack  # noqa: E402,F401
