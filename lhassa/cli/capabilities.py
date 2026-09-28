# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa capabilities`` (alias ``doctor``); surface Lhassa's elasticity.

Lhassa is built to be **fully elastic and highly modular**: every swap point sits
behind an interface + a uniform :class:`~lhassa.registry.Registry`, and
capabilities drop in without editing core. This command makes that visible; it
reports, in one place, **what is wired** (every registry's registered names) and
**what is on/off** (the active configuration + which adapters/channels are actually
available/configured) plus the drop-in plugins discovered.

It is **LLM-free and offline**: it never constructs a provider, never calls a
model, and touches the network only insofar as plugin discovery / health already
might. :func:`collect_capabilities` is a pure gather (no printing) that is
**defensive end to end**: a missing or degraded subsystem is *reported*, never
fatal. :func:`render` turns that mapping into a clean, sectioned report.

Lives under ``lhassa/cli/`` (a shell layer) so the architecture guard stays green.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any, Callable, Optional

import typer
from rich.console import Console
from rich.table import Table

# Marks for on/off state in the rendered (non-JSON) report.
_ON = "✓"
_OFF = "–"


# ── pure collection ─────────────────────────────────────────────────────────────

def _config(config: Optional[Any]):
    """Return ``config`` or a freshly loaded default. Lazy import keeps this light."""
    if config is not None:
        return config
    from lhassa.config import Config

    return Config()


def _safe(errors: dict, label: str, fn: Callable[[], Any], default: Any = None) -> Any:
    """Run ``fn`` defensively; on any failure record it under ``label`` and return
    ``default`` (so one degraded subsystem is reported, never fatal)."""
    try:
        return fn()
    except Exception as exc:  # never raise; a limited subsystem is reported
        errors[label] = f"{type(exc).__name__}: {exc}"
        return default


def _collect_registries(errors: dict, config: Any) -> dict:
    """The wired swap points: each registry's registered names plus, where it is
    meaningful, the available/configured/active subset."""
    out: dict[str, Any] = {}

    def _providers() -> dict:
        from lhassa.llm.provider import PROVIDERS

        return {"registered": PROVIDERS.names()}

    def _harnesses() -> dict:
        from lhassa.harness.adapter import ADAPTERS, available_adapters

        return {
            "registered": ADAPTERS.names(),
            "available": [a.name for a in available_adapters()],
        }

    def _verifiers() -> dict:
        from lhassa.agents.verify import VERIFIERS

        return {"registered": VERIFIERS.names()}

    def _specialists() -> dict:
        from lhassa.agents.specialists.registry import SPECIALISTS

        return {"registered": SPECIALISTS.names()}

    def _channels() -> dict:
        from lhassa.notify.channels import CHANNELS, active_channels

        configured = []
        for name in CHANNELS.names():
            channel = CHANNELS.get_or_none(name)
            try:
                if channel is not None and channel.is_configured(config):
                    configured.append(name)
            except Exception:  # a misbehaving predicate must not break the report
                continue
        return {
            "registered": CHANNELS.names(),
            "configured": configured,
            "active": [c.name for c in active_channels(config)],
        }

    def _commands() -> dict:
        from lhassa.notify.commands import COMMANDS

        return {"registered": COMMANDS.names()}

    def _actions() -> dict:
        from lhassa.core import actions

        # `blocked` names actions that are registered but structurally cannot
        # act right now (a flag off, nothing configured). They are skipped by
        # the heartbeat WITHOUT leaving an episode, so this is where "why isn't
        # it running?" gets its answer.
        return {
            "registered": list(actions.ACTIONS),
            "blocked": actions.blocked_actions(),
        }

    def _departments() -> dict:
        from lhassa.org import routing

        return {"registered": [d.name for d in routing.list_departments()]}

    out["providers"] = _safe(errors, "providers", _providers, {"registered": []})
    out["harnesses"] = _safe(
        errors, "harnesses", _harnesses, {"registered": [], "available": []}
    )
    out["verifiers"] = _safe(errors, "verifiers", _verifiers, {"registered": []})
    out["specialists"] = _safe(errors, "specialists", _specialists, {"registered": []})
    out["channels"] = _safe(
        errors,
        "channels",
        _channels,
        {"registered": [], "configured": [], "active": []},
    )
    out["commands"] = _safe(errors, "commands", _commands, {"registered": []})
    out["actions"] = _safe(errors, "actions", _actions, {"registered": []})
    out["departments"] = _safe(errors, "departments", _departments, {"registered": []})
    return out


def _collect_active_config(errors: dict, config: Any) -> dict:
    """What is on/off right now; mirrored off plain config, no model call."""
    out: dict[str, Any] = {}

    def _routine() -> dict:
        # Mirror the router's routine (heartbeat) choice off plain config WITHOUT
        # constructing a provider or making a network call.
        from lhassa.notify.commands import _routine_provider_model

        provider, model = _routine_provider_model(config)
        return {"provider": provider, "model": model}

    routine = _safe(errors, "routine", _routine, {"provider": None, "model": None})
    out["routine_provider"] = routine.get("provider")
    out["routine_model"] = routine.get("model")

    out["pseudonymize_remote"] = bool(getattr(config, "pseudonymize_remote", False))
    out["memory_layer4_enabled"] = bool(getattr(config, "memory_layer4_enabled", False))
    out["voice_enabled"] = bool(getattr(config, "voice_enabled", False))
    out["llm_kill_switch"] = bool(getattr(config, "llm_kill_switch", False))

    def _telegram() -> dict:
        from lhassa.notify import telegram

        return {
            "configured": bool(telegram.is_configured(config)),
            "poll_enabled": bool(getattr(config, "telegram_poll_enabled", False)),
        }

    out["telegram"] = _safe(
        errors, "telegram", _telegram, {"configured": False, "poll_enabled": False}
    )

    def _org() -> dict:
        from lhassa.org.topology import load_topology

        topology = load_topology(config)
        nodes = topology.nodes()
        standalone = topology.is_standalone()
        if standalone:
            summary = "standalone (1 brain)"
        else:
            summary = f"{len(nodes)}-brain org"
        return {
            "role": (getattr(config, "org_role", "") or "") or "standalone",
            "standalone": standalone,
            "node_count": len(nodes),
            "summary": summary,
        }

    out["org"] = _safe(
        errors,
        "org",
        _org,
        {
            "role": (getattr(config, "org_role", "") or "") or "standalone",
            "standalone": None,
            "node_count": 0,
            "summary": "unavailable",
        },
    )

    out["plugins_dir"] = str(getattr(config, "plugins_dir", "") or "")
    return out


def _collect_plugins(errors: dict, config: Any) -> dict:
    """Drop-in plugins discovered (idempotent loader; never raises)."""

    def _do() -> dict:
        from pathlib import Path

        from lhassa.plugins import load_plugins

        loaded = load_plugins(config)
        raw = getattr(config, "plugins_dir", "") or ""
        path = Path(str(raw)).expanduser() if raw else None
        exists = bool(path and path.is_dir())
        return {
            "loaded": loaded,
            "plugins_dir": str(path) if path else "",
            "plugins_dir_exists": exists,
        }

    return _safe(
        errors,
        "plugins",
        _do,
        {"loaded": [], "plugins_dir": "", "plugins_dir_exists": False},
    )


def collect_capabilities(config: Optional[Any] = None) -> dict:
    """Gather Lhassa's capability picture: wired registries + on/off config + plugins.

    Pure (no printing) and **defensive end to end**: never raises. A missing or
    degraded subsystem is reported under ``errors`` and replaced with a safe
    placeholder, so the report is always complete. LLM-free: no provider is
    constructed and no model is called.
    """
    cfg = config
    errors: dict[str, str] = {}
    cfg = _safe(errors, "config", lambda: _config(config), None)

    return {
        "registries": _collect_registries(errors, cfg),
        "active_config": _collect_active_config(errors, cfg),
        "plugins": _collect_plugins(errors, cfg),
        "errors": errors,
    }


# ── rendering ───────────────────────────────────────────────────────────────────

def _mark(present: bool) -> str:
    return _ON if present else _OFF


def _names_line(names: list) -> str:
    return ", ".join(str(n) for n in names) if names else f"{_OFF} (none)"


def render(data: dict) -> str:
    """A clean, sectioned, human-readable report of :func:`collect_capabilities`."""
    # Render into an in-memory buffer (not stdout) and return the captured text;
    # the caller decides where it goes (so we never double-print).
    console = Console(file=io.StringIO(), record=True, width=100)
    reg = data.get("registries", {})
    active = data.get("active_config", {})
    plugins = data.get("plugins", {})
    errors = data.get("errors", {})

    console.print("[bold]Lhassa capabilities[/bold]", style="bold")

    # ── Registries (the wired swap points) ─────────────────────────────────────
    table = Table(title="Registries (wired swap points)", show_lines=False)
    table.add_column("swap point")
    table.add_column("registered")
    table.add_column("on/available")

    def _row(label: str, key: str, subset_key: Optional[str] = None) -> None:
        entry = reg.get(key, {}) or {}
        registered = entry.get("registered", []) or []
        if subset_key is not None:
            subset = entry.get(subset_key, []) or []
            on = f"{subset_key}: {_names_line(subset)}"
        else:
            on = "—"
        table.add_row(label, _names_line(registered), on)

    _row("Providers", "providers")
    _row("Harnesses", "harnesses", "available")
    _row("Verifiers", "verifiers")
    _row("Specialists", "specialists")
    _row("Channels", "channels", "active")
    _row("Commands", "commands")
    _row("Actions", "actions")
    _row("Departments", "departments")
    console.print(table)

    blocked = (reg.get("actions", {}) or {}).get("blocked", {}) or {}
    if blocked:
        console.print()
        console.print(
            "[dim]Blocked; registered, skipped while they cannot act "
            "(no episode is logged):[/dim]"
        )
        for name, reason in sorted(blocked.items()):
            console.print(f"  [dim]{name}; {reason}[/dim]")

    # Channels: also show the configured subset (distinct from active order).
    chan = reg.get("channels", {}) or {}
    if chan.get("registered"):
        console.print(
            f"  Channels configured: {_names_line(chan.get('configured', []))}",
            style="dim",
        )

    # ── Org ────────────────────────────────────────────────────────────────────
    org = active.get("org", {}) or {}
    console.print("\n[bold]Org[/bold]")
    console.print(
        f"  role={org.get('role')}  ·  {org.get('summary')}  ·  "
        f"nodes={org.get('node_count')}"
    )

    # ── Plugins ────────────────────────────────────────────────────────────────
    console.print("\n[bold]Plugins[/bold]")
    pdir = plugins.get("plugins_dir") or "(unset)"
    console.print(
        f"  dir={pdir}  [{_mark(bool(plugins.get('plugins_dir_exists')))} exists]"
    )
    console.print(f"  loaded: {_names_line(plugins.get('loaded', []))}")

    # ── Active config (what's on/off) ──────────────────────────────────────────
    console.print("\n[bold]Active config (on/off)[/bold]")
    tg = active.get("telegram", {}) or {}
    flags = [
        ("routine provider", f"{active.get('routine_provider')} / {active.get('routine_model')}"),
        ("pseudonymize remote", _mark(active.get("pseudonymize_remote"))),
        ("memory layer 4", _mark(active.get("memory_layer4_enabled"))),
        ("voice", _mark(active.get("voice_enabled"))),
        ("llm kill-switch", _mark(active.get("llm_kill_switch"))),
        ("telegram configured", _mark(tg.get("configured"))),
        ("telegram poll", _mark(tg.get("poll_enabled"))),
    ]
    for label, value in flags:
        console.print(f"  {label:<22} {value}")

    # ── Degraded subsystems (if any) ───────────────────────────────────────────
    if errors:
        console.print("\n[bold yellow]Degraded subsystems[/bold yellow]")
        for label, msg in errors.items():
            console.print(f"  {_OFF} {label}: {msg}", style="yellow")

    return console.export_text()


# ── CLI command ─────────────────────────────────────────────────────────────────

def capabilities_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the full capability object as one JSON document."
    ),
) -> None:
    """Show what Lhassa has wired and what is on/off (alias: ``doctor``).

    Offline and LLM-free: lists every registry's registered names, which harnesses
    are installed and which channels are configured/active, the routine provider,
    the org topology, the drop-in plugins, and the on/off feature flags.
    """
    data = collect_capabilities()
    if json_out:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
        return
    # Data to stdout; the export already excludes diagnostics (those go to stderr).
    sys.stdout.write(render(data))
    sys.stdout.flush()
