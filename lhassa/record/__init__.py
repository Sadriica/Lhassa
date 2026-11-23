# SPDX-License-Identifier: AGPL-3.0-or-later
"""The history contract: an append-only event log plus the never-keep policy.

Leaf infrastructure: sources (the harness recorder today, observers later) write
through :class:`EventLog`; readers use :func:`read_log`. Nothing here knows a
shell, a model or a harness.
"""

from lhassa.record.log import SCHEMA, EventLog, Sink, log_path, read_log
from lhassa.record.never_keep import WITHHELD, first_withheld, withheld_rule, withheld_rule_in_text

__all__ = [
    "SCHEMA", "WITHHELD", "EventLog", "Sink", "first_withheld",
    "log_path", "read_log", "withheld_rule", "withheld_rule_in_text",
]
