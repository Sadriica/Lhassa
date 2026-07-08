"""``calendar_sync`` — the master-calendar reconcile action.

Runs :func:`lhassa.calendar.reconcile.find_unsynced` ($0, deterministic — a set
diff over cached feed reads, no model) and, for every candidate not already
delivered (deduped via ``watch_state`` on the candidate's stable UID — see
:mod:`lhassa.calendar.reconcile`'s ``to_ics``), writes a ``.ics``
(:func:`lhassa.calendar.ics.write_ics`) and hands it to the owner via
:func:`lhassa.org.deliver.deliver` — the ONE sender-of-record seam (never calls
``send_document`` directly; see ``tests/test_deliver.py``'s guard).

Fits the actionpack calling convention: a plain ``(params) -> str`` callable,
never raises, degrades to a clean "nothing to sync" when there's no primary
calendar or no feeds configured at all — so it is safe to schedule
unconditionally (see the ``maintenance`` seed).
"""

from __future__ import annotations

from loguru import logger

from lhassa.calendar import reconcile as _reconcile
from lhassa.calendar import store as _cal_store
from lhassa.calendar.ics import write_ics as _write_ics
from lhassa.config import Config
from lhassa.core import db as _db

# watch_state key prefix — one entry per delivered candidate UID, so a second
# sync pass never re-delivers the same unsynced event (mirrors the
# ``detect_repetition`` "seen" idiom in ``_selfimprove.py``).
_DELIVERED_PREFIX = "calendar_sync:delivered:"


def _stable_key(candidate: dict) -> str:
    """A stable dedupe key for ``candidate`` — reuses the same UID
    :func:`lhassa.calendar.reconcile.to_ics` embeds in the ``.ics`` file, so
    "delivered" and "the file's identity" never drift apart."""
    start = candidate.get("start")
    if start is None:
        return ""
    return _reconcile.stable_uid(candidate.get("summary") or "", start)


async def calendar_sync(params: dict) -> str:
    """Find events unsynced from the master calendar and deliver each as a
    tappable ``.ics``. Never raises.

    params:
        hours        — the look-ahead window in hours (default 168 = 1 week).
        instance_id  — defaults to Config.instance_id.
        config       — an injectable Config (tests only); defaults to Config().
    """
    try:
        params = params or {}
        config = params.get("config") or Config()
        instance_id = params.get("instance_id") or getattr(
            config, "instance_id", None
        ) or Config.instance_id
        hours = int(params.get("hours") or 168)

        primary = _cal_store.get_primary(config)
        if not primary:
            return "calendar_sync: no primary calendar configured — nothing to sync"

        candidates = await _reconcile.find_unsynced(instance_id, hours=hours, config=config)
        if not candidates:
            return "calendar_sync: 0 unsynced event(s), 0 delivered"

        await _db.init_db()

        delivered = 0
        for candidate in candidates:
            key = _stable_key(candidate)
            if not key:
                continue
            dedupe_key = _DELIVERED_PREFIX + key
            if await _db.get_watch_state(instance_id, dedupe_key):
                continue  # already delivered this exact unsynced event

            content = _reconcile.to_ics(candidate)
            if not content:
                continue
            ics_path = _write_ics(content, name=candidate.get("summary") or "event", run_id=key)

            from lhassa.org.deliver import deliver

            ok, _artifact_id = await deliver(
                document=ics_path,
                caption=(
                    f"📅 {candidate.get('summary') or 'Event'} "
                    f"— on {candidate.get('source_calendar') or 'another calendar'}, "
                    f"not on {primary}"
                ),
                kind="calendar_event",
                metadata={
                    "summary": candidate.get("summary") or "",
                    "source_calendar": candidate.get("source_calendar") or "",
                    "primary": primary,
                },
                origin_department="",
                config=config,
            )
            if ok:
                delivered += 1
                await _db.set_watch_state(instance_id, dedupe_key, "1")

        return f"calendar_sync: {len(candidates)} unsynced event(s), {delivered} delivered"
    except Exception as exc:
        logger.warning("calendar_sync: error: {}", exc)
        return f"calendar_sync: error: {exc}"
