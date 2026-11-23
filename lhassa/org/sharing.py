# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-instance sharing. Stream 6 Phase A ("one brain per person, shared resources").

Lhassa's org layer already models 1..N brains WITHIN one instance
(:mod:`lhassa.org.topology`) plus a typed overlay of relationships BETWEEN them
(:mod:`lhassa.org.relationships`). This module is the one level up: 1..N
INSTANCES (one brain per person), each sovereign, optionally sharing a small,
CLOSED set of resources (skills, flows, templates, knowledge) while memory
(facts, episodes) and identity stay private BY CONSTRUCTION (there is no
column or code path in this module that can name a fact or an episode as a
shareable resource; see the ``shared_resources`` table in
:mod:`lhassa.core.db`).

The gate is the SAME seam every other org permission question goes through:
:func:`lhassa.org.relationships.permits`, extended with one new directed,
INSTANCE-scoped kind, :data:`lhassa.org.relationships.SHARES`
("A may discover/subscribe to B's shared resources"). Nothing here bypasses
it; every read or write that crosses an instance boundary calls
:func:`_check_permit` first, deny-by-default: no edge, no access, and that
denial is itself a logged, $0 episode (never a silent drop).

Every cross-instance hop logs its own Episode (the standing hierarchy
principle: "every brain in a chain logs its own hop", extended here to every
brain-to-INSTANCE hop), attributed ``dominio=Config.root_admin_id``: sharing
is an act the root administrator performs on behalf of the instance, mirroring
how every other org-wide governance op (topology edits, relationship edits) is
attributed at the root. Trust is never promoted by a subscription: content
pulled from another instance is exactly as trusted as content from any other
external source (MUNDO); callers that turn a subscribed resource into an
action must apply :func:`lhassa.security.trust.degrade` themselves, same as
any other MUNDO input.

Everything here is $0, offline (no network; a real multi-machine transport is
future work; today this operates over one shared ``core.db`` DB_PATH holding
multiple instances' rows, or is invoked identically once each instance grows
its own DB and a sync/transport layer is added later). PRODUCT DECISION (Phase
A, no live transport exists yet to arbitrate otherwise): the SHARES grant
registry (``config.org_relationships_file``) is likewise a file SHARED across
every instance on one box; a grant instance A writes naming instance B must
be readable when B asks "am I permitted?", which today means the same
physical relationships file, exactly like the shared DB_PATH already implies.
Once instances run on separate machines this becomes a real question
(replicate the grant, or have the requester ask the provider directly over a
future transport); flagged here rather than decided silently. NEVER raises to a
caller; every function returns a small result dict with an explicit
``ok``/``reason``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import db
from lhassa.core.episode import Episode
from lhassa.org import relationships as rel

if TYPE_CHECKING:
    pass

#: The closed, deliberately small set of resource kinds that MAY cross an
#: instance boundary. Facts and episodes are never in this set; memory and
#: identity stay private by default (Stream 6 Phase A decision).
SHAREABLE_TYPES: frozenset[str] = frozenset({"skill", "flow", "template", "knowledge"})

DESPERTAR_SHARE = "cross-instance-share"


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def _local_instance_id(config: "Optional[Config]") -> str:
    cfg = _resolve_config(config)
    return str(getattr(cfg, "instance_id", "") or "").strip()


async def _log_hop(
    *,
    dominio: str,
    accion: str,
    decision: str,
    resultado: str,
    instance_id: str,
) -> None:
    """One deterministic, $0, LLM-free episode for a cross-instance hop.

    Best-effort: an audit-log failure is logged and swallowed, never blocks the
    (already-decided) outcome it is recording.
    """
    try:
        await db.init_db()
        ep = Episode.begin(despertar_por=DESPERTAR_SHARE, instance_id=instance_id)
        ep.dominio = dominio
        ep.finish(decision=decision, accion=accion, resultado=resultado, costo_usd=0.0)
        await ep.append()
    except Exception as exc:  # observability must never block the work
        logger.debug("org.sharing: episode write failed for {}: {}", accion, exc)


def _validate_type(resource_type: str) -> "Optional[str]":
    """Return an error string if ``resource_type`` is not in the closed shareable
    set, else ``None``. The single choke point that keeps facts/episodes out."""
    resource_type = (resource_type or "").strip()
    if resource_type not in SHAREABLE_TYPES:
        return (
            f"{resource_type!r} is not shareable; only "
            f"{sorted(SHAREABLE_TYPES)} may cross an instance boundary"
        )
    return None


def _resource_exists(resource_type: str, resource_name: str, config: "Config") -> bool:
    """Best-effort local existence check for the named resource.

    Never raises; an existence check failing (e.g. a store misconfigured) is
    treated as "does not exist" rather than crashing the publish call; the
    caller sees a clear refusal instead of a 500.
    """
    try:
        if resource_type == "skill":
            from lhassa.skills.store import SkillStore

            return SkillStore(config).exists(resource_name)
        if resource_type == "flow":
            from lhassa.flows.store import FlowStore

            return FlowStore(config).get(resource_name) is not None
        if resource_type == "template":
            from lhassa.org import templates as templ

            try:
                templ.get_template(resource_name)
                return True
            except Exception:
                return False
        if resource_type == "knowledge":
            # Existence is checked at publish time against the live index via an
            # async DB read (see publish()); this sync helper is only used for
            # skill/flow/template, so knowledge always reports True here and the
            # real check happens in publish().
            return True
    except Exception as exc:
        logger.debug("org.sharing: existence check failed for {}/{}: {}", resource_type, resource_name, exc)
        return False
    return False


async def publish(
    resource_type: str,
    resource_name: str,
    *,
    actor_id: str,
    config: "Optional[Config]" = None,
) -> dict:
    """Publish one of THIS instance's resources for other instances to discover.

    Deny-by-default at the CONTENT level, not the permission level: publishing
    never requires a grant (an instance always controls its own offerings), but
    the resource_type must be in the closed :data:`SHAREABLE_TYPES` set and the
    named resource must actually exist locally; refuses (never crashes)
    otherwise. Logs one ``cross-instance-share`` episode either way.
    """
    cfg = _resolve_config(config)
    instance_id = _local_instance_id(cfg)
    resource_type = (resource_type or "").strip()
    resource_name = (resource_name or "").strip()
    actor_id = (actor_id or "").strip() or Config.root_admin_id

    err = _validate_type(resource_type)
    if err:
        await _log_hop(
            dominio=Config.root_admin_id,
            accion="publish_resource",
            decision=f"{actor_id} attempted to publish {resource_type}/{resource_name}",
            resultado=f"refused: {err}",
            instance_id=instance_id,
        )
        return {"ok": False, "reason": err}

    if resource_type == "knowledge":
        files = await db.list_knowledge_files(instance_id)
        exists = resource_name in files
    else:
        exists = _resource_exists(resource_type, resource_name, cfg)
    if not exists:
        reason = f"no local {resource_type} named {resource_name!r} to publish"
        await _log_hop(
            dominio=Config.root_admin_id,
            accion="publish_resource",
            decision=f"{actor_id} attempted to publish {resource_type}/{resource_name}",
            resultado=f"refused: {reason}",
            instance_id=instance_id,
        )
        return {"ok": False, "reason": reason}

    await db.init_db()
    await db.publish_resource(instance_id, resource_type, resource_name, actor_id=actor_id)
    await _log_hop(
        dominio=Config.root_admin_id,
        accion="publish_resource",
        decision=f"{actor_id} publishes {resource_type}/{resource_name}",
        resultado=f"published: {resource_type}/{resource_name}",
        instance_id=instance_id,
    )
    return {"ok": True, "resource_type": resource_type, "resource_name": resource_name}


async def unpublish(
    resource_type: str, resource_name: str, *, actor_id: str, config: "Optional[Config]" = None
) -> dict:
    """Revoke a published offering. Idempotent; logs its own hop."""
    cfg = _resolve_config(config)
    instance_id = _local_instance_id(cfg)
    actor_id = (actor_id or "").strip() or Config.root_admin_id
    await db.init_db()
    changed = await db.unpublish_resource(instance_id, resource_type, resource_name)
    await _log_hop(
        dominio=Config.root_admin_id,
        accion="unpublish_resource",
        decision=f"{actor_id} unpublishes {resource_type}/{resource_name}",
        resultado="revoked" if changed else "was not published",
        instance_id=instance_id,
    )
    return {"ok": True, "revoked": changed}


async def grant(
    requester_instance_id: str, *, actor_id: str, note: str = "", config: "Optional[Config]" = None
) -> dict:
    """Grant a remote instance permission to discover/subscribe to THIS instance's
    shared resources; adds a :data:`lhassa.org.relationships.SHARES` edge
    ``(requester_instance_id, local_instance_id)``. Trust is never promoted by a
    grant; it only OPENS a door the deny-by-default gate would otherwise refuse.
    """
    cfg = _resolve_config(config)
    instance_id = _local_instance_id(cfg)
    requester_instance_id = (requester_instance_id or "").strip()
    actor_id = (actor_id or "").strip() or Config.root_admin_id
    try:
        rel.add(requester_instance_id, instance_id, rel.SHARES, config=cfg, note=note)
    except ValueError as exc:
        await _log_hop(
            dominio=Config.root_admin_id,
            accion="grant_share",
            decision=f"{actor_id} attempted to grant {requester_instance_id} access",
            resultado=f"refused: {exc}",
            instance_id=instance_id,
        )
        return {"ok": False, "reason": str(exc)}
    await _log_hop(
        dominio=Config.root_admin_id,
        accion="grant_share",
        decision=f"{actor_id} grants {requester_instance_id} access to this instance's shared resources",
        resultado=f"granted: {requester_instance_id} → shares → {instance_id}",
        instance_id=instance_id,
    )
    return {"ok": True, "granted_to": requester_instance_id}


async def revoke_grant(
    requester_instance_id: str, *, actor_id: str, config: "Optional[Config]" = None
) -> dict:
    """Revoke a previously granted SHARES edge. Idempotent; logs its own hop."""
    cfg = _resolve_config(config)
    instance_id = _local_instance_id(cfg)
    requester_instance_id = (requester_instance_id or "").strip()
    actor_id = (actor_id or "").strip() or Config.root_admin_id
    rel.remove(requester_instance_id, instance_id, rel.SHARES, config=cfg)
    await _log_hop(
        dominio=Config.root_admin_id,
        accion="revoke_share",
        decision=f"{actor_id} revokes {requester_instance_id}'s access",
        resultado=f"revoked: {requester_instance_id} → shares → {instance_id}",
        instance_id=instance_id,
    )
    return {"ok": True, "revoked_from": requester_instance_id}


async def _check_permit(
    requester_instance_id: str, provider_instance_id: str, *, config: "Config"
) -> bool:
    """The deny-by-default gate: may ``requester_instance_id`` reach
    ``provider_instance_id``'s shared resources?

    Pure read over :func:`lhassa.org.relationships.permits`; an edge lookup
    failure (e.g. a corrupt relationships file) degrades to DENY, never allow;
    the fail-safe direction for a permission gate.
    """
    try:
        return rel.permits(requester_instance_id, provider_instance_id, rel.SHARES, config=config)
    except Exception as exc:
        logger.debug("org.sharing: permit check failed, denying: {}", exc)
        return False


async def list_offerings(
    provider_instance_id: str,
    *,
    requester_instance_id: str,
    actor_id: str,
    config: "Optional[Config]" = None,
) -> dict:
    """List what ``provider_instance_id`` offers, IF ``requester_instance_id`` is
    permitted (deny-by-default). Logs one hop either way (allow or refuse).

    A request for one's OWN offerings never needs a grant (you always see your
    own list); the permit check only guards a genuinely cross-instance read.
    """
    cfg = _resolve_config(config)
    local_id = _local_instance_id(cfg)
    provider_instance_id = (provider_instance_id or "").strip()
    requester_instance_id = (requester_instance_id or "").strip() or local_id
    actor_id = (actor_id or "").strip() or Config.root_admin_id

    if provider_instance_id != requester_instance_id and not await _check_permit(
        requester_instance_id, provider_instance_id, config=cfg
    ):
        await _log_hop(
            dominio=Config.root_admin_id,
            accion="list_offerings",
            decision=f"{actor_id} ({requester_instance_id}) requested {provider_instance_id}'s offerings",
            resultado="refused: no shares permit",
            instance_id=local_id,
        )
        return {"ok": False, "reason": "no shares permit", "offerings": []}

    await db.init_db()
    offerings = await db.list_shared_resources(provider_instance_id)
    # Defense in depth: filter to the closed shareable set even though publish()
    # is the only writer today; a row inserted by any future/other path (a
    # migration, a direct db.publish_resource call, manual DB surgery) must
    # still never surface a fact/episode through this read, not just be
    # prevented from being written by this one call site.
    offerings = [o for o in offerings if o.get("resource_type") in SHAREABLE_TYPES]
    await _log_hop(
        dominio=Config.root_admin_id,
        accion="list_offerings",
        decision=f"{actor_id} ({requester_instance_id}) listed {provider_instance_id}'s offerings",
        resultado=f"{len(offerings)} offering(s)",
        instance_id=local_id,
    )
    return {"ok": True, "offerings": offerings}


async def subscribe(
    provider_instance_id: str,
    resource_type: str,
    resource_name: str,
    *,
    actor_id: str,
    config: "Optional[Config]" = None,
) -> dict:
    """Subscribe THIS instance to a resource offered by ``provider_instance_id``.

    Deny-by-default: refuses without a live SHARES permit AND without the
    resource actually being published (not merely claimed by the caller).
    """
    cfg = _resolve_config(config)
    instance_id = _local_instance_id(cfg)
    provider_instance_id = (provider_instance_id or "").strip()
    resource_type = (resource_type or "").strip()
    resource_name = (resource_name or "").strip()
    actor_id = (actor_id or "").strip() or Config.root_admin_id

    err = _validate_type(resource_type)
    if err:
        await _log_hop(
            dominio=Config.root_admin_id,
            accion="subscribe_resource",
            decision=f"{actor_id} attempted to subscribe to {provider_instance_id}/{resource_type}/{resource_name}",
            resultado=f"refused: {err}",
            instance_id=instance_id,
        )
        return {"ok": False, "reason": err}

    if not await _check_permit(instance_id, provider_instance_id, config=cfg):
        await _log_hop(
            dominio=Config.root_admin_id,
            accion="subscribe_resource",
            decision=f"{actor_id} attempted to subscribe to {provider_instance_id}/{resource_type}/{resource_name}",
            resultado="refused: no shares permit",
            instance_id=instance_id,
        )
        return {"ok": False, "reason": "no shares permit"}

    await db.init_db()
    offer = await db.get_shared_resource(provider_instance_id, resource_type, resource_name)
    if offer is None:
        reason = f"{provider_instance_id} has not published {resource_type}/{resource_name}"
        await _log_hop(
            dominio=Config.root_admin_id,
            accion="subscribe_resource",
            decision=f"{actor_id} attempted to subscribe to {provider_instance_id}/{resource_type}/{resource_name}",
            resultado=f"refused: {reason}",
            instance_id=instance_id,
        )
        return {"ok": False, "reason": reason}

    await db.add_resource_subscription(
        instance_id, provider_instance_id, resource_type, resource_name, actor_id=actor_id
    )
    await _log_hop(
        dominio=Config.root_admin_id,
        accion="subscribe_resource",
        decision=f"{actor_id} subscribes to {provider_instance_id}/{resource_type}/{resource_name}",
        resultado=f"subscribed: {resource_type}/{resource_name} from {provider_instance_id}",
        instance_id=instance_id,
    )
    return {"ok": True, "provider_instance_id": provider_instance_id, "resource_type": resource_type, "resource_name": resource_name}


async def unsubscribe(
    provider_instance_id: str,
    resource_type: str,
    resource_name: str,
    *,
    actor_id: str,
    config: "Optional[Config]" = None,
) -> dict:
    """Unsubscribe from a previously-subscribed resource. Idempotent."""
    cfg = _resolve_config(config)
    instance_id = _local_instance_id(cfg)
    actor_id = (actor_id or "").strip() or Config.root_admin_id
    await db.init_db()
    changed = await db.remove_resource_subscription(
        instance_id, provider_instance_id, resource_type, resource_name
    )
    await _log_hop(
        dominio=Config.root_admin_id,
        accion="unsubscribe_resource",
        decision=f"{actor_id} unsubscribes from {provider_instance_id}/{resource_type}/{resource_name}",
        resultado="unsubscribed" if changed else "was not subscribed",
        instance_id=instance_id,
    )
    return {"ok": True, "unsubscribed": changed}


async def list_subscriptions(*, config: "Optional[Config]" = None) -> list[dict]:
    """THIS instance's own live subscriptions; no permit needed for your own list."""
    cfg = _resolve_config(config)
    instance_id = _local_instance_id(cfg)
    await db.init_db()
    return await db.list_resource_subscriptions(instance_id)
