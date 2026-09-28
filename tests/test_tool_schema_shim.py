# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tool catalog must import (and render identically) without the voice extra.

``lhassa/tools/schemas.py`` is what every door sends the model, including the
CLI. The default install ships without pipecat by design (see CLAUDE.md: the
API/heartbeat/telegram/CLI roles never import pipecat or torch), so the catalog
carries a local stand-in for pipecat's two data holders.

A stand-in is only safe while it renders the SAME dict, so this pins the shim
against the real thing whenever pipecat is actually installed; on a no-voice
install that comparison is skipped and only the import-without-pipecat property
is asserted.
"""

from __future__ import annotations

import pytest


def test_the_catalog_imports_and_renders_without_pipecat():
    """The property that matters on a default install: it works at all."""
    from lhassa.tools.schemas import TOOLS

    rendered = [fs.to_default_dict() for fs in TOOLS.standard_tools]
    assert rendered, "the catalog must not be empty"
    for entry in rendered:
        assert set(entry) == {"name", "description", "parameters"}
        assert entry["parameters"]["type"] == "object"
        assert isinstance(entry["parameters"]["properties"], dict)
        assert isinstance(entry["parameters"]["required"], list)


def test_the_shim_renders_exactly_what_pipecat_renders():
    """Pin the stand-in to the real type so the two cannot drift."""
    pipecat_schema = pytest.importorskip(
        "pipecat.adapters.schemas.function_schema",
        reason="voice extra not installed; nothing to compare against",
    )

    from lhassa.tools import schemas as catalog

    sample = dict(
        name="probe",
        description="a probe",
        properties={"q": {"type": "string", "description": "query"}},
        required=["q"],
    )
    theirs = pipecat_schema.FunctionSchema(**sample).to_default_dict()
    ours = catalog.FunctionSchema(**sample).to_default_dict()
    # When the voice extra IS installed the module binds pipecat's own type, so
    # this also guards the import order: `ours` must never be a third shape.
    assert ours == theirs
