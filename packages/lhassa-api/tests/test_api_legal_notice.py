"""The Appropriate Legal Notice must actually exist in every surface.

Lhassa's LICENSE carries an additional term under AGPL-3.0 section 7(b): the
attribution notice must be preserved and displayed wherever legal or version
information is shown. A term like that is worth nothing if the notice is a
decorative string someone can delete without consequence; so these tests are
the consequence. Removing the notice from the CLI, the API, the dashboard, or
LICENSE itself fails the suite.
"""

import pathlib

import pytest

from lhassa import legal

REPO = pathlib.Path(__file__).resolve().parent.parent


# ── the notice itself ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_exposes_the_notice():
    from lhassa.api.runtime_routes import get_legal, get_status

    assert (await get_legal())["notice"] == legal.NOTICE
    assert (await get_status())["legal"]["notice"] == legal.NOTICE
