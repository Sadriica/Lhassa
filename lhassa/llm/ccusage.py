"""
ccusage — cross-harness LLM accounting from the ``ccusage`` CLI's JSON output.

The gateway prices the calls Lhassa itself makes (``llm.gateway``). But a lot of
real spend happens *outside* that choke point: work delegated to subscription
harnesses (Claude Code, Codex, opencode). ``ccusage`` reads those tools' local
usage logs and reports cost + tokens; we shell out to it, normalize the JSON, and
surface a single cross-harness summary.

Two public functions:
  * ``parse_ccusage(json_obj)`` — pure normalization (unit-tested on a fixture).
  * ``read_ccusage()`` — runs the CLI and parses it; fully defensive (never raises,
    returns ``{"available": False, ...}`` if the tool is missing or misbehaves).
"""

from __future__ import annotations

import asyncio
import json

# Try the installed binary first, then the npx fallback. ``--json`` = machine-readable;
# ``--offline`` uses cached pricing instead of fetching it over the network per run (that
# network fetch made a full run take 20s+ — offline drops it to ~3-4s).
_CCUSAGE_COMMANDS: list[list[str]] = [
    ["ccusage", "--offline", "--json"],
    ["npx", "ccusage@latest", "--offline", "--json"],
]

_EMPTY = {
    "available": False,
    "by_harness": {},
    "total_cost_usd": 0.0,
    "source": "ccusage",
}


def _coerce_float(value) -> float:
    try:
        return round(float(value or 0.0), 6)
    except (TypeError, ValueError):
        return 0.0


def _coerce_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _entry_cost_tokens(entry: dict) -> tuple[float, int]:
    """Pull (cost_usd, tokens) out of one ccusage record, tolerant of field names.

    [SUPUESTO] ccusage's documented daily/totals records use ``totalCost`` and
    ``totalTokens`` (with ``inputTokens``/``outputTokens`` components). We also
    accept ``cost``/``cost_usd``/``tokens`` so a wrapper's shape still parses.
    Validate the exact field names against a live ``ccusage --json`` smoke.
    """
    cost = entry.get("totalCost")
    if cost is None:
        cost = entry.get("cost_usd", entry.get("cost"))
    tokens = entry.get("totalTokens")
    if tokens is None:
        tokens = entry.get("tokens")
    if tokens is None:
        tokens = _coerce_int(entry.get("inputTokens")) + _coerce_int(entry.get("outputTokens"))
    return _coerce_float(cost), _coerce_int(tokens)


def parse_ccusage(data, source: str = "ccusage") -> dict:
    """Normalize a parsed ``ccusage --json`` object into a cross-harness summary.

    Returns ``{available, by_harness: {name: {cost_usd, tokens}}, total_cost_usd,
    source}``. Unexpected input yields ``available: False`` rather than raising.
    """
    if not isinstance(data, dict):
        return {**_EMPTY, "source": source, "error": "unexpected ccusage JSON (not an object)"}

    by_harness: dict[str, dict] = {}

    # [SUPUESTO] Preferred shape: an explicit per-harness breakdown. ccusage core
    # reports Claude Code only; a cross-harness aggregation would key cost/tokens
    # by harness name under ``harnesses`` (or ``byHarness``). Confirm in a live smoke.
    raw_harnesses = data.get("harnesses")
    if not isinstance(raw_harnesses, dict):
        raw_harnesses = data.get("byHarness")
    if isinstance(raw_harnesses, dict):
        for name, entry in raw_harnesses.items():
            if isinstance(entry, dict):
                cost, tokens = _entry_cost_tokens(entry)
                by_harness[str(name)] = {"cost_usd": cost, "tokens": tokens}

    # [SUPUESTO] Documented ccusage shape: ``{"daily": [...], "totals": {...}}`` with
    # no harness dimension. Attribute the whole report to a single harness named for
    # the source binary (it is Claude Code's usage).
    if not by_harness:
        totals = data.get("totals")
        if isinstance(totals, dict):
            cost, tokens = _entry_cost_tokens(totals)
            by_harness[source] = {"cost_usd": cost, "tokens": tokens}

    if not by_harness:
        return {**_EMPTY, "source": source, "error": "no totals or harness breakdown in ccusage JSON"}

    total_cost = round(sum(h["cost_usd"] for h in by_harness.values()), 6)
    return {
        "available": True,
        "by_harness": by_harness,
        "total_cost_usd": total_cost,
        "source": source,
    }


async def read_ccusage(timeout: float = 15.0, allow_npx: bool = True) -> dict:
    """Run ``ccusage --json`` (or the npx fallback) and return a normalized summary.

    ``timeout`` is a TOTAL budget shared across the candidate commands (a deadline),
    so trying the binary then the ``npx`` fallback can never exceed it. ``allow_npx``
    gates the ``npx ccusage@latest`` fallback: its cold-start downloads the package and
    is far too slow for a dashboard auto-load, so those callers pass ``allow_npx=False``
    (only the installed binary; instant degrade when it is absent). Fully defensive: a
    missing binary, a non-zero exit, a timeout, or malformed output all yield
    ``{"available": False, ...}``. This function never raises.
    """
    deadline = asyncio.get_event_loop().time() + max(0.0, timeout)
    for argv in _CCUSAGE_COMMANDS:
        if not allow_npx and argv and argv[0] == "npx":
            continue  # skip the slow npx cold-start for snappy callers
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break  # budget spent — degrade rather than block on the next candidate
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError):
            continue  # binary not on PATH — try the next candidate
        except Exception:
            continue

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=remaining)
        except (asyncio.TimeoutError, Exception):
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            continue

        if proc.returncode != 0 or not stdout:
            continue

        try:
            obj = json.loads(stdout.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            continue

        parsed = parse_ccusage(obj, source=argv[0])
        if parsed.get("available"):
            return parsed

    return {**_EMPTY, "error": "ccusage not available"}
