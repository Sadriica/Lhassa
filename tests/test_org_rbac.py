"""Tests for lhassa.org.rbac; the role→permission matrix (Fase 7)."""

import pytest

from lhassa.org import rbac


# ── general permissions ─────────────────────────────────────────────────────────

def test_general_may_write_commons_and_assign():
    assert rbac.can("general", "write", "commons")
    assert rbac.can("general", "assign", "task")
    assert rbac.can("general", "collect", "task")
    assert rbac.can("general", "read", "department")  # oversight


def test_general_may_not_handle_task():
    # the general COORDINATES, it does not execute department work
    assert not rbac.can("general", "handle", "task")


# ── department permissions ──────────────────────────────────────────────────────

def test_department_reads_commons_and_owns_its_memory():
    assert rbac.can("department", "read", "commons")
    assert rbac.can("department", "write", "own_memory")
    assert rbac.can("department", "handle", "task")


def test_department_may_not_write_commons():
    assert not rbac.can("department", "write", "commons")


def test_department_may_not_assign():
    assert not rbac.can("department", "assign", "task")


def test_department_may_not_touch_another_department():
    # a department cannot access ANOTHER department's data/action
    assert not rbac.can("department", "read", "department")
    assert not rbac.can("department", "write", "department")


# ── deny-by-default + require() ─────────────────────────────────────────────────

def test_unknown_role_denied():
    assert not rbac.can("intern", "read", "commons")


def test_require_passes_on_grant():
    rbac.require("general", "write", "commons")  # must not raise


def test_require_raises_on_violation():
    with pytest.raises(PermissionError):
        rbac.require("department", "write", "commons")
    with pytest.raises(PermissionError):
        rbac.require("department", "assign", "task")
