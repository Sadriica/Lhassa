# SPDX-License-Identifier: AGPL-3.0-or-later
"""
A tiny, generic, dependency-free registry; the reusable foundation for every
swap point in Lhassa (channels, org departments, providers, harnesses,
verifiers, specialists, actions).

The owner's standing principle is that Lhassa must be **fully elastic and highly
modular**: every swap point sits behind an interface + a registry, and new
capabilities are **drop-in** (add a module, never edit core). This module is the
shared registry primitive those swap points adopt so the behaviour is uniform:

* one string key → one item (a class, factory, instance, or dataclass);
* register both as a **direct call** and as a **decorator**;
* **duplicate registration is an error** unless ``override=True``: so two
  plugins can never silently clobber each other;
* a clear, typed ``NotRegistered`` on a missing lookup.

It deliberately has **no dependencies** and never imports anything from Lhassa,
so it can be adopted by any layer without creating import cycles.

Example::

    actions = Registry[Callable]("action")

    @actions.register("sync_repo")
    def sync_repo(params): ...

    actions.register("ping", lambda p: "pong")     # direct call
    actions.get("ping")                            # -> the lambda
    "sync_repo" in actions                         # -> True
"""

from __future__ import annotations

from typing import Dict, Generic, Iterator, Mapping, Optional, TypeVar

T = TypeVar("T")

__all__ = ["Registry", "NotRegistered", "attr_registry"]


class NotRegistered(KeyError):
    """Raised by :meth:`Registry.get` when ``name`` is not registered.

    Subclasses :class:`KeyError` so existing ``except KeyError`` handlers keep
    working, while callers that want a clearer signal can catch this directly.
    """


class Registry(Generic[T]):
    """A small, defensive, name → item registry.

    Generic over the item type ``T`` (a class, factory, instance, dataclass…).
    Insertion order is preserved (plain ``dict`` semantics), so iteration and
    :meth:`names` reflect registration order; handy for "first registered wins"
    defaults elsewhere in the codebase.

    Parameters
    ----------
    kind:
        A short human label for the kind of thing stored (``"action"``,
        ``"channel"``, …). Only used to make error messages legible.
    """

    def __init__(self, kind: str = "item") -> None:
        self.kind = kind
        self._items: Dict[str, T] = {}

    # ── registration ──────────────────────────────────────────────────────────
    def register(
        self,
        name: str,
        item: Optional[T] = None,
        *,
        override: bool = False,
    ):
        """Register ``item`` under ``name``.

        Usable **two** ways:

        * **Direct call**: ``reg.register("x", item)`` stores ``item`` and
          returns it.
        * **Decorator**: ``@reg.register("x")`` (``item`` omitted) returns a
          decorator that registers and returns the decorated object unchanged,
          so the name in the module still points at the original callable/class.

        A duplicate ``name`` raises :class:`ValueError` unless ``override=True``,
        which replaces the previous item. ``name`` must be a non-empty string.
        """
        if not isinstance(name, str) or not name:
            raise ValueError(f"{self.kind} name must be a non-empty string, got {name!r}")

        def _store(value: T) -> T:
            if name in self._items and not override:
                raise ValueError(
                    f"{self.kind} {name!r} is already registered; "
                    f"pass override=True to replace it"
                )
            self._items[name] = value
            return value

        # Decorator form: @reg.register("x")
        if item is None:
            return _store

        # Direct-call form: reg.register("x", item)
        return _store(item)

    def unregister(self, name: str) -> None:
        """Remove ``name``. Raises :class:`NotRegistered` if it is absent."""
        if name not in self._items:
            raise NotRegistered(f"no {self.kind} registered as {name!r}")
        del self._items[name]

    # ── lookup ────────────────────────────────────────────────────────────────
    def get(self, name: str) -> T:
        """Return the item registered as ``name``.

        Raises :class:`NotRegistered` (a ``KeyError``) with a clear message,
        listing the known names, if nothing is registered under ``name``.
        """
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(sorted(self._items)) or "<none>"
            raise NotRegistered(
                f"no {self.kind} registered as {name!r}; known: {known}"
            ) from None

    def get_or_none(self, name: str) -> Optional[T]:
        """Return the item registered as ``name``, or ``None`` if absent."""
        return self._items.get(name)

    # ── introspection ─────────────────────────────────────────────────────────
    def names(self) -> list[str]:
        """All registered names, in registration order."""
        return list(self._items)

    def all(self) -> list[T]:
        """All registered items, in registration order."""
        return list(self._items.values())

    def items(self) -> Dict[str, T]:
        """A shallow ``{name: item}`` copy (safe to mutate by the caller)."""
        return dict(self._items)

    def as_mapping(self) -> Mapping[str, T]:
        """A **live, read-only** view of the backing store.

        Reflects later (un)registrations and supports ``in``/``[]``/``.get()``/
        ``len()``/iteration; ideal for exposing a legacy module-level ``dict``
        backed by this registry without letting callers mutate it directly.
        """
        from types import MappingProxyType

        return MappingProxyType(self._items)

    def clear(self) -> None:
        """Drop every registration (mainly for tests)."""
        self._items.clear()

    # ── dunder sugar ──────────────────────────────────────────────────────────
    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __getitem__(self, name: str) -> T:
        return self.get(name)

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Registry(kind={self.kind!r}, names={self.names()!r})"


def attr_registry(
    items: object = (),
    *,
    kind: str = "item",
    key: str = "name",
) -> "Registry":
    """Build a :class:`Registry` keyed by an **attribute** of each item.

    Several existing Lhassa registries are "dataclass keyed by a field"
    (e.g. ``Department`` keyed by ``.name``). This helper captures that pattern:
    each item is registered under ``getattr(item, key)``.

    ``items`` may be any iterable of items to seed the registry with. The
    resulting registry behaves like any other; register more later with
    ``reg.register(item.name, item)`` or via :func:`register_attr`-style calls.
    """
    reg: Registry = Registry(kind=kind)
    for item in items:
        name = getattr(item, key)
        reg.register(name, item)
    return reg
