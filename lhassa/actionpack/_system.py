"""System actions: run_command, system_snapshot, power_mode.

run_command is the one action that requires human approval (requires_approval=True
in the allowlist) — it executes arbitrary shell commands in the quarantine sandbox.

system_snapshot and power_mode are deterministic and $0 (no LLM, no network).
"""

from __future__ import annotations

import re
import shutil
import subprocess

from lhassa.security.quarantine import run_in_quarantine

# Regex for power_mode's mode argument — only safe lowercase-kebab strings.
_MODE_RE = re.compile(r"^[a-z-]{1,20}$")

# Output cap for run_command (chars returned to the caller).
_CMD_OUTPUT_CAP = 2000


async def run_command(params: dict) -> str:
    """Execute a shell command inside the quarantine sandbox.

    Uses ``security.quarantine.run_in_quarantine`` (strongest available
    isolation: unshare → firejail → bwrap → best-effort). No network, scrubbed
    env, 60 s timeout, killpg on expiry. Output is capped at 2000 chars.

    This action has ``requires_approval=True`` in the allowlist — a human must
    approve it before it runs.

    params:
        command (required)  — shell code string or argv list.
        timeout             — integer seconds (default 60, max 120).

    Never raises.
    """
    try:
        cmd = params.get("command") or params.get("cmd") or ""
        if not cmd:
            return "run_command: 'command' param is required"

        timeout_raw = params.get("timeout", 60)
        try:
            timeout = max(1, min(120, int(timeout_raw)))
        except (TypeError, ValueError):
            timeout = 60

        result = await run_in_quarantine(cmd, timeout=timeout)

        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = (out + ("\n" + err if err else "")).strip()
        snippet = combined[:_CMD_OUTPUT_CAP]
        truncated = "…" if len(combined) > _CMD_OUTPUT_CAP else ""

        if result.timed_out:
            return f"run_command: timed out after {timeout}s. output: {snippet}{truncated}"
        if result.escaped:
            return f"run_command: sandbox escape detected (isolation={result.isolation})"
        status = "ok" if result.ok else f"exit {result.exit_code}"
        return f"run_command [{status}] isolation={result.isolation}: {snippet}{truncated}"

    except Exception as exc:
        return f"run_command: error: {exc}"


def system_snapshot(params: dict) -> str:
    """Return a one-liner system snapshot (disk/memory/failed-units/uptime).

    Deterministic, $0, no LLM, no network. Reads /proc/uptime and calls the
    health.checks disk/memory/failed-unit collectors directly. Never raises.

    params: (none required)
    """
    try:
        from lhassa.health.checks import check_disk, check_failed_units, check_mem_pressure

        # Uptime from /proc/uptime (seconds since boot, no subprocess needed).
        try:
            uptime_text = open("/proc/uptime").read().split()[0]
            uptime_s = float(uptime_text)
            uptime_h = int(uptime_s // 3600)
            uptime_m = int((uptime_s % 3600) // 60)
            uptime_str = f"up {uptime_h}h{uptime_m:02d}m"
        except Exception:
            uptime_str = "uptime unknown"

        disk = check_disk()
        mem = check_mem_pressure()
        units = check_failed_units()

        parts = [
            uptime_str,
            disk["summary"],
            mem["summary"],
            units["summary"],
        ]
        return "system_snapshot: " + " | ".join(parts)

    except Exception as exc:
        return f"system_snapshot: error: {exc}"


def power_mode(params: dict) -> str:
    """Set the system power mode via the ``power-mode`` script (NOPASSWD sudo).

    Validates the mode string against ``^[a-z-]{1,20}$`` to prevent injection.
    Refuses if ``power-mode`` is not on PATH. The script validates its own mode
    values and will exit non-zero for unrecognised modes.

    params:
        mode (required)  — e.g. "performance", "balanced", "power-saver".

    Never raises.
    """
    try:
        mode = str(params.get("mode") or "").strip()
        if not mode:
            return "power_mode: 'mode' param is required"
        if not _MODE_RE.match(mode):
            return (
                f"power_mode: mode {mode!r} is invalid "
                "(must match ^[a-z-]{{1,20}}$)"
            )

        if not shutil.which("power-mode"):
            return "power_mode: 'power-mode' script not found on PATH"

        proc = subprocess.run(
            ["power-mode", mode],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            out = (proc.stdout or "").strip() or "ok"
            return f"power_mode {mode}: {out}"
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        return f"power_mode {mode}: failed — {err}"

    except subprocess.TimeoutExpired:
        return "power_mode: timed out"
    except Exception as exc:
        return f"power_mode: error: {exc}"
