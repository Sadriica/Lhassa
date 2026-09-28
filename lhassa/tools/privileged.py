# SPDX-License-Identifier: AGPL-3.0-or-later
"""Privileged command execution; scoped, allowlisted, and audited.

lhassa may run a curated set of admin commands as root via ``sudo -n`` once the
sudoers drop-in (``scripts/lhassa-sudoers``, installed by
``scripts/install-privileged.sh``) is in place. Anything outside the allowlist,
or matching a catastrophic pattern, is refused and recorded in the audit log.

Two layers guard this:
- the sudoers file is the kernel-enforced boundary (only allowlisted binaries
  run passwordless);
- this module is the application-level allowlist + destructive-pattern check.

Keep the allowlist here in sync with ``scripts/lhassa-sudoers``.
"""

import asyncio
import os
import shlex

from loguru import logger

from lhassa.state import state

# Candidate commands lhassa may run as root, distro-agnostic. The effective
# allowlist keeps only those actually installed on THIS host (mirrors how
# scripts/install-privileged.sh builds the sudoers per host): universal admin
# tools, whichever package manager this distro ships, and the laptop/power
# tools when present. The sudoers file is the kernel-side gate; this set is the
# application-side mirror.
_CANDIDATE_ALLOW = {
    "systemctl",
    "journalctl",
    "dmesg",
    "sensors",
    # Package managers (Arch / Debian+Ubuntu / Fedora / openSUSE).
    "pacman",
    "apt-get",
    "apt",
    "dnf",
    "zypper",
    # Laptop/power tools.
    "tlp",
    "tlp-stat",
    "power-mode",
}


def _installed_allow() -> set[str]:
    import shutil

    return {c for c in _CANDIDATE_ALLOW if shutil.which(c)}


_DEFAULT_ALLOW = _installed_allow()

# Refused even when the base command is allowlisted.
_DESTRUCTIVE = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "of=/dev/sd",
    ":(){",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
)

_TIMEOUT = float(os.getenv("LHASSA_PRIVILEGED_TIMEOUT", "60"))


def allowlist() -> set[str]:
    """Allowed base commands, extendable via LHASSA_PRIVILEGED_ALLOW (comma-separated)."""
    base = set(_DEFAULT_ALLOW)
    extra = os.getenv("LHASSA_PRIVILEGED_ALLOW", "")
    if extra:
        base |= {item.strip() for item in extra.split(",") if item.strip()}
    return base


def _base_command(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    return os.path.basename(parts[0]) if parts else None


async def run_privileged(
    command: str,
    *,
    source: str = "assistant",
    timeout: float | None = None,
) -> str:
    """Run ``command`` as root via ``sudo -n`` if it passes the allowlist.

    Returns a human-readable result string; never raises. Every attempt is
    recorded via ``state.record_audit_event``. If the sudoers drop-in is not
    installed, ``sudo -n`` fails cleanly and we report that.
    """
    base = _base_command(command)
    if base is None:
        return "I couldn't parse that command, so I didn't run it."

    lowered = command.lower()
    if any(pattern in lowered for pattern in _DESTRUCTIVE):
        await state.record_audit_event(
            "privileged_exec", command, source, "blocked", "destructive pattern"
        )
        return f"I won't run that as root; it looks destructive ({base})."

    allowed = allowlist()
    if base not in allowed:
        await state.record_audit_event(
            "privileged_exec", command, source, "blocked", "not in allowlist"
        )
        return (
            f"'{base}' isn't in my privileged allowlist, so I left it alone. "
            f"Allowed: {', '.join(sorted(allowed))}."
        )

    argv = ["sudo", "-n", *shlex.split(command)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout or _TIMEOUT
        )
        text = (out or b"").decode(errors="replace").strip()
        ok = proc.returncode == 0
        await state.record_audit_event(
            "privileged_exec", command, source,
            "ok" if ok else "error", f"rc={proc.returncode}",
        )
        if not ok and "a password is required" in text:
            return (
                "I don't have passwordless sudo for that yet; run "
                "`sudo bash scripts/install-privileged.sh` to enable the allowlist."
            )
        if not text:
            return "Done." if ok else f"That command failed (exit {proc.returncode})."
        return text
    except asyncio.TimeoutError:
        await state.record_audit_event(
            "privileged_exec", command, source, "error", "timeout"
        )
        return f"That command took too long (over {timeout or _TIMEOUT:.0f}s), so I stopped it."
    except FileNotFoundError:
        return "I couldn't find `sudo` on this system."
    except Exception as exc:  # never let a privileged call crash the caller
        logger.warning(f"privileged exec failed: {exc}")
        await state.record_audit_event(
            "privileged_exec", command, source, "error", str(exc)
        )
        return f"Something went wrong running that as root: {exc}"
