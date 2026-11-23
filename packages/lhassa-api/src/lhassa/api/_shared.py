# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared constants for the API shell.

Small values used by BOTH the integrator (``server.py``) and a domain router; kept
here so a router never has to import ``server`` (which would risk an import cycle).
"""

from __future__ import annotations

import os
from pathlib import Path

# The dashboard (React/Vite) lives at the checkout root, ``dashboard/``, outside
# this pack. ``LHASSA_DASHBOARD_DIST`` points at a built ``dist`` explicitly.
DASHBOARD_DIST_ENV = "LHASSA_DASHBOARD_DIST"


def resolve_dashboard_dist(
    start: Path | None = None, env: dict[str, str] | None = None, cwd: Path | None = None
) -> Path:
    """Where the built dashboard is (``dashboard/dist``).

    1. ``LHASSA_DASHBOARD_DIST`` when set (a deploy that builds the dashboard
       elsewhere);
    2. the nearest ancestor of this file that holds a ``dashboard/`` directory (an
       editable install from the checkout: ``packages/lhassa-api/src/lhassa/api``
       walks up to the repo root; the Docker image's ``/app`` too);
    3. ``./dashboard/dist`` under the current directory (a wheel install run from
       the checkout).

    The result may not exist; callers check (``/api/health`` reports it, the
    integrator serves a hint instead of the static mount).
    """
    environ = os.environ if env is None else env
    override = (environ.get(DASHBOARD_DIST_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        candidate = parent / "dashboard"
        if (candidate / "package.json").is_file() or (candidate / "dist").is_dir():
            return candidate / "dist"
    return (cwd or Path.cwd()) / "dashboard" / "dist"


# The built dashboard directory. Read by the runtime ``/api/health`` route (reports
# whether it exists) AND by the integrator's static-file mount.
DASHBOARD_DIST = resolve_dashboard_dist()
