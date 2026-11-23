"""Skip markers for tests that run the tool forge's gates end to end."""

from __future__ import annotations

import subprocess

import pytest

from lhassa.security.quarantine import _DEFAULT_PATH


def _system_python_has_pytest() -> bool:
    """True when the interpreter the quarantine finds has pytest.

    The forge's "segura" gate runs ``python3 -m pytest`` inside the quarantine,
    whose environment is scrubbed down to the system PATH. A runner or machine
    whose system ``python3`` has no pytest (the project's own pytest lives in
    the venv) cannot graduate any candidate, so the tests that expect a
    graduation are skipped there rather than reported as forge failures.
    """
    try:
        done = subprocess.run(
            ["python3", "-m", "pytest", "--version"],
            env={"PATH": _DEFAULT_PATH},
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


needs_system_pytest = pytest.mark.skipif(
    not _system_python_has_pytest(),
    reason="the system python3 (the quarantine's PATH) has no pytest",
)
