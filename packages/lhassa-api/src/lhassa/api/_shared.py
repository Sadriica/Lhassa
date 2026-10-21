# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared constants for the API shell.

Small values used by BOTH the integrator (``server.py``) and a domain router; kept
here so a router never has to import ``server`` (which would risk an import cycle).
"""

from pathlib import Path

# The built dashboard directory (dashboard/dist). Read by the runtime ``/api/health``
# route (reports whether it exists) AND by the integrator's static-file mount.
DASHBOARD_DIST = Path(__file__).parent.parent.parent / "dashboard" / "dist"
