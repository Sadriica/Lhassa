# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capability packs declare policy rows for their own actions, never core's."""

from __future__ import annotations

import pytest

from lhassa.security import allowlist as al
from lhassa.security.trust import TrustLevel

SPEC = al.ActionSpec(objetivos=al.WILDCARD, min_trust=TrustLevel.INTERNO, requires_approval=False)


@pytest.fixture
def clean_pack_specs():
    saved_specs = dict(al.PACK_SPECS)
    saved_table = dict(al.ALLOWLIST)
    yield
    al.PACK_SPECS.clear()
    al.PACK_SPECS.update(saved_specs)
    al.ALLOWLIST.clear()
    al.ALLOWLIST.update(saved_table)


def test_pack_row_joins_the_live_table_and_rebuilds(clean_pack_specs):
    al.register_action_spec("test_pack_action", SPEC)
    assert al.ALLOWLIST["test_pack_action"] is SPEC
    assert al.build_allowlist()["test_pack_action"] is SPEC


def test_a_pack_cannot_redefine_core_policy(clean_pack_specs):
    relaxed = al.ActionSpec(objetivos=al.WILDCARD, min_trust=TrustLevel.INTERNO, requires_approval=False)
    with pytest.raises(ValueError):
        al.register_action_spec("run_command", relaxed)
    assert al.ALLOWLIST["run_command"].requires_approval is True


def test_same_spec_twice_is_a_noop_a_different_one_raises(clean_pack_specs):
    al.register_action_spec("test_pack_action", SPEC)
    al.register_action_spec("test_pack_action", SPEC)
    stricter = al.ActionSpec(objetivos=al.WILDCARD, min_trust=TrustLevel.INTERNO, requires_approval=True)
    with pytest.raises(ValueError):
        al.register_action_spec("test_pack_action", stricter)


def test_an_action_with_no_row_stays_denied():
    assert "test_never_declared" not in al.build_allowlist()
