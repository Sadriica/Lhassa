# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for the dashboard + REST API (``lhassa.plugins`` -> ``api``).

Importing this module registers the pack and the one surface it adds to core:

  * the ``lhassa serve`` command (``extensions.register_cli_command``), which boots
    :func:`lhassa.api.server.run_api_server`.

The server itself (``lhassa.api.server``) mounts every core domain router plus the
routers other packs register through ``extensions.register_api_router``; it is
imported only by the process that serves it (``lhassa serve``, ``docker/run_api.py``,
``main.py``), never here. Registration is idempotent.
"""

from __future__ import annotations

from lhassa.extensions import register_cli_command, register_pack

register_pack(
    "api",
    "Dashboard + REST/WebSocket API (FastAPI): lhassa serve, /api/*, the React dashboard",
    module=__name__,
)


def _serve_command():
    from lhassa.api.cli import serve_cmd

    return serve_cmd


register_cli_command("serve", _serve_command)
