# SPDX-License-Identifier: AGPL-3.0-or-later
"""SEEDS; reproducible BOOTSTRAP units, backed by the keystone :class:`Registry`.

A *seed* recreates a whole working structure on a fresh machine from code, with no
hand-rebuilding: it is the reproducible counterpart to the config the owner would
otherwise assemble by clicking around. The first seed is the ``cv`` pipeline (the
interns department, its worker brains, the monitor role, and the canonical
"Impossible-to-reject CV" work-flow); future seeds drop in exactly the same way.

Elastic + modular, like every other swap point in Lhassa (``PROVIDERS``,
``CHANNELS``, ``COMMANDS``, ``VERIFIERS`` …): a :class:`Seed` sits behind the generic
:class:`lhassa.registry.Registry`, and a new seed self-registers with
``@register_seed("name")`` at import; **no core edit**. Built-in seeds register when
this package is imported; a drop-in seed in a ``plugins_dir`` module (or a
``lhassa.plugins`` entry point) registers the same way through
:func:`lhassa.plugins.load_plugins`.

A :class:`Seed` is a tiny unit; ``name``, ``description`` and an
``apply(config) -> dict`` that MUST be idempotent and non-destructive (re-applying is
safe; it never clobbers an existing real base CV or existing brains). ``apply`` never
raises destructively; a partial failure is reported in its returned summary, not
fatal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from lhassa.registry import Registry

if TYPE_CHECKING:
    from lhassa.config import Config

__all__ = [
    "Seed",
    "SEEDS",
    "register_seed",
    "list_seeds",
    "apply_seed",
    "apply_all",
]

#: An ``apply`` takes an optional :class:`~lhassa.config.Config` and returns a
#: serialisable summary of what it created/kept.
ApplyFn = Callable[["Optional[Config]"], dict]


@dataclass(frozen=True)
class Seed:
    """One reproducible bootstrap unit.

    ``apply(config)`` performs the (idempotent, non-destructive) bootstrap and
    returns a summary dict. Immutable so a registered seed can't be mutated in place.
    """

    name: str
    description: str
    apply: ApplyFn


#: The global seed registry; ``name`` → :class:`Seed`. Adopts the keystone
#: :class:`Registry`, so it behaves like every other Lhassa swap point (duplicate
#: protection, ordered iteration, ``in``/``get``/``names``).
SEEDS: "Registry[Seed]" = Registry("seed")


def register_seed(name: str, description: str = "", *, override: bool = False):
    """Register an ``apply`` function as the seed ``name``: decorator form.

    Usage::

        @register_seed("cv", "Recreate the CV pipeline")
        def apply(config=None) -> dict:
            ...

    Wraps the decorated function in a :class:`Seed` and stores it in :data:`SEEDS`
    (a duplicate name raises unless ``override=True``). Returns the function
    unchanged, so the module name still points at the original callable.
    """

    def _deco(fn: ApplyFn) -> ApplyFn:
        SEEDS.register(
            name,
            Seed(name=name, description=str(description or ""), apply=fn),
            override=override,
        )
        return fn

    return _deco


def list_seeds() -> list[dict]:
    """Every registered seed as ``{name, description}`` (registration order)."""
    return [{"name": s.name, "description": s.description} for s in SEEDS.all()]


def apply_seed(name: str, config: "Optional[Config]" = None) -> dict:
    """Apply one registered seed by name and return its summary.

    Raises :class:`lhassa.registry.NotRegistered` (a ``KeyError``) if ``name`` is
    unknown; the seed's own ``apply`` never raises destructively.
    """
    return SEEDS.get(name).apply(config)


def apply_all(config: "Optional[Config]" = None) -> dict:
    """Apply EVERY registered seed (registration order) and collect the summaries.

    A seed that raises is captured as an ``{"error": ...}`` entry rather than
    aborting the rest; bootstrapping stays best-effort. Returns
    ``{"applied": [names], "results": {name: summary}}``.
    """
    results: dict[str, dict] = {}
    for seed in SEEDS.all():
        try:
            results[seed.name] = seed.apply(config)
        except Exception as exc:  # never let one seed abort the whole bootstrap
            results[seed.name] = {"error": f"{type(exc).__name__}: {exc}"}
    return {"applied": list(results), "results": results}


# ── built-in seeds register on import (drop-ins do the same via plugins.py) ────────
# Imported last so ``register_seed`` above already exists when the module runs its
# ``@register_seed`` decorators. New built-in seeds add a line here; external seeds
# need no edit at all (they self-register when their plugin module is imported).
from lhassa.seeds import cv as _cv  # noqa: E402,F401  (self-registers "cv")
from lhassa.seeds import digest as _digest  # noqa: E402,F401  (self-registers "digest")
from lhassa.seeds import maintenance as _maintenance  # noqa: E402,F401  (self-registers "maintenance")
from lhassa.seeds import personal as _personal  # noqa: E402,F401  (self-registers "personal")
from lhassa.seeds import temple as _temple  # noqa: E402,F401  (self-registers "temple")
