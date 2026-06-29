"""
Lhassa' LLM-free "PC-state brain".

Deterministic background system checks that run on a timer and NEVER call any
LLM or cloud service. Pure stdlib + subprocess only. Results are written as
JSON snapshots and, for anything that needs attention, surfaced via
``notify-send`` in Lhassa' voice (see :func:`lhassa.persona.format_alert`).
"""

from lhassa.health.checks import (
    check_disk,
    check_failed_units,
    check_git_status,
    check_mem_pressure,
    run_all,
)

__all__ = [
    "check_disk",
    "check_failed_units",
    "check_git_status",
    "check_mem_pressure",
    "run_all",
]
