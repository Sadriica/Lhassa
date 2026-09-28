"""Tests for the generic swap-point registry (``lhassa/registry.py``)."""

from dataclasses import dataclass

import pytest

from lhassa.registry import NotRegistered, Registry, attr_registry


def test_register_as_direct_call_returns_item():
    reg: Registry = Registry(kind="thing")
    obj = object()
    returned = reg.register("a", obj)
    assert returned is obj
    assert reg.get("a") is obj


def test_register_as_decorator_returns_original():
    reg: Registry = Registry(kind="action")

    @reg.register("ping")
    def ping(params):
        return "pong"

    # The decorator returns the function unchanged, and it is registered.
    assert ping({}) == "pong"
    assert reg.get("ping") is ping
    assert reg.get("ping")({}) == "pong"


def test_get_missing_raises_not_registered_and_is_keyerror():
    reg: Registry = Registry()
    with pytest.raises(NotRegistered):
        reg.get("nope")
    # NotRegistered subclasses KeyError so legacy handlers still catch it.
    with pytest.raises(KeyError):
        reg.get("nope")


def test_get_or_none():
    reg: Registry = Registry()
    reg.register("x", 1)
    assert reg.get_or_none("x") == 1
    assert reg.get_or_none("missing") is None


def test_duplicate_without_override_raises():
    reg: Registry = Registry(kind="channel")
    reg.register("dup", 1)
    with pytest.raises(ValueError):
        reg.register("dup", 2)
    # The original survives an attempted clobber.
    assert reg.get("dup") == 1


def test_override_replaces():
    reg: Registry = Registry()
    reg.register("k", "old")
    reg.register("k", "new", override=True)
    assert reg.get("k") == "new"


def test_decorator_duplicate_without_override_raises():
    reg: Registry = Registry()

    @reg.register("d")
    def first(p):
        return 1

    with pytest.raises(ValueError):

        @reg.register("d")
        def second(p):
            return 2


def test_empty_name_rejected():
    reg: Registry = Registry()
    with pytest.raises(ValueError):
        reg.register("", 1)


def test_names_all_items_preserve_order():
    reg: Registry = Registry()
    reg.register("b", 2)
    reg.register("a", 1)
    reg.register("c", 3)
    assert reg.names() == ["b", "a", "c"]
    assert reg.all() == [2, 1, 3]
    assert reg.items() == {"b": 2, "a": 1, "c": 3}
    # items() is a copy; mutating it must not affect the registry.
    reg.items()["b"] = 999
    assert reg.get("b") == 2


def test_len_and_contains():
    reg: Registry = Registry()
    assert len(reg) == 0
    assert "x" not in reg
    reg.register("x", 1)
    assert len(reg) == 1
    assert "x" in reg
    assert reg["x"] == 1  # __getitem__


def test_iter():
    reg: Registry = Registry()
    reg.register("a", 1)
    reg.register("b", 2)
    assert list(reg) == ["a", "b"]


def test_unregister():
    reg: Registry = Registry()
    reg.register("x", 1)
    reg.unregister("x")
    assert "x" not in reg
    with pytest.raises(NotRegistered):
        reg.unregister("x")


def test_clear():
    reg: Registry = Registry()
    reg.register("a", 1)
    reg.register("b", 2)
    reg.clear()
    assert len(reg) == 0


def test_as_mapping_is_live_and_read_only():
    reg: Registry = Registry()
    reg.register("a", 1)
    view = reg.as_mapping()
    assert view["a"] == 1
    assert "a" in view
    assert view.get("a") == 1
    assert len(view) == 1
    # Live: a later registration shows up in the existing view.
    reg.register("b", 2)
    assert view["b"] == 2
    # Read-only: the view cannot be mutated directly.
    with pytest.raises(TypeError):
        view["c"] = 3  # type: ignore[index]


def test_attr_registry_keys_by_attribute():
    @dataclass(frozen=True)
    class Dept:
        name: str
        label: str = ""

    a = Dept("opt", "optimization")
    b = Dept("doc", "documentation")
    reg = attr_registry([a, b], kind="department", key="name")
    assert reg.get("opt") is a
    assert reg.get("doc") is b
    assert reg.names() == ["opt", "doc"]


def test_attr_registry_empty_default():
    reg = attr_registry()
    assert len(reg) == 0
