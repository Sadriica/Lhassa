# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Drop-in plugin discovery — capabilities self-register WITHOUT editing core.

The elastic/modular principle (every swap point behind a registry, capabilities
**drop-in**) needs a loader: something that *imports* third-party / local plugin
modules so their top-level ``@some_registry.register(...)`` calls actually run.
Importing the module is the whole mechanism — there is no plugin API to call.

Two discovery sources, both optional and both fully defensive:

1. **A directory** (``config.plugins_dir``) — every ``*.py`` file and every
   package (a sub-directory with ``__init__.py``) directly inside it is imported.
2. **Entry points** in the group ``"lhassa.plugins"`` — any installed
   distribution can advertise a plugin module here; we import each one.

Contract:

* :func:`load_plugins` **never raises**. A broken or missing plugin is logged at
  warning level and skipped — a bad plugin must never crash startup.
* It is **idempotent**: already-loaded plugins are tracked and never re-imported,
  so calling it twice is safe and the second call is a no-op for those.
* It returns the list of plugin names successfully loaded **this call**.

Nothing here is auto-invoked at import time — wiring :func:`load_plugins` into
startup is a deliberate, separate step. Importing this module has no side effects.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from loguru import logger

__all__ = ["load_plugins", "ENTRY_POINT_GROUP"]

# Installed distributions advertise plugin modules under this entry-point group.
ENTRY_POINT_GROUP = "lhassa.plugins"

# Names of plugins already imported in this process — makes load_plugins idempotent
# across repeated calls (each unique source name is imported at most once).
_LOADED: set[str] = set()


def load_plugins(config: Optional[Any] = None) -> list[str]:
    """Discover and import all available plugins, returning the names loaded now.

    Imports (a) every module/package under ``config.plugins_dir`` if that
    directory exists, and (b) every entry point in the ``"lhassa.plugins"`` group.
    Importing a plugin is what lets it run its registry ``register`` calls.

    Never raises and is idempotent: a plugin already loaded in this process is
    skipped, and any failure to import a single plugin is logged and skipped so
    one bad plugin can never break the others or crash startup.

    Parameters
    ----------
    config:
        Anything exposing a ``plugins_dir`` attribute (Lhassa's ``Config`` or an
        instance). If ``None``, the directory source is read from ``Config``.
    """
    loaded: list[str] = []
    loaded.extend(_load_from_dir(config))
    loaded.extend(_load_from_entry_points())
    return loaded


# ── directory source ──────────────────────────────────────────────────────────
def _resolve_plugins_dir(config: Optional[Any]) -> Optional[Path]:
    """Best-effort resolve the plugins directory; never raises."""
    try:
        if config is None:
            from lhassa.config import Config

            config = Config
        raw = getattr(config, "plugins_dir", None)
        if not raw:
            return None
        return Path(raw).expanduser()
    except Exception as exc:  # defensive: config access must never crash discovery
        logger.warning(f"plugins: could not resolve plugins_dir: {exc}")
        return None


def _load_from_dir(config: Optional[Any]) -> list[str]:
    """Import every top-level ``*.py`` module and package under the plugins dir."""
    directory = _resolve_plugins_dir(config)
    if directory is None:
        return []
    try:
        if not directory.is_dir():
            return []
    except OSError as exc:  # e.g. permission error stat-ing the path
        logger.warning(f"plugins: cannot access plugins_dir {directory}: {exc}")
        return []

    loaded: list[str] = []
    for path in _candidate_paths(directory):
        name = _plugin_name(path)
        if name in _LOADED:
            continue
        if _import_path(name, path):
            _LOADED.add(name)
            loaded.append(name)
    return loaded


def _candidate_paths(directory: Path) -> list[Path]:
    """Sorted, de-duplicated plugin entry paths directly under ``directory``.

    A plugin is either ``<dir>/<name>.py`` (a module) or ``<dir>/<name>/`` with an
    ``__init__.py`` (a package). Dunder/private names (``_*``) are skipped.
    """
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        logger.warning(f"plugins: cannot list {directory}: {exc}")
        return []

    paths: list[Path] = []
    for entry in entries:
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir():
            if (entry / "__init__.py").is_file():
                paths.append(entry / "__init__.py")
        elif entry.suffix == ".py":
            paths.append(entry)
    return paths


def _plugin_name(path: Path) -> str:
    """A stable, namespaced module name for a directory-sourced plugin."""
    stem = path.parent.name if path.name == "__init__.py" else path.stem
    return f"lhassa_plugin_{stem}"


def _import_path(name: str, path: Path) -> bool:
    """Import the file at ``path`` under module ``name``. Returns success; never raises."""
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            logger.warning(f"plugins: no import spec for {path}")
            return False
        module = importlib.util.module_from_spec(spec)
        # Register before exec so a plugin that imports itself by name resolves.
        sys.modules[name] = module
        spec.loader.exec_module(module)
        logger.info(f"plugins: loaded {name} from {path}")
        return True
    except Exception as exc:  # a broken plugin is skipped, never fatal
        sys.modules.pop(name, None)
        logger.warning(f"plugins: failed to load {path}: {exc}")
        return False


# ── entry-point source ────────────────────────────────────────────────────────
def _load_from_entry_points() -> list[str]:
    """Import every module advertised under the ``lhassa.plugins`` entry-point group."""
    loaded: list[str] = []
    for ep in _iter_entry_points():
        name = f"ep:{getattr(ep, 'name', '?')}"
        if name in _LOADED:
            continue
        if _load_one_entry_point(ep):
            _LOADED.add(name)
            loaded.append(name)
    return loaded


def _iter_entry_points() -> Iterable[Any]:
    """Yield entry points in our group, tolerating old and new metadata APIs.

    Python ≥3.10 ``entry_points(group=...)`` vs the older dict-style ``.get(group)``.
    Any failure returns nothing (the directory source still works).
    """
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present on 3.8+
        return []

    try:
        eps = entry_points()
    except Exception as exc:
        logger.warning(f"plugins: entry_points() failed: {exc}")
        return []

    # New API (3.10+): selectable by group.
    try:
        select = getattr(eps, "select", None)
        if callable(select):
            return list(select(group=ENTRY_POINT_GROUP))
    except Exception as exc:
        logger.warning(f"plugins: entry_points(group=) failed: {exc}")

    # Old API (3.8/3.9): a mapping of group -> [EntryPoint].
    try:
        if hasattr(eps, "get"):
            return list(eps.get(ENTRY_POINT_GROUP, []))  # type: ignore[union-attr]
    except Exception as exc:
        logger.warning(f"plugins: legacy entry-point lookup failed: {exc}")

    return []


def _load_one_entry_point(ep: Any) -> bool:
    """Import the module behind an entry point. Returns success; never raises."""
    try:
        target = getattr(ep, "value", None) or getattr(ep, "module", None)
        # ``value`` is like "pkg.mod" or "pkg.mod:obj"; we only need the module.
        module_name = str(target).split(":", 1)[0].strip() if target else ""
        if not module_name:
            # Fall back to EntryPoint.load() which imports + resolves the object.
            ep.load()
        else:
            importlib.import_module(module_name)
        logger.info(f"plugins: loaded entry point {getattr(ep, 'name', '?')}")
        return True
    except Exception as exc:  # a broken entry point is skipped, never fatal
        logger.warning(
            f"plugins: failed to load entry point {getattr(ep, 'name', '?')}: {exc}"
        )
        return False
