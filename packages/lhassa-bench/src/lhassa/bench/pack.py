# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for LhassaBench (``lhassa.plugins`` -> ``bench``).

Importing this module registers the pack and mounts ``lhassa bench``. The
``bench_runs`` table predates the pack and stays in core's migrations.
"""

from __future__ import annotations

from lhassa.extensions import register_cli_app, register_pack

register_pack(
    "bench",
    "LhassaBench: fixtures x subjects, scored by the objective verifiers",
    module=__name__,
)


def _cli_app():
    from lhassa.bench.surfaces.cli import bench_app

    return bench_app


register_cli_app("bench", _cli_app)
