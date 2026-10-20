# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for the CV / job-applications capability (``lhassa.plugins`` -> ``cv``).

Importing this module registers the pack and everything it plugs in:

  * the ``compile_cv`` action (``core.actions.register_action``);
  * the ``/cvnewbase`` and ``/cvbase`` chat commands plus their ``/help`` lines;
  * the Telegram inbound hook: ``/cv``, the held-CV follow-up, CV uploads;
  * the work-flow entries: the ``cv`` context provider and the ``tex`` output;
  * the ``cv`` seed (the "Impossible-to-reject CV" pipeline);
  * the ``/api/postulaciones`` router for the dashboard.

The shell-touching registrations live in :mod:`lhassa.cv.surfaces.wiring`; surface
modules are imported lazily, inside each wrapper or factory, so a process that never
serves a surface never imports it. The ``postulaciones`` table, its
migrations, the ``core.db`` helpers and the ``tracking_postulaciones`` typed task
predate the pack and stay in core. Registration is idempotent.
"""

from __future__ import annotations

from lhassa.extensions import register_api_router, register_pack

register_pack(
    "cv",
    "CV work-flow: tailor the base CV to a job posting (/cv), compile_cv, applications tracker API",
    module=__name__,
)


# ── API router ────────────────────────────────────────────────────────────────
def _api_router():
    from lhassa.cv.surfaces.api import router

    return router


register_api_router("postulaciones", _api_router)


def _register_surfaces() -> None:
    # The registrations that touch shells (notify, seeds, flows) live in the
    # pack's surface slice; their surface modules still import lazily.
    from lhassa.cv.surfaces import wiring

    wiring.register()


_register_surfaces()
