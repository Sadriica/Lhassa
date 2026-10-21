# SPDX-License-Identifier: AGPL-3.0-or-later
"""Action commands for the Lhassa CLI (additive to the default chat/REPL).

These extend the terminal surface beyond the read-only power subcommands
(``tools``/``approvals``/``report``/...) with the things the owner actually
*does* day to day:

  - ``lhassa status`` (alias ``overview``); the whole brain at a glance: brains,
    skills, pending approvals, today's cost, health and recent activity. It renders
    the SAME aggregate the dashboard's ``/api/overview`` serves, built by calling
    the in-process :func:`lhassa.views.overview.build_overview` directly (no HTTP).
  - ``lhassa version``: the package version + Python + a few key toggles.
  - ``lhassa serve``: run the dashboard + REST API (Ctrl-C stops it). The command
    ships in the api pack (``extensions.register_cli_command``); without the pack a
    stub here says how to install it.
  - ``lhassa beat``: fire one heartbeat tick (what the systemd timer runs).
  - ``lhassa watch``: periodically re-render the latest activity (Ctrl-C stops).

Everything is offline/LLM-free except ``serve`` (which boots the API process).
Data goes to stdout (``--json`` where a command returns data); the themed chrome
and diagnostics go to stderr, so piped output stays clean. They are attached to the
main Typer ``app`` via :func:`register`: see ``lhassa.cli.app``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from lhassa import legal as _legal
from lhassa.cli.theme import LHASSA_THEME, PANEL_KWARGS

_ON = "✓"
_OFF = "–"


def _out() -> Console:
    return Console(theme=LHASSA_THEME)


def _err() -> Console:
    return Console(stderr=True, theme=LHASSA_THEME)


def _dump_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


# ── status / overview ────────────────────────────────────────────────────────────

def load_overview() -> dict:
    """Build the dashboard-home aggregate in-process (the ``/api/overview`` data).

    The read-model lives in core (:mod:`lhassa.views.overview`), so ``status``
    works without the api pack; it is imported lazily to keep the CLI's quiet
    boot path light.
    """
    from lhassa.views.overview import build_overview

    return asyncio.run(build_overview())


def _flag(value: bool) -> str:
    return _ON if value else _OFF


def render_overview(data: dict) -> str:
    """A clean, themed, one-screen summary of :func:`load_overview` as Rich markup."""
    brains = data.get("brains", {}) or {}
    skills = data.get("skills", {}) or {}
    approvals = data.get("approvals", {}) or {}
    cost = data.get("cost", {}) or {}
    health = data.get("health", {}) or {}
    integrity = data.get("integrity", {}) or {}
    activity = data.get("activity", {}) or {}
    tools = data.get("tools", {}) or {}
    errors = data.get("errors", {}) or {}

    lines: list[str] = []

    shape = "standalone" if brains.get("standalone") else "org"
    lines.append(
        f"  [title]Brains[/]     [value]{brains.get('count', 0)}[/] [dim]·[/] "
        f"admin [model]{escape(str(brains.get('admin') or '—'))}[/] [dim]·[/] {shape}"
    )
    lines.append(
        f"  [title]Skills[/]     [value]{skills.get('installed', 0)}[/] installed "
        f"[dim]·[/] [value]{skills.get('recommended', 0)}[/] recommended"
    )

    pending = approvals.get("pending", 0)
    style = "warn" if pending else "success"
    lines.append(f"  [title]Approvals[/]  [{style}]{pending}[/] pending")
    for item in (approvals.get("items") or [])[:6]:
        lines.append(
            f"      [dim]·[/] [accent]{escape(str(item.get('id') or '?'))}[/] "
            f"[dim]{escape(str(item.get('tipo') or '—'))}[/] "
            f"{escape(str(item.get('summary') or '—'))}"
        )

    ks = "ON" if cost.get("kill_switch") else "off"
    lines.append(
        f"  [title]Cost today[/] [cost]${float(cost.get('today_cost_usd', 0.0)):.4f}[/] "
        f"[dim]·[/] budget [value]{cost.get('daily_token_budget', 0)}[/] tok "
        f"[dim]·[/] kill-switch [accent]{ks}[/]"
    )

    worst = str(health.get("worst_level") or "info")
    hstyle = "success" if health.get("ok") else "warn"
    lines.append(f"  [title]Health[/]     [{hstyle}]{worst}[/] [dim]·[/] ok={_flag(bool(health.get('ok')))}")
    istyle = "success" if integrity.get("valid") else "warn"
    lines.append(
        f"  [title]Integrity[/]  [{istyle}]"
        f"{escape(str(integrity.get('status') or 'unattested'))}[/] "
        f"[dim]·[/] {_flag(bool(integrity.get('valid')))} "
        f"[dim]{escape(str(integrity.get('reason') or ''))}[/]"
    )

    by_estado = tools.get("by_estado") or {}
    estado_str = ", ".join(f"{k}={v}" for k, v in by_estado.items()) or "—"
    lines.append(f"  [title]Tools[/]      [value]{tools.get('count', 0)}[/] [dim]· {escape(estado_str)}[/]")

    lines.append("  [title]Activity[/]")
    recent = activity.get("recent") or []
    if not recent:
        lines.append("      [dim](no episodes yet)[/]")
    for ep in recent[:8]:
        trigger = escape(str(ep.get("despertar_por") or "?"))
        decision = escape(str(ep.get("decision") or "—"))
        resultado = escape(str(ep.get("resultado") or "")[:80])
        lines.append(f"      [dim]·[/] [model]{trigger}[/] [dim]{decision}[/] {resultado}")

    if errors:
        lines.append("")
        lines.append("  [warn]Degraded:[/] " + ", ".join(escape(k) for k in errors))

    body = "\n".join(lines)
    return Panel(
        body,
        title="[brand]ᕮ Lhassa[/] [dim]overview[/]",
        subtitle="[dim]brains · spend · approvals · activity[/]",
        **PANEL_KWARGS,
    )


def status_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the full overview aggregate as one JSON document."
    ),
) -> None:
    """The whole brain at a glance (alias ``overview``): brains, skills, pending
    approvals, today's cost, health and recent activity. Offline + LLM-free."""
    data = load_overview()
    if json_out:
        _dump_json(data)
        return
    _out().print(render_overview(data))


# ── version ──────────────────────────────────────────────────────────────────────

def version_info() -> dict:
    """The package version + runtime + a few key on/off toggles (never raises)."""
    import importlib.metadata as md
    import platform

    try:
        version = md.version("lhassa")
    except Exception:
        version = "0+unknown"

    from lhassa.config import Config

    config = Config()
    from lhassa import legal as _lg

    return {
        "version": version,
        "python": platform.python_version(),
        "instance_id": Config.instance_id,
        "legal": _lg.notice_dict(),
        "toggles": {
            "voice": bool(getattr(config, "voice_enabled", False)),
            "memory_layer4": bool(getattr(config, "memory_layer4_enabled", False)),
            "llm_kill_switch": bool(getattr(config, "llm_kill_switch", False)),
            "telegram_poll": bool(getattr(config, "telegram_poll_enabled", False)),
        },
    }


def version_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the version info as JSON."),
) -> None:
    """Print Lhassa's version, the Python runtime, and a few key toggles."""
    info = version_info()
    if json_out:
        _dump_json(info)
        return
    out = _out()
    toggles = info["toggles"]
    flags = " ".join(f"{k}={_flag(v)}" for k, v in toggles.items())
    out.print(
        Panel(
            f"[title]Version[/] [value]{info['version']}[/] [dim]· Python {info['python']}[/]\n"
            f"[dim]instance[/] [model]{escape(str(info['instance_id']))}[/]\n"
            f"[dim]toggles[/] {flags}\n"
            # Appropriate Legal Notice; required by the AGPL-3.0 section 7(b)
            # term in LICENSE. Rendered from lhassa.legal, asserted by
            # tests/test_legal_notice.py; do not inline or remove it.
            f"[dim]{escape(_legal.NOTICE.splitlines()[0])}[/]\n"
            f"[dim]{escape(_legal.NOTICE.splitlines()[1])}[/]",
            title="[brand]Lhassa[/]",
            **PANEL_KWARGS,
        )
    )


# ── serve (shipped by the api pack) ───────────────────────────────────────────────

API_PACK_HINT = "uv sync --package lhassa-api"


def serve_absent_cmd() -> None:
    """Run the dashboard + REST API (needs the api pack, lhassa-api)."""
    _err().print(
        "[error]✗[/] the dashboard + API ship in the api pack (lhassa-api), which is "
        f"not installed. Install it with: [model]{API_PACK_HINT}[/]"
    )
    raise typer.Exit(code=1)


# ── beat (one heartbeat tick) ─────────────────────────────────────────────────────

def run_beat() -> dict:
    """Fire one heartbeat tick and return ``step``'s summary (LLM-free, $0)."""
    from lhassa.core import heartbeat

    return asyncio.run(heartbeat.beat())


def beat_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the beat summary as JSON."),
) -> None:
    """Run one heartbeat tick; exactly what the systemd timer runs. Deterministic,
    $0; advances the brain one step and prints the summary."""
    summary = run_beat()
    if json_out:
        _dump_json(summary)
        return
    out = _out()
    triggers = summary.get("triggers", 0)
    if not triggers:
        out.print("[success]✓[/] [dim]heartbeat: nothing to do (all quiet).[/]")
        return
    parts = ", ".join(f"{k}={v}" for k, v in summary.items())
    out.print(f"[success]✓[/] heartbeat: [value]{escape(parts)}[/]")


# ── watch (tail recent activity) ──────────────────────────────────────────────────

async def _recent_episodes(n: int) -> list[dict]:
    from lhassa.config import Config
    from lhassa.core import db

    await db.init_db()
    return await db.load_recent_episodes(Config.instance_id, n)


def render_activity(rows: list[dict]) -> str:
    """A themed block of the latest episodes (newest first) as Rich markup."""
    lines = []
    if not rows:
        lines.append("  [dim](no episodes yet)[/]")
        return Panel(
            "\n".join(lines),
            title=f"[brand]ᕮ Lhassa[/] [dim]activity {time.strftime('%H:%M:%S')}[/]",
            **PANEL_KWARGS,
        )
    for ep in rows:
        when = escape(str(ep.get("creado_en") or "")[:19])
        trigger = escape(str(ep.get("despertar_por") or "?"))
        decision = escape(str(ep.get("decision") or "—"))
        resultado = escape(str(ep.get("resultado") or "")[:80])
        lines.append(f"  [dim]{when}[/] [model]{trigger}[/] [dim]{decision}[/] {resultado}")
    return Panel(
        "\n".join(lines),
        title=f"[brand]ᕮ Lhassa[/] [dim]activity {time.strftime('%H:%M:%S')}[/]",
        **PANEL_KWARGS,
    )


def watch_cmd(
    once: bool = typer.Option(
        False, "--once", help="Render a single frame and exit (no loop)."
    ),
    interval: float = typer.Option(3.0, "--interval", help="Seconds between refreshes."),
    n: int = typer.Option(12, "--n", help="How many recent episodes to show."),
) -> None:
    """Tail the brain's recent activity; a periodic re-render of the latest
    episodes (Ctrl-C to stop). ``--once`` renders a single frame and exits."""
    out = _out()
    try:
        while True:
            rows = asyncio.run(_recent_episodes(n))
            if out.is_terminal and not once:
                out.clear()
            out.print(render_activity(rows))
            if once:
                return
            time.sleep(max(interval, 0.2))
    except KeyboardInterrupt:
        _err().print("[dim]stopped.[/]")


# ── REPL helpers (used by app.py's _repl slash-commands) ──────────────────────────

def overview_text() -> str:
    """The overview summary as Rich markup; for the REPL's ``/status``."""
    return render_overview(load_overview())


def skills_text() -> str:
    """A quick installed-skills list as Rich markup; for the REPL's ``/skills``."""
    from lhassa.config import Config
    from lhassa.skills.store import SkillStore

    skills = SkillStore(Config).list_skills()
    if not skills:
        return "[dim]No skills installed. Try [/][model]lhassa skills[/][dim].[/]"
    lines = ["[title]Skills[/]"]
    for s in skills:
        name = escape(str(getattr(s, "name", "") or "?"))
        desc = escape(str(getattr(s, "description", "") or "")[:60])
        lines.append(f"  [accent]{name}[/] [dim]{desc}[/]")
    return "\n".join(lines)


def tools_text() -> str:
    """A quick self-written-tools list as Rich markup; for the REPL's ``/tools``."""
    from lhassa.config import Config
    from lhassa.core import db

    async def _load() -> list[dict]:
        await db.init_db()
        return await db.list_tools(Config.instance_id)

    rows = asyncio.run(_load())
    if not rows:
        return "[dim]No self-written tools yet.[/]"
    lines = ["[title]Tools[/]"]
    for r in rows:
        name = escape(str(r.get("nombre") or "?"))
        estado = escape(str(r.get("estado") or "—"))
        dominio = escape(str(r.get("dominio") or "—"))
        lines.append(f"  [accent]{name}[/] [dim]{dominio} · {estado}[/]")
    return "\n".join(lines)


# ── registration ──────────────────────────────────────────────────────────────────

# ── code (run an agentic task through a local AI CLI) ─────────────────────────────

def code_cmd(
    task: "list[str]" = typer.Argument(..., metavar="TASK", help="The coding task to run."),
    cli: str = typer.Option(
        None, "--cli", "-c", help="Which CLI: claude | codex | opencode (auto-detected if omitted)."
    ),
) -> None:
    """Run an AGENTIC coding task through a local AI CLI (claude / codex / opencode).

    Unlike ``lhassa "…"`` (a chat that just answers), this hands the task to the real
    agentic CLI; it can read/edit files and run tools in the CURRENT directory; using
    the owner's subscription (no API key), and prints its output.
    """
    import os

    from lhassa.agents.local_cli import is_installed, make_agent

    task_text = " ".join(task).strip()
    if not task_text:
        _err().print('[error]✗[/] give a task, e.g. [model]lhassa code "add a test for utils.py"[/]')
        raise typer.Exit(code=1)
    # Stream the CLI's output LIVE, line by line, straight to stdout as it works.
    streamed = {"any": False}

    def _emit(line: str) -> None:
        streamed["any"] = True
        sys.stdout.write(line)
        sys.stdout.flush()

    agent = make_agent(cli, on_line=_emit)
    if not is_installed(agent.cli):
        _err().print(
            f"[error]✗[/] the [model]{escape(agent.cli)}[/] CLI is not installed "
            "(claude / codex / opencode)."
        )
        raise typer.Exit(code=1)
    _err().print(
        f"[dim]running via[/] [model]{escape(agent.cli)}[/] [dim]in {escape(os.getcwd())} …[/]"
    )
    try:
        result = asyncio.run(agent.run(task_text))
    except KeyboardInterrupt:
        _err().print("\n[dim]stopped.[/]")
        raise typer.Exit(code=130)
    if streamed["any"]:
        sys.stdout.write("\n")  # tidy trailing newline after the live stream
        sys.stdout.flush()
    elif result:  # nothing streamed (e.g. an error string came back); show it
        _out().print(result)


def _register_serve_fallback(app: "typer.Typer") -> None:
    # The api pack mounts the real ``serve`` (cli/app.py's CLI_COMMANDS loop);
    # only when no pack provides it does core attach the "not installed" stub.
    from lhassa import actionpack
    from lhassa.extensions import CLI_COMMANDS

    actionpack.register_all()
    if "serve" not in CLI_COMMANDS:
        app.command("serve")(serve_absent_cmd)


def register(app: "typer.Typer") -> None:
    """Attach the action commands to the main Typer app."""
    app.command("status")(status_cmd)
    app.command("overview")(status_cmd)
    app.command("version")(version_cmd)
    _register_serve_fallback(app)
    app.command("beat")(beat_cmd)
    app.command("watch")(watch_cmd)
    app.command("code")(code_cmd)
