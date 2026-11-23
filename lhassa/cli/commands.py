# SPDX-License-Identifier: AGPL-3.0-or-later
"""Power subcommands for the Lhassa CLI.

These operate **offline** against the local brain DB (``lhassa.core.db``) and the
LLM gateway ledger; no LLM call, no network (except ``report``'s optional
cross-harness ccusage read, which is itself fully defensive). Each command runs its
async work through ``asyncio.run`` and supports ``--json`` for machine-readable
output. Data goes to stdout; diagnostics / errors go to stderr (so ``--json`` stays
clean when piped).

They are registered onto the main Typer ``app`` via :func:`register`, alongside the
default chat/REPL behavior; see ``lhassa.cli.app``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lhassa.cli.theme import LHASSA_THEME, PANEL_KWARGS, TABLE_KWARGS


def _out() -> Console:
    return Console(theme=LHASSA_THEME)


def _err() -> Console:
    return Console(stderr=True, theme=LHASSA_THEME)


def _dump_json(obj) -> None:
    """Write one JSON document to stdout (mirrors the chat path's --json style)."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _instance_id() -> str:
    from lhassa.config import Config

    return Config.instance_id


# ── tools ───────────────────────────────────────────────────────────────────────

async def _load_tools() -> list[dict]:
    from lhassa.core import db

    await db.init_db()
    rows = await db.list_tools(_instance_id())
    return [
        {
            "nombre": r.get("nombre"),
            "dominio": r.get("dominio"),
            "estado": r.get("estado"),
            "metrica_actual": r.get("metrica_actual"),
            "usos": r.get("usos") or 0,
            "fallos": r.get("fallos") or 0,
        }
        for r in rows
    ]


def tools_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the tool rows as JSON."),
) -> None:
    """List the self-written tools and their gate/usage state."""
    rows = asyncio.run(_load_tools())
    if json_out:
        _dump_json(rows)
        return
    if not rows:
        _err().print("No tools yet.", style="dim")
        return
    table = Table(title="Tools", **TABLE_KWARGS)
    for col in ("nombre", "dominio", "estado", "metric", "usos", "fallos"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["nombre"]),
            str(r["dominio"] or "—"),
            str(r["estado"] or "—"),
            "—" if r["metrica_actual"] is None else str(r["metrica_actual"]),
            str(r["usos"]),
            str(r["fallos"]),
        )
    _out().print(table)


# ── approvals ───────────────────────────────────────────────────────────────────

async def _load_approvals() -> list[dict]:
    from lhassa.core import db

    await db.init_db()
    rows = await db.load_pending_approvals(_instance_id())
    out = []
    for r in rows:
        payload = r.get("payload") or {}
        out.append(
            {
                "id": r.get("id"),
                "tipo": r.get("tipo"),
                "accion": payload.get("accion"),
                "actor_id": payload.get("actor_id"),
                "creado_en": r.get("creado_en"),
                # Kept whole: ``security.approval.describe`` reads the payload to
                # say WHAT is being approved, and this projection used to drop
                # it, which is why the queue could only ever show a bare action
                # name. The --json callers get the fuller row too.
                "payload": payload,
            }
        )
    return out


def approvals_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the queue as JSON."),
) -> None:
    """List the pending approval queue (oldest first)."""
    rows = asyncio.run(_load_approvals())
    if json_out:
        _dump_json(rows)
        return
    if not rows:
        _err().print("No pending approvals.", style="dim")
        return
    from lhassa.security import approval as _approval

    table = Table(title="Pending approvals", **TABLE_KWARGS)
    table.add_column("id")
    table.add_column("what", no_wrap=True, overflow="ellipsis", max_width=52)
    table.add_column("tipo")
    table.add_column("actor_id")
    table.add_column("creado_en")
    for r in rows:
        # ``describe`` is the ONE place that knows each payload shape; the
        # dashboard, the digest and Telegram all render through it. This table
        # printed the bare ``accion`` instead, so ten proposals to retire ten
        # different capabilities were ten identical rows of "retire_capabi...".
        try:
            described = _approval.describe(r)
            what = str(described.get("title") or "") or str(r["accion"] or "")
        except Exception:  # a display helper must never break the queue
            what = str(r["accion"] or "")
        table.add_row(
            str(r["id"]),
            what or "-",
            str(r["tipo"] or "-"),
            str(r["actor_id"] or "-"),
            str(r["creado_en"] or "-"),
        )
    _out().print(table)


async def _resolve(approval_id: str, approved: bool, by_actor: str) -> None:
    from lhassa.core import db
    from lhassa.security import approval

    await db.init_db()
    try:
        await approval.resolve(
            _instance_id(),
            approval_id,
            approved=approved,
            by_actor=by_actor,
            approver=approval.TrustedApprover("owner", "local-process"),
        )
    except KeyError:
        raise typer.BadParameter(f"no approval {approval_id!r}") from None
    except (
        approval.ApprovalResolutionConflict,
        approval.ApprovalAuthorizationError,
    ) as exc:
        raise typer.BadParameter(str(exc)) from None


def approve_cmd(
    approval_id: str = typer.Argument(..., help="The approval id to approve."),
    by_actor: str = typer.Option(
        "duenio", "--by", help="Audit label (CLI identity remains authoritative)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Approve a pending approval (feeds the autonomy knob)."""
    asyncio.run(_resolve(approval_id, True, by_actor))
    if json_out:
        _dump_json({"approval_id": approval_id, "approved": True})
    else:
        _out().print(f"[success]✓[/] approved [model]{approval_id}[/]")


def deny_cmd(
    approval_id: str = typer.Argument(..., help="The approval id to deny."),
    by_actor: str = typer.Option(
        "duenio", "--by", help="Audit label (CLI identity remains authoritative)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Deny a pending approval (resets its autonomy streak)."""
    asyncio.run(_resolve(approval_id, False, by_actor))
    if json_out:
        _dump_json({"approval_id": approval_id, "approved": False})
    else:
        _out().print(f"[warn]–[/] denied [model]{approval_id}[/]")


# ── report (cost / usage over a window) ──────────────────────────────────────────

async def _report(days: int) -> dict:
    from lhassa.llm.ccusage import read_ccusage
    from lhassa.llm.gateway import gateway

    rows = await gateway.ledger_rows()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    window = [r for r in rows if str(r.get("ts") or "") >= cutoff]

    by_model: dict[str, dict] = {}
    total_cost = 0.0
    total_in = 0
    total_out = 0
    for r in window:
        model = str(r.get("model") or "?")
        cost = float(r.get("cost_usd") or 0.0)
        in_t = int(r.get("input_tokens") or 0)
        out_t = int(r.get("output_tokens") or 0)
        bucket = by_model.setdefault(
            model, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0}
        )
        bucket["cost_usd"] += cost
        bucket["input_tokens"] += in_t
        bucket["output_tokens"] += out_t
        bucket["calls"] += 1
        total_cost += cost
        total_in += in_t
        total_out += out_t

    for bucket in by_model.values():
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)

    ccusage = await read_ccusage()

    return {
        "window_days": days,
        "calls": len(window),
        "total_cost_usd": round(total_cost, 6),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "by_model": by_model,
        "ccusage": ccusage,
    }


def report_cmd(
    days: int = typer.Option(7, "--days", help="Window in days (default 7)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """A cost/usage summary for a recent window (gateway ledger + ccusage)."""
    data = asyncio.run(_report(days))
    if json_out:
        _dump_json(data)
        return
    out = _out()
    spent = float(data["total_cost_usd"] or 0.0)
    cost_line = (
        "I spent $0.0000 in this window. The quiet path held."
        if spent <= 0
        else f"I spent ${spent:.4f} in this window. Every think is on the ledger."
    )
    out.print(
        Panel(
            f"[title]Last {data['window_days']}d[/]\n"
            f"{cost_line}\n"
            f"[value]{data['calls']}[/] think(s) [dim]·[/] "
            f"[tokens]{data['input_tokens']}+{data['output_tokens']} tokens[/] [dim]·[/] "
            f"[cost]${spent:.4f}[/]",
            title="[brand]Lhassa ledger[/]",
            **PANEL_KWARGS,
        )
    )
    if data["by_model"]:
        table = Table(title="By model", **TABLE_KWARGS)
        for col in ("model", "calls", "in", "out", "cost_usd"):
            table.add_column(col)
        for model, b in data["by_model"].items():
            table.add_row(
                model,
                str(b["calls"]),
                str(b["input_tokens"]),
                str(b["output_tokens"]),
                f"{b['cost_usd']:.4f}",
            )
        out.print(table)
    ccu = data.get("ccusage") or {}
    if ccu.get("available"):
        out.print(
            f"Cross-harness (ccusage): ${ccu.get('total_cost_usd', 0.0):.4f}", style="dim"
        )


# ── memory (fact search) ─────────────────────────────────────────────────────────

async def _search_memory(query: str) -> list[dict]:
    from lhassa.memory import facts

    resolved = facts.resolve(await facts.load_facts(_instance_id()))
    q = query.strip().lower()
    matches = []
    for f in resolved:
        if not q or q in f.contenido.lower() or any(q in t.lower() for t in f.tags):
            matches.append(
                {
                    "id": f.id,
                    "tipo": f.tipo,
                    "contenido": f.contenido,
                    "tags": f.tags,
                    "pinned": f.pinned,
                    "confianza": f.confianza,
                }
            )
    return matches


def memory_cmd(
    query: str = typer.Argument(..., help="Substring / tag to search facts for."),
    json_out: bool = typer.Option(False, "--json", help="Emit the matches as JSON."),
) -> None:
    """Search the durable facts (substring or tag match)."""
    rows = asyncio.run(_search_memory(query))
    if json_out:
        _dump_json(rows)
        return
    if not rows:
        _err().print("No matching facts.", style="dim")
        return
    table = Table(title=f"Facts matching {query!r}", **TABLE_KWARGS)
    for col in ("tipo", "contenido", "tags", "conf"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["tipo"]),
            str(r["contenido"]),
            ", ".join(r["tags"]) or "—",
            f"{r['confianza']:.2f}",
        )
    _out().print(table)


# ── fleet (known machines + check-in / desired_state) ────────────────────────────

async def _load_fleet() -> list[dict]:
    from lhassa._vendor import aiosqlite

    from lhassa.core import db

    await db.init_db()
    inst = _instance_id()
    machines: dict[str, dict] = {}
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT despertar_por AS dp, MAX(creado_en) AS last
               FROM episodes
               WHERE instance_id = ? AND despertar_por LIKE 'checkin:%'
               GROUP BY despertar_por""",
            (inst,),
        ) as cur:
            for row in await cur.fetchall():
                mid = str(row["dp"]).split("checkin:", 1)[1]
                machines.setdefault(mid, {})["last_checkin"] = row["last"]
        async with conn.execute(
            "SELECT DISTINCT machine_id FROM desired_state WHERE instance_id = ?",
            (inst,),
        ) as cur:
            for row in await cur.fetchall():
                machines.setdefault(row["machine_id"], {})

    out = []
    for mid in sorted(machines):
        ds = await db.get_desired_state(inst, mid)
        out.append(
            {
                "machine_id": mid,
                "last_checkin": machines[mid].get("last_checkin"),
                "desired_state": ds,
            }
        )
    return out


def fleet_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the fleet as JSON."),
) -> None:
    """List known machines with their last check-in and desired state."""
    rows = asyncio.run(_load_fleet())
    if json_out:
        _dump_json(rows)
        return
    if not rows:
        _err().print("No machines known yet.", style="dim")
        return
    table = Table(title="Fleet", **TABLE_KWARGS)
    for col in ("machine_id", "last_checkin", "desired_state"):
        table.add_column(col)
    for r in rows:
        ds = r["desired_state"] or {}
        ds_str = ", ".join(f"{k}={v}" for k, v in ds.items()) or "—"
        table.add_row(str(r["machine_id"]), str(r["last_checkin"] or "—"), ds_str)
    _out().print(table)


# ── registration ─────────────────────────────────────────────────────────────────

def register(app: "typer.Typer") -> None:
    """Attach the power subcommands to the main Typer app.

    ``tools`` is intentionally NOT registered here: it is now the ``tools`` Typer
    GROUP in :mod:`lhassa.cli.tools_cmds` (list + ``propose``), which reuses
    ``tools_cmd`` for its bare/``--json`` form; see that module's callback.

    ``memory`` is likewise NOT registered here: it is now the ``memory`` Typer
    GROUP in :mod:`lhassa.cli.memory_cmds` (search + ``add``), which reuses
    ``memory_cmd`` (below) as its hidden default ``search`` subcommand.
    """
    app.command("approvals")(approvals_cmd)
    app.command("approve")(approve_cmd)
    app.command("deny")(deny_cmd)
    app.command("report")(report_cmd)
    app.command("fleet")(fleet_cmd)
