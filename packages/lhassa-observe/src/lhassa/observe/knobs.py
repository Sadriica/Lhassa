# SPDX-License-Identifier: AGPL-3.0-or-later
"""The observe pack's ``Config`` knobs (imported by the package itself)."""

from __future__ import annotations

from pathlib import Path

from lhassa.config import register_config_knob

# The user's observer configuration. Absent or empty means: observe nothing.
register_config_knob(
    "observe_config", "LHASSA_OBSERVE_CONFIG", str(Path.home() / ".lhassa" / "observe.json")
)
# Where the shell hook's FIFO and datagram socket live. Empty means the default
# (``$XDG_RUNTIME_DIR/lhassa-observe``, else ``/tmp/lhassa-observe-<uid>``).
register_config_knob("observe_runtime_dir", "LHASSA_OBSERVE_DIR", "")
