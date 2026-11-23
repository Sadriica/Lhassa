# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only observers that write to the history (``lhassa.record``).

Four observers (system resources, the sway session, shell commands, declared
folders) and a coordinator that runs the enabled ones. Nothing here acts on the
machine: every observer reads and every event passes the never-keep policy
before it is written. Nothing is observed unless it is switched on.
"""

from lhassa.observe import knobs  # noqa: F401  (declares the Config knobs)
