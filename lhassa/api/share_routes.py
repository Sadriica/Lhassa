# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-instance sharing API (Stream 6 Phase A) — a thin surface over
:mod:`lhassa.org.sharing`.

A self-contained :class:`fastapi.APIRouter` (``router``) the integrator mounts
with ``app.include_router(share_routes.router)``. Every write op returns a
clear 4xx (never a 500) on a refusal — a bad resource type, an unpermitted
cross-instance read, or an unpublished resource are all ordinary, expected
outcomes of a deny-by-default gate, not server errors.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

import lhassa.config as lhassa_config
from lhassa.org import sharing

router = APIRouter()


def _actor_id(payload: dict) -> str:
    return str(payload.get("actor_id") or "").strip() or lhassa_config.Config.root_admin_id


def _refuse(result: dict) -> None:
    if not result.get("ok"):
        raise HTTPException(status_code=403, detail=result.get("reason") or "refused")


@router.post("/api/share/publish")
async def publish_resource(payload: dict):
    """Body: ``{resource_type, resource_name, actor_id?}``."""
    resource_type = str(payload.get("resource_type") or "").strip()
    resource_name = str(payload.get("resource_name") or "").strip()
    if not resource_type or not resource_name:
        raise HTTPException(status_code=400, detail="resource_type and resource_name are required.")
    result = await sharing.publish(resource_type, resource_name, actor_id=_actor_id(payload))
    _refuse(result)
    return result


@router.post("/api/share/unpublish")
async def unpublish_resource(payload: dict):
    """Body: ``{resource_type, resource_name, actor_id?}``."""
    resource_type = str(payload.get("resource_type") or "").strip()
    resource_name = str(payload.get("resource_name") or "").strip()
    if not resource_type or not resource_name:
        raise HTTPException(status_code=400, detail="resource_type and resource_name are required.")
    return await sharing.unpublish(resource_type, resource_name, actor_id=_actor_id(payload))


@router.post("/api/share/grant")
async def grant_access(payload: dict):
    """Body: ``{instance_id, note?, actor_id?}``."""
    instance_id = str(payload.get("instance_id") or "").strip()
    if not instance_id:
        raise HTTPException(status_code=400, detail="instance_id is required.")
    result = await sharing.grant(
        instance_id, actor_id=_actor_id(payload), note=str(payload.get("note") or "")
    )
    _refuse(result)
    return result


@router.post("/api/share/revoke")
async def revoke_access(payload: dict):
    """Body: ``{instance_id, actor_id?}``."""
    instance_id = str(payload.get("instance_id") or "").strip()
    if not instance_id:
        raise HTTPException(status_code=400, detail="instance_id is required.")
    return await sharing.revoke_grant(instance_id, actor_id=_actor_id(payload))


@router.get("/api/share/offerings/{instance_id}")
async def get_offerings(instance_id: str, actor_id: str = ""):
    """What ``instance_id`` offers — refused (403) without a live SHARES permit."""
    cfg = lhassa_config.Config()
    result = await sharing.list_offerings(
        instance_id,
        requester_instance_id=cfg.instance_id,
        actor_id=actor_id or cfg.root_admin_id,
        config=cfg,
    )
    _refuse(result)
    return result


@router.post("/api/share/subscribe")
async def subscribe(payload: dict):
    """Body: ``{instance_id, resource_type, resource_name, actor_id?}``."""
    instance_id = str(payload.get("instance_id") or "").strip()
    resource_type = str(payload.get("resource_type") or "").strip()
    resource_name = str(payload.get("resource_name") or "").strip()
    if not instance_id or not resource_type or not resource_name:
        raise HTTPException(
            status_code=400, detail="instance_id, resource_type and resource_name are required."
        )
    result = await sharing.subscribe(
        instance_id, resource_type, resource_name, actor_id=_actor_id(payload)
    )
    _refuse(result)
    return result


@router.post("/api/share/unsubscribe")
async def unsubscribe(payload: dict):
    """Body: ``{instance_id, resource_type, resource_name, actor_id?}``."""
    instance_id = str(payload.get("instance_id") or "").strip()
    resource_type = str(payload.get("resource_type") or "").strip()
    resource_name = str(payload.get("resource_name") or "").strip()
    if not instance_id or not resource_type or not resource_name:
        raise HTTPException(
            status_code=400, detail="instance_id, resource_type and resource_name are required."
        )
    return await sharing.unsubscribe(
        instance_id, resource_type, resource_name, actor_id=_actor_id(payload)
    )


@router.get("/api/share/subscriptions")
async def get_subscriptions():
    """This instance's own live subscriptions."""
    return {"subscriptions": await sharing.list_subscriptions()}
