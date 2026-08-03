# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The Appropriate Legal Notice — one source of truth for every surface.

Lhassa is licensed under AGPL-3.0-or-later with an additional term under
section 7(b) of that license (see ``LICENSE``): the notice below must be
preserved and displayed in every user-facing interface that shows legal or
version information.

A term like that is only meaningful if the notice ACTUALLY EXISTS in the
product. So it lives here as a constant rather than as a hard-coded string in
each surface, every surface renders :data:`NOTICE` (or :func:`notice_lines`),
and ``tests/test_legal_notice.py`` asserts each one still does. Deleting the
notice from a surface breaks a test, which is the point — it is a legal notice,
not decoration.

This is **leaf infra**: it imports nothing from the package, so any layer may
use it.
"""

from __future__ import annotations

__all__ = [
    "PROJECT",
    "VENDOR",
    "LICENSE_ID",
    "SOURCE_URL",
    "ATTRIBUTION",
    "NOTICE",
    "notice_lines",
    "notice_dict",
]

PROJECT = "Lhassa"
VENDOR = "Witara"
LICENSE_ID = "AGPL-3.0-or-later"
SOURCE_URL = "https://github.com/Sadriica/Lhassa"

#: The short attribution line. Kept separate because some surfaces (a CLI panel
#: footer, a dashboard footer) have room for one line and not three.
ATTRIBUTION = f"{PROJECT} — a {VENDOR} project"

#: The full Appropriate Legal Notice, exactly as ``LICENSE`` requires it.
NOTICE = (
    f"{ATTRIBUTION}. Licensed under {LICENSE_ID}.\n"
    f"Source: {SOURCE_URL}"
)


def notice_lines() -> list[str]:
    """The notice as individual lines, for surfaces that render line by line."""
    return NOTICE.split("\n")


def notice_dict() -> dict:
    """The notice as a JSON-serialisable mapping, for API responses."""
    return {
        "project": PROJECT,
        "vendor": VENDOR,
        "attribution": ATTRIBUTION,
        "license": LICENSE_ID,
        "source": SOURCE_URL,
        "notice": NOTICE,
    }
