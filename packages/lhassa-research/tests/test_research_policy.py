# SPDX-License-Identifier: AGPL-3.0-or-later
"""The research pack declares the allowlist rows for its own actions."""

from __future__ import annotations

import pytest

from lhassa import actionpack
from lhassa.security import allowlist as al
from lhassa.security.trust import TrustLevel


@pytest.mark.parametrize(
    "action, trust",
    [
        ("research_intake", TrustLevel.HUMANO),
        ("draft_research_note", TrustLevel.INTERNO),
    ],
)
def test_pack_policy_row(action, trust):
    actionpack.register_all()
    spec = al.ALLOWLIST[action]
    assert spec.min_trust == trust
    assert spec.requires_approval is False
    assert action not in al._CORE_NAMES
