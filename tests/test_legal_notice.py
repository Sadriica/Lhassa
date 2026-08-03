"""The Appropriate Legal Notice must actually exist in every surface.

Lhassa's LICENSE carries an additional term under AGPL-3.0 section 7(b): the
attribution notice must be preserved and displayed wherever legal or version
information is shown. A term like that is worth nothing if the notice is a
decorative string someone can delete without consequence — so these tests are
the consequence. Removing the notice from the CLI, the API, the dashboard, or
LICENSE itself fails the suite.
"""

import json
import pathlib
import re

import pytest

from lhassa import legal

REPO = pathlib.Path(__file__).resolve().parent.parent


# ── the notice itself ─────────────────────────────────────────────────────────


def test_notice_names_the_project_the_vendor_and_the_license():
    assert "Lhassa" in legal.NOTICE
    assert "Witara" in legal.NOTICE
    assert "AGPL-3.0-or-later" in legal.NOTICE
    assert legal.SOURCE_URL in legal.NOTICE


def test_notice_dict_is_json_serialisable():
    payload = legal.notice_dict()
    json.dumps(payload)  # must not raise
    assert payload["license"] == "AGPL-3.0-or-later"
    assert payload["vendor"] == "Witara"


# ── LICENSE ───────────────────────────────────────────────────────────────────


def test_license_is_agpl_with_the_section_7_term():
    text = (REPO / "LICENSE").read_text()
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 19 November 2007" in text
    assert "ADDITIONAL TERMS UNDER AGPL-3.0 SECTION 7" in text
    # The notice quoted in LICENSE must be the notice the code actually renders.
    assert legal.ATTRIBUTION in text
    assert legal.SOURCE_URL in text


def test_license_no_longer_grants_mit():
    text = (REPO / "LICENSE").read_text()
    assert "MIT License" not in text
    assert "Permission is hereby granted, free of charge" not in text


# ── the three surfaces ────────────────────────────────────────────────────────


def test_cli_version_renders_the_notice():
    """`lhassa version` must show it — and `--json` must not be a way around it."""
    from lhassa.cli.extra_cmds import version_info

    info = version_info()
    assert info["legal"]["notice"] == legal.NOTICE


def test_cli_source_actually_references_the_notice_module():
    """The panel must render legal.NOTICE, not a copy pasted next to it."""
    src = (REPO / "lhassa" / "cli" / "extra_cmds.py").read_text()
    assert "_legal.NOTICE" in src


@pytest.mark.asyncio
async def test_api_exposes_the_notice():
    from lhassa.api.runtime_routes import get_legal, get_status

    assert (await get_legal())["notice"] == legal.NOTICE
    assert (await get_status())["legal"]["notice"] == legal.NOTICE


def test_dashboard_notice_matches_the_python_one():
    """The dashboard copy and lhassa/legal.py must not drift apart."""
    js = (REPO / "dashboard" / "src" / "legal.js").read_text()

    def const(name: str) -> str:
        m = re.search(rf"export const {name} = '([^']*)'", js)
        assert m, f"{name} missing from dashboard/src/legal.js"
        return m.group(1)

    assert const("PROJECT") == legal.PROJECT
    assert const("VENDOR") == legal.VENDOR
    assert const("LICENSE_ID") == legal.LICENSE_ID
    assert const("SOURCE_URL") == legal.SOURCE_URL


def test_dashboard_about_card_renders_the_notice():
    src = (REPO / "dashboard" / "src" / "components" / "SettingsPanel.jsx").read_text()
    assert "from '../legal.js'" in src
    assert "{NOTICE}" in src


# ── package metadata ──────────────────────────────────────────────────────────


def test_pyproject_declares_the_license():
    text = (REPO / "pyproject.toml").read_text()
    assert "AGPL-3.0-or-later" in text


def test_dashboard_package_declares_the_license():
    pkg = json.loads((REPO / "dashboard" / "package.json").read_text())
    assert pkg.get("license") == "AGPL-3.0-or-later"


def test_readmes_declare_agpl_and_not_mit():
    for name in ("README.md", "README.es.md"):
        text = (REPO / name).read_text()
        assert "AGPL" in text, f"{name} does not mention AGPL"
        assert "MIT — see" not in text, f"{name} still declares MIT"
        assert "MIT — ver" not in text, f"{name} still declares MIT"
