# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa tui``: a Textual terminal UI over the brain.

Reads DIRECTLY off ``lhassa.core.db`` (the same seam ``cli/commands.py`` uses),
never the HTTP API, so the TUI works with the server down. Actions that mutate
state (approve/deny) go through the SAME security seam the CLI/API use
(``lhassa.security.approval.resolve``); never bypassed.

Import this subpackage lazily (inside the ``tui`` command) so importing the
top-level CLI never pays Textual's import cost.
"""

from __future__ import annotations
