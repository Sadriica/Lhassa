# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``run_script`` action. Programmatic Tool Calling (PTC), Fase 3.

A thin ``(params) -> str`` wrapper over :func:`lhassa.agents.ptc.run_script` so
one LLM turn can hand over a Python script that orchestrates N deterministic
Lhassa actions instead of one tool call per turn. The script runs inside the
quarantine sandbox and every action call it makes is re-checked against the
security allowlist by the parent process (see ``lhassa/agents/ptc.py`` for the
full contract); the script gains zero privileges beyond a normal action
dispatch. Not registered in ``actionpack/__init__.py`` by default (a deliberate
Fase-3 rollout gate; see ``register_all()`` for the exact wiring to add there).
"""

from __future__ import annotations

from lhassa.agents import ptc


async def run_script(params: dict) -> str:
    """Run a PTC script through the quarantine sandbox. Never raises.

    ``params``:
        script; the Python source to run (required).
        instance_id; isolation scope (default ``Config.instance_id``).
        timeout; optional wall-clock override, in seconds.
        max_calls; optional cap on RPC calls the script may make.

    Returns a short outcome string (ok/calls/a stdout snippet on success, the
    error on failure).
    """
    try:
        if not isinstance(params, dict):
            params = {}
        script = str(params.get("script") or "").strip()
        if not script:
            return "run_script: no script provided"

        from lhassa.config import Config

        instance_id = str(params.get("instance_id") or Config.instance_id)

        kwargs: dict = {}
        if params.get("timeout") is not None:
            kwargs["timeout"] = float(params["timeout"])
        if params.get("max_calls") is not None:
            kwargs["max_calls"] = int(params["max_calls"])

        res = await ptc.run_script(script, instance_id=instance_id, **kwargs)

        calls = len(res.get("calls") or [])
        stdout_snip = (res.get("stdout") or "")[:200]
        if res.get("ok"):
            return f"run_script: ok calls={calls} stdout={stdout_snip!r}"
        err = res.get("error") or "unknown error"
        return f"run_script: failed calls={calls} error={err} stdout={stdout_snip!r}"
    except Exception as exc:  # an action must NEVER raise
        return f"run_script: error: {exc}"
