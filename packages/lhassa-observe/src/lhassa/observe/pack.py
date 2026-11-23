# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for the observers (``lhassa.plugins`` -> ``observe``).

Importing this module registers the pack and mounts ``lhassa observe``. The pack
owns no tables: its history is JSONL under ``Config.records_dir``.
"""

from __future__ import annotations

from lhassa.extensions import register_cli_app, register_pack

register_pack(
    "observe",
    "Observers: read-only system, sway, shell and file events written to the history",
    module=__name__,
)


def _cli_app():
    from lhassa.observe.surfaces.cli import observe_app

    return observe_app


register_cli_app("observe", _cli_app)
