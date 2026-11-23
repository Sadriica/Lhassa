# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-card; deterministic, $0 self-knowledge for the chat think.

The Telegram chat model used to answer "what are you running on?" with a shrug:
the think carried identity + memory but nothing about the LIVE configuration.
This module renders a small block (~200 tokens) from :class:`lhassa.config.Config`
and the registries (no model, no network, no DB) so every chat turn knows:

- what it IS (version, instance) and which seats think where (chat provider/model,
  heartbeat provider, the CLI-first fallback chain);
- HOW it executes; the modality taxonomy (deterministic actions, PTC scripts,
  metered API think, subscription CLIs, markers, org delegation), so the model
  can explain itself and reason about which route fits a request;
- which registered actions exist for the ``[[action: …]]`` marker, split into
  run-now vs approval-gated (the allowlist's ``requires_approval`` flag).

Everything is best-effort: any failing sub-part degrades to a placeholder and
``build`` never raises; a broken card must not cost the owner a chat reply.
Secrets never enter the card (values are names/counts only, never key material).
"""

from __future__ import annotations

import shutil

from loguru import logger

# The static half of the card: the execution-modality taxonomy. Facts only;
# each line names the mechanism, its cost shape, and its gate, so the model
# stops conflating "a script", "a function", "an API" and "a CLI".
_MODALITIES = (
    "How you execute (distinct mechanisms; never conflate them):\n"
    "- ACTIONS: registered deterministic functions (the ACTIONS registry). $0, "
    "no model involved, allowlist-gated. Reachable from chat via the "
    "[[action: …]] marker.\n"
    "- PTC scripts: one model turn writes a Python script that orchestrates "
    "many actions; it runs inside the quarantine sandbox and every call passes "
    "the signed-task allowlist. Approval-gated.\n"
    "- API think: what you are doing RIGHT NOW; a metered token completion "
    "through the cloud gateway. Costs money per token; the answer cache and "
    "intent router exist to avoid it.\n"
    "- Subscription CLIs: claude/codex/opencode run locally on subscription "
    "($0 marginal). They are the agentic body (real file edits and tool use) "
    "reached via /code (owner-approved) and preferred over the API in the "
    "fallback chain.\n"
    "- Markers: [[research: …]], [[remember: …]], [[action: …]]; you only "
    "leave the signal; the system mints a signed task and executes it outside "
    "this turn.\n"
    "- Org delegation: departments under you execute and results bubble up "
    "the hierarchy, one logged hop per brain."
)


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("lhassa")
    except Exception:
        return "unknown"


def _clis_on_path() -> list[str]:
    return [c for c in ("claude", "codex", "opencode") if shutil.which(c)]


def _action_lists() -> tuple[list[str], list[str]]:
    """(run-now action names, approval-gated action names), both sorted.

    Only actions BOTH registered in the ACTIONS registry and present in the
    allowlist are advertised; the marker path enforces the same intersection.
    """
    from lhassa import actionpack
    from lhassa.core.actions import ACTIONS
    from lhassa.security.allowlist import build_allowlist

    actionpack.register_all()  # idempotent; the card may render before a beat
    allowlist = build_allowlist()
    free, gated = [], []
    for name in sorted(ACTIONS):
        spec = allowlist.get(name)
        if spec is None:
            continue
        (gated if spec.requires_approval else free).append(name)
    return free, gated


def _cli_surface() -> list[str]:
    """``group: sub, sub, …`` lines for every ``lhassa`` command group.

    Read off the live Typer app rather than hand-listed, so a new subcommand
    shows up here the moment it is registered and this can never drift into a
    lie. This is the operator surface a terminal turn needs: creating a brain,
    a flow or a skill is a CLI subcommand, not an ACTIONS entry, so an agent
    that only knew the action registry concluded it could not do those at all.
    """
    from lhassa.cli.app import app

    lines: list[str] = []
    for group in getattr(app, "registered_groups", []):
        inner = getattr(group, "typer_instance", None)
        name = group.name or getattr(getattr(inner, "info", None), "name", None)
        if not inner or not name:
            continue
        subs = [
            c.name or getattr(c.callback, "__name__", "")
            for c in getattr(inner, "registered_commands", [])
        ]
        subs = [s for s in subs if s]
        if subs:
            lines.append(f"    lhassa {name}: {', '.join(subs)}")
    return lines


def build(config, *, door: str = "chat") -> str:
    """Render the self-card. Deterministic, $0, never raises.

    ``door`` names the surface asking. It changes only HOW the actions are
    described, never which ones exist: the chat door reaches them by leaving a
    ``[[action: …]]`` marker for the system to mint, while the CLI door is a
    real tool-calling loop that can invoke them directly. Telling the terminal
    about markers it cannot use (and never telling it the action names at all,
    which is what happened before this) is why a CLI turn spent its whole tool
    budget running ``lhassa --help`` to rediscover capabilities it already had.
    """
    lines: list[str] = ["Your live configuration (real, current values):"]
    try:
        lines.append(
            f"- You are Lhassa v{_version()}, instance "
            f"{getattr(config, 'instance_id', None) or 'inst_personal'}, running "
            f"locally on the owner's machine."
        )
        provider = getattr(config, "llm_provider", "") or "?"
        model = getattr(config, "model", "") or "provider-default"
        pinned = (getattr(config, "telegram_provider", "") or "").strip()
        seat = f"{provider} ({model})"
        if pinned and pinned not in ("auto", "default"):
            seat = f"{pinned} (pinned) over {seat}"
        lines.append(f"- This chat thinks via the cloud gateway seat: {seat}.")
        lines.append(
            f"- Heartbeat/routine seat: "
            f"{getattr(config, 'heartbeat_provider', 'auto') or 'auto'}; fallback "
            f"chain (CLI subscriptions before the metered API): "
            f"{getattr(config, 'fallback_providers', '') or 'claude,codex,default'}."
        )
        clis = _clis_on_path()
        lines.append(
            "- Agentic CLIs on PATH: " + (", ".join(clis) if clis else "none")
            + "."
        )
    except Exception as exc:
        logger.warning(f"selfcard: config lines degraded: {exc}")

    try:
        free, gated = _action_lists()
        if door == "cli":
            how = (
                "- Registered actions (deterministic, $0, no model). From this "
                "terminal you reach them by running `lhassa` subcommands or the "
                "`shell` tool; you do NOT need to discover them with --help, the "
                "list is right here: "
            )
        else:
            how = (
                "- Registered actions you may request with "
                "[[action: <name>]]; run immediately: "
            )
        lines.append(f"{how}{', '.join(free) or 'none'}.")
        if gated:
            lines.append(
                f"- Approval-gated actions (park for the owner's tap): "
                f"{', '.join(gated)}."
            )
    except Exception as exc:
        logger.warning(f"selfcard: action lines degraded: {exc}")

    if door == "cli":
        try:
            surface = _cli_surface()
            if surface:
                lines.append(
                    "- Your own operator surface. These are YOUR commands, run "
                    "them with the shell tool to create and change your own "
                    "brains, flows, skills, projects and settings:"
                )
                lines.extend(surface)
        except Exception as exc:
            logger.warning(f"selfcard: cli surface degraded: {exc}")

    return "\n".join(lines) + "\n\n" + _MODALITIES
