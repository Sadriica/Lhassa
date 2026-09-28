# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa share``: cross-instance resource sharing (Stream 6 Phase A).

An OFFLINE, $0 management surface over :mod:`lhassa.org.sharing`: publish or
revoke one of this instance's own skills/flows/templates/knowledge, grant or
revoke another instance's discovery permission, and list offerings/
subscriptions. Memory (facts/episodes) and identity are never reachable here;
the closed shareable-type vocabulary lives in
:data:`lhassa.org.sharing.SHAREABLE_TYPES` and this CLI is a thin wrapper over
it, so it inherits the same deny-by-default guarantees.
"""

from __future__ import annotations

import asyncio
import sys

import typer
from rich.console import Console
from rich.table import Table

from lhassa.cli.theme import LHASSA_THEME, TABLE_KWARGS

share_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Publish/subscribe shared resources (skills/flows/templates/knowledge) across instances.",
)
_console = Console(theme=LHASSA_THEME)
_err = Console(stderr=True)


def _config():
    from lhassa.config import Config

    return Config()


def _actor(actor: "str | None") -> str:
    from lhassa.config import Config

    return (actor or "").strip() or Config.root_admin_id


@share_app.command("publish")
def publish_cmd(
    resource_type: str = typer.Argument(..., help="skill | flow | template | knowledge"),
    resource_name: str = typer.Argument(...),
    actor: str = typer.Option(None, "--actor", help="Who is publishing (default: the owner)."),
) -> None:
    """Publish one of this instance's own resources for other instances to find."""
    from lhassa.org import sharing

    result = asyncio.run(
        sharing.publish(resource_type, resource_name, actor_id=_actor(actor), config=_config())
    )
    if not result["ok"]:
        _err.print(f"[warn]refused:[/warn] {result['reason']}")
        raise typer.Exit(1)
    _console.print(f"[ok]published[/ok] {resource_type}/{resource_name}")


@share_app.command("unpublish")
def unpublish_cmd(
    resource_type: str = typer.Argument(...),
    resource_name: str = typer.Argument(...),
    actor: str = typer.Option(None, "--actor"),
) -> None:
    """Revoke a previously published offering (idempotent)."""
    from lhassa.org import sharing

    result = asyncio.run(
        sharing.unpublish(resource_type, resource_name, actor_id=_actor(actor), config=_config())
    )
    _console.print(
        f"[ok]revoked[/ok] {resource_type}/{resource_name}"
        if result.get("revoked")
        else f"{resource_type}/{resource_name} was not published"
    )


@share_app.command("grant")
def grant_cmd(
    instance_id: str = typer.Argument(..., help="The remote instance id to grant access to."),
    note: str = typer.Option("", "--note"),
    actor: str = typer.Option(None, "--actor"),
) -> None:
    """Grant a remote instance permission to discover/subscribe to this instance's offerings."""
    from lhassa.org import sharing

    result = asyncio.run(
        sharing.grant(instance_id, actor_id=_actor(actor), note=note, config=_config())
    )
    if not result["ok"]:
        _err.print(f"[warn]refused:[/warn] {result['reason']}")
        raise typer.Exit(1)
    _console.print(f"[ok]granted[/ok] {instance_id} may now discover this instance's offerings")


@share_app.command("revoke")
def revoke_cmd(
    instance_id: str = typer.Argument(...),
    actor: str = typer.Option(None, "--actor"),
) -> None:
    """Revoke a previously granted instance's access."""
    from lhassa.org import sharing

    asyncio.run(sharing.revoke_grant(instance_id, actor_id=_actor(actor), config=_config()))
    _console.print(f"[ok]revoked[/ok] {instance_id}'s access")


@share_app.command("offerings")
def offerings_cmd(
    instance_id: str = typer.Argument(..., help="Whose offerings to list (an instance id)."),
    actor: str = typer.Option(None, "--actor"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List a (possibly remote) instance's published offerings.

    Refused with a clear reason if this instance was never granted a SHARES
    permit (deny-by-default); asking for your own instance's offerings never
    needs a grant.
    """
    from lhassa.org import sharing

    cfg = _config()
    result = asyncio.run(
        sharing.list_offerings(
            instance_id,
            requester_instance_id=cfg.instance_id,
            actor_id=_actor(actor),
            config=cfg,
        )
    )
    if not result["ok"]:
        _err.print(f"[warn]refused:[/warn] {result['reason']}")
        raise typer.Exit(1)
    offerings = result["offerings"]
    if as_json:
        import json

        sys.stdout.write(json.dumps(offerings, ensure_ascii=False, default=str) + "\n")
        return
    if not offerings:
        _console.print(f"{instance_id} has no live offerings.")
        return
    table = Table(title=f"{instance_id}'s offerings", **TABLE_KWARGS)
    table.add_column("type")
    table.add_column("name")
    table.add_column("published by")
    table.add_column("published at")
    for row in offerings:
        table.add_row(
            row["resource_type"], row["resource_name"], row.get("actor_id") or "",
            row.get("published_at") or "",
        )
    _console.print(table)


@share_app.command("subscribe")
def subscribe_cmd(
    instance_id: str = typer.Argument(..., help="The instance offering the resource."),
    resource_type: str = typer.Argument(...),
    resource_name: str = typer.Argument(...),
    actor: str = typer.Option(None, "--actor"),
) -> None:
    """Subscribe this instance to a resource published by ``instance_id``."""
    from lhassa.org import sharing

    result = asyncio.run(
        sharing.subscribe(
            instance_id, resource_type, resource_name, actor_id=_actor(actor), config=_config()
        )
    )
    if not result["ok"]:
        _err.print(f"[warn]refused:[/warn] {result['reason']}")
        raise typer.Exit(1)
    _console.print(f"[ok]subscribed[/ok] to {instance_id}/{resource_type}/{resource_name}")


@share_app.command("unsubscribe")
def unsubscribe_cmd(
    instance_id: str = typer.Argument(...),
    resource_type: str = typer.Argument(...),
    resource_name: str = typer.Argument(...),
    actor: str = typer.Option(None, "--actor"),
) -> None:
    """Unsubscribe from a resource (idempotent)."""
    from lhassa.org import sharing

    asyncio.run(
        sharing.unsubscribe(
            instance_id, resource_type, resource_name, actor_id=_actor(actor), config=_config()
        )
    )
    _console.print(f"[ok]unsubscribed[/ok] from {instance_id}/{resource_type}/{resource_name}")


@share_app.command("subscriptions")
def subscriptions_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """List this instance's own live subscriptions."""
    from lhassa.org import sharing

    subs = asyncio.run(sharing.list_subscriptions(config=_config()))
    if as_json:
        import json

        sys.stdout.write(json.dumps(subs, ensure_ascii=False, default=str) + "\n")
        return
    if not subs:
        _console.print("No active subscriptions.")
        return
    table = Table(title="Subscriptions", **TABLE_KWARGS)
    table.add_column("from instance")
    table.add_column("type")
    table.add_column("name")
    table.add_column("subscribed at")
    for row in subs:
        table.add_row(
            row["source_instance_id"], row["resource_type"], row["resource_name"],
            row.get("subscribed_at") or "",
        )
    _console.print(table)
