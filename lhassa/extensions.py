# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Extension points for capability packs; leaf infra, imports only ``registry``.

A **pack** is a separately installable distribution (``packages/lhassa-<x>``)
that contributes one capability end to end: its own modules under the shared
``lhassa.<x>`` namespace, plus whatever it plugs into the surfaces. A pack is
discovered as an ordinary plugin (``lhassa.plugins`` entry point, see
``plugins.py``); importing its ``pack`` module runs the ``register_*`` calls
below. Core never imports a pack.

The registries that already existed keep doing their job (actions, commands,
channels, seeds, watchlist senses, providers). This module adds the ones a pack
needs to reach the surfaces that used to be hard-wired:

* :data:`PACKS` : a manifest per pack (name, summary), for ``lhassa capabilities``.
* :data:`API_ROUTERS` : name -> zero-arg factory returning a FastAPI ``APIRouter``.
* :data:`CLI_APPS` : name -> zero-arg factory returning a ``typer.Typer``; the
  name is the subcommand (``lhassa <name> ...``).
* :data:`CLI_COMMANDS` : name -> zero-arg factory returning a plain function;
  mounted as a single top-level command (``lhassa <name>``, no subcommands).
* :data:`PACK_MIGRATIONS` : pack name -> ordered ``Migration`` tuple, tracked
  per pack in the ``pack_schema`` table (independent of the core
  ``PRAGMA user_version`` track).

Factories are lazy on purpose: this module must stay importable without
fastapi or typer, and a pack's surface module should only be imported by the
process that serves that surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from lhassa.registry import Registry

__all__ = [
    "PackInfo",
    "PACKS",
    "API_ROUTERS",
    "CLI_APPS",
    "CLI_COMMANDS",
    "PACK_MIGRATIONS",
    "register_pack",
    "register_api_router",
    "register_cli_app",
    "register_cli_command",
    "register_pack_migrations",
]


@dataclass(frozen=True)
class PackInfo:
    """What a pack says about itself."""

    name: str
    summary: str
    module: str = ""


PACKS: Registry[PackInfo] = Registry("pack")
API_ROUTERS: Registry[Callable[[], Any]] = Registry("api router")
CLI_APPS: Registry[Callable[[], Any]] = Registry("cli app")
CLI_COMMANDS: Registry[Callable[[], Any]] = Registry("cli command")
PACK_MIGRATIONS: Registry[Sequence[Any]] = Registry("pack migrations")


def register_pack(name: str, summary: str, module: str = "") -> PackInfo:
    """Record a pack's manifest. Re-registering the same name is a no-op."""
    existing = PACKS.get_or_none(name)
    if existing is not None:
        return existing
    info = PackInfo(name=name, summary=summary, module=module)
    PACKS.register(name, info)
    return info


def _register_once(registry: Registry, name: str, value: Any) -> Any:
    # Packs are imported once per process by load_plugins, but tests and
    # reloads may import twice; the first registration wins.
    if name not in registry:
        registry.register(name, value)
    return value


def register_api_router(name: str, factory: Callable[[], Any]) -> Callable[[], Any]:
    """Mount ``factory()`` on the API app (``api/server.py``)."""
    return _register_once(API_ROUTERS, name, factory)


def register_cli_app(name: str, factory: Callable[[], Any]) -> Callable[[], Any]:
    """Mount ``factory()`` as ``lhassa <name>`` (``cli/app.py``)."""
    return _register_once(CLI_APPS, name, factory)


def register_cli_command(name: str, factory: Callable[[], Any]) -> Callable[[], Any]:
    """Mount ``factory()`` (a typer-compatible function) as ``lhassa <name>`` (``cli/app.py``)."""
    return _register_once(CLI_COMMANDS, name, factory)


def register_pack_migrations(pack: str, migrations: Sequence[Any]) -> Sequence[Any]:
    """Declare a pack's own schema migrations, applied by ``core.db.init_db``."""
    return _register_once(PACK_MIGRATIONS, pack, tuple(migrations))
