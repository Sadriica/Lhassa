# SPDX-License-Identifier: AGPL-3.0-or-later
"""The owner's no-em-dash rule, enforced on the tree instead of remembered.

The house rule states it absolutely: "No em dashes in anything new. Runtime messages,
UI strings, docs, comments, commit messages." It has been swept by hand more
than once and it keeps coming back, because a rule that lives only in a document
is re-litigated by every new file. This is the same shape as
``tests/test_architecture.py``: a deterministic, $0, dependency-free scan with an
explicit register of the legacy files still carrying dashes.

The register is a CEILING, not a permission. Adding a dash to a file already in
it is allowed (the file is already dirty); adding one to any other file fails,
and so does listing a file that no longer has any, so the register can only ever
shrink. Retiring the last entry retires this list with it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

DASHES = ("—", "–", "―")  # em, en, horizontal bar

#: Extensions worth scanning: the ones that become text a human or a model reads.
SCANNED_SUFFIXES = {".py", ".md", ".js", ".jsx", ".ts", ".tsx", ".yml", ".yaml", ".sh", ".toml"}

#: Paths that carry the rule's own subject matter and must keep their dashes:
#: the scrubber that removes them, and the tests that prove it works.
SELF_REFERENTIAL = {
    "lhassa/persona.py",
    "tests/test_persona.py",
    "tests/test_no_em_dashes.py",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=_repo_root(), capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        pytest.skip("not a git checkout; nothing to scan")
    return [line for line in out.stdout.splitlines() if line.strip()]


def _files_with_dashes() -> set[str]:
    root = _repo_root()
    found: set[str] = set()
    for rel in _tracked_files():
        if rel in SELF_REFERENTIAL or Path(rel).suffix not in SCANNED_SUFFIXES:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(dash in text for dash in DASHES):
            found.add(rel)
    return found


def _load_register() -> set[str]:
    path = Path(__file__).parent / "em_dash_legacy.txt"
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def test_no_new_file_carries_an_em_dash():
    offenders = sorted(_files_with_dashes() - _load_register())
    if offenders:
        listing = "\n".join(f"  {name}" for name in offenders)
        pytest.fail(
            "Em dashes in files outside the legacy register (the house rule forbids "
            f"them in anything new):\n{listing}\n"
            "Replace them with ':', ';', ',' or a rewrite. Do NOT add the file "
            "to tests/em_dash_legacy.txt; that register only shrinks."
        )


def test_the_legacy_register_has_no_stale_entries():
    """A cleaned file must leave the register, so it can never grow back."""
    stale = sorted(_load_register() - _files_with_dashes())
    if stale:
        listing = "\n".join(f"  {name}" for name in stale)
        pytest.fail(
            "These files are clean now; drop them from tests/em_dash_legacy.txt "
            f"so the rule keeps tightening:\n{listing}"
        )
