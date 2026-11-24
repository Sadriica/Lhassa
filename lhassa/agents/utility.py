# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The utility tribunal; the $0 sense that asks whether a capability still serves.

Lhassa's invariants are all invariants of RESTRAINT: deny-by-default, signed
tasks, quarantine, provenance, integrity contracts. Every one of them asks "is
this safe / is this honest?". None of them asks **"is this used?"**: so the
system optimises the thing it measures and accretes capability it never
exercises. This module is the missing counterweight, and it is deliberately
built as the mirror image of :mod:`lhassa.skills.curator`: the curator gives
SKILLS a lifecycle driven by usage telemetry; this gives CAPABILITIES
(registered ACTIONS) the same discipline.

**No new storage.** The curator needs a sidecar because a skill read leaves no
trace. A capability does: every run of an action writes an Episode whose
``accion`` names it. So this module is a pure derived read over the append-only
episode log; the same choice :mod:`lhassa.memory.evidence` makes. Nothing here
writes, nothing here calls an LLM.

**Owner-driven vs self-driven use is the whole point.** Counting bare uses would
call a capability "active" because Lhassa's own maintenance seed schedules it;
precisely the self-referential trap this module exists to expose. So each use is
classified by the episode's ``despertar_por`` into ``owner`` (a human reached for
it), ``self`` (Lhassa scheduled or delegated it to itself) or ``unknown``, and a
capability that only ever fires because Lhassa asked itself to is reported as
``self_driven``: running, but not earning its place. The wake vocabulary is a
closed, documented HEURISTIC (see :data:`OWNER_WAKES` / :data:`SELF_WAKES`), not
NLP, in the same spirit as the knowledge graph's entity extraction.

**It proposes; it never retires.** A registered action lives in code; this
module cannot and must not delete it. :func:`find_dormant` returns candidates
and the ``review_capabilities`` action parks ONE summary approval per sweep.
Approving records the capabilities as *retirement candidates*
(:func:`retired_candidates`); nothing is removed or disabled. Rejecting pins
them (:func:`pinned_capabilities`) so they are never asked about again.

**Four states, because "no owner use" is not one thing.** ``unused`` (nobody
ever used it), ``dormant_owner`` (the owner used it, but not within the dormant
window), ``self_driven`` (only Lhassa or its seed uses it) and ``active``.
The old single ``self_driven`` bucket mixed the second and third, and the
tribunal's titles told the owner "only Lhassa itself uses it" about things he
used himself.

**Who is never put on trial.** Seeded maintenance machinery, the temple's
fronted actions and a closed set of structural actions are infrastructure by
definition (:func:`excluded_reason`). A capability with no instrumented door
cannot be judged ``unused``: its zero would be missing instrumentation, not
disuse. Every capability also gets its OWN grace period from the first time it
was observed (``utility:seen:<cap>``), not just the instance's.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from lhassa._vendor import aiosqlite
from lhassa.config import Config
from lhassa.core import db as _db

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

__all__ = [
    "INFRASTRUCTURE",
    "STATE_DORMANT_OWNER",
    "SEEN_PREFIX",
    "PINNED_PREFIX",
    "RETIRED_KEY",
    "excluded_reason",
    "has_instrumented_door",
    "load_pinned",
    "pin_capability",
    "retired_candidates",
    "add_retired_candidate",
    "mark_seen",
    "WAKE_OWNER",
    "WAKE_SELF",
    "WAKE_UNKNOWN",
    "STATE_ACTIVE",
    "STATE_SELF_DRIVEN",
    "STATE_UNUSED",
    "OWNER_WAKES",
    "SELF_WAKES",
    "classify_wake",
    "pinned_capabilities",
    "survey",
    "find_dormant",
]

# ── the closed wake vocabulary (a documented heuristic, not NLP) ──────────────

WAKE_OWNER = "owner"
WAKE_SELF = "self"
WAKE_UNKNOWN = "unknown"

#: Wake sources that mean a HUMAN reached for the capability. Matched as exact
#: values or as a ``<prefix>:`` prefix, so ``telegram`` and ``telegram:12345``
#: both classify as owner-driven.
OWNER_WAKES = (
    "telegram",
    "cli",
    "api",
    "voice",
    "humano",
    "human",
    "owner",
    "duenio",
    "approval",
    "dashboard",
)

#: Wake sources that mean LHASSA drove itself; a scheduled task, an org hop, a
#: monitor tick, a health alert. A capability seen only under these is running
#: because Lhassa asked itself to, which is exactly what this module surfaces.
SELF_WAKES = (
    "task",
    "monitor",
    "org",
    "org-delegate",
    "org-deliver",
    "temple-execute",
    "health",
    "heartbeat",
    "watchlist",
    "schedule",
    "seed",
    "standing-brains",
)

STATE_ACTIVE = "active"
STATE_SELF_DRIVEN = "self_driven"
STATE_UNUSED = "unused"
#: The owner used it at least once, but not inside the dormant window.
STATE_DORMANT_OWNER = "dormant_owner"

DEFAULT_DORMANT_DAYS = 30
DEFAULT_GRACE_DAYS = 14
DEFAULT_CAPABILITY_GRACE_DAYS = 30

# watch_state keys (all scoped by instance_id by the db layer).
#: ``utility:seen:<cap>`` -> ISO timestamp the capability was first observed.
SEEN_PREFIX = "utility:seen:"
#: ``utility:pinned:<cap>`` -> "1"; written when a HUMAN rejects a retirement.
PINNED_PREFIX = "utility:pinned:"
#: JSON list of capabilities whose retirement the owner approved (candidates only).
RETIRED_KEY = "utility:retired_candidates"

#: Structural / plumbing actions that are infrastructure by definition, on top of
#: the seeded ones (``seeds.maintenance._RECURRING_ACTIONS`` and
#: ``actionpack._temple.TEMPLE_ACTIONS``, read live). Either Lhassa's own
#: machinery (the temple wrapper, the digest, the notifier) or an action that
#: other code calls DIRECTLY instead of through dispatch, so it never leaves an
#: episode naming itself: a "0 uses" there measures missing instrumentation, not
#: disuse. Measured: add_fact, add_task, org_run, run_script, set_task_state and
#: sync_repo all read "never used" while being called constantly.
INFRASTRUCTURE = frozenset(
    {
        "temple_run",
        "send_digest",
        "notify",
        "set_task_state",
        "sync_repo",
        "run_script",
        "org_run",
        "add_fact",
        "add_task",
        "check_health",
        "consolidate_memory",
        "backup_memory",
        "system_snapshot",
        "list_approvals",
        "git_status_repos",
    }
)


def classify_wake(despertar_por: Optional[str]) -> str:
    """Bucket one episode's ``despertar_por`` into owner / self / unknown.

    Matching is exact-or-``prefix:`` against the two closed vocabularies above
    (``task:send_digest`` -> ``self``, ``telegram`` -> ``owner``). Owner is
    checked FIRST so an owner-originated wake is never mistaken for plumbing.
    Anything unrecognised is ``unknown``: reported, never guessed at.
    """
    raw = (despertar_por or "").strip().lower()
    if not raw:
        return WAKE_UNKNOWN
    head = raw.split(":", 1)[0]
    for token in OWNER_WAKES:
        if raw == token or head == token:
            return WAKE_OWNER
    for token in SELF_WAKES:
        if raw == token or head == token:
            return WAKE_SELF
    return WAKE_UNKNOWN


def _resolve_config(config: "Optional[Config]") -> "Config":
    return config if config is not None else Config


def pinned_capabilities(config: "Optional[Config]" = None, persisted=()) -> set:
    """Capabilities the owner declared deliberately dormant; never proposed.

    The union of ``Config.utility_pinned`` (``LHASSA_UTILITY_PINNED``, a
    comma-separated list) and ``persisted``: the names an owner's rejection
    pinned in ``watch_state`` (``utility:pinned:<cap>``, see :func:`load_pinned`
    for the async read). The curator's ``pinned`` flag, for capabilities.
    """
    cfg = _resolve_config(config)
    raw = getattr(cfg, "utility_pinned", "") or ""
    env = {p.strip() for p in str(raw).split(",") if p.strip()}
    return env | {str(p) for p in (persisted or ())}


async def load_pinned(instance_id: Optional[str] = None, config: "Optional[Config]" = None) -> set:
    """Env-pinned plus rejection-pinned capabilities (the full pinned set)."""
    cfg = _resolve_config(config)
    inst = instance_id or cfg.instance_id
    persisted: set = set()
    try:
        await _db.init_db()
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            async with conn.execute(
                "SELECT key FROM watch_state WHERE instance_id = ? AND key LIKE ?",
                (inst, PINNED_PREFIX + "%"),
            ) as cur:
                persisted = {str(r[0])[len(PINNED_PREFIX):] for r in await cur.fetchall()}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("agents.utility: load_pinned failed; {}", exc)
    return pinned_capabilities(cfg, persisted)


async def pin_capability(instance_id: str, capability: str) -> None:
    """Persist ``utility:pinned:<cap>``: the owner said no, never ask again."""
    await _db.set_watch_state(instance_id, PINNED_PREFIX + capability, "1")


async def retired_candidates(instance_id: Optional[str] = None) -> list:
    """Capabilities whose retirement the owner approved (a list, never an act)."""
    inst = instance_id or Config.instance_id
    try:
        raw = await _db.get_watch_state(inst, RETIRED_KEY)
        data = json.loads(raw) if raw else []
        return [str(c) for c in data] if isinstance(data, list) else []
    except Exception:
        return []


async def add_retired_candidate(instance_id: str, capability: str) -> list:
    """Record ``capability`` as a retirement candidate (deduped). Nothing is removed."""
    current = await retired_candidates(instance_id)
    if capability not in current:
        current.append(capability)
        await _db.set_watch_state(instance_id, RETIRED_KEY, json.dumps(current))
    return current


async def mark_seen(
    instance_id: str, names: list, *, now: Optional[datetime] = None
) -> dict:
    """Stamp ``utility:seen:<cap>`` the first time each capability is observed.

    Returns ``{capability: first_seen_datetime}`` for every name. The per-
    capability grace clock: the instance's age says nothing about a capability
    registered yesterday.
    """
    now_dt = _now_utc(now)
    out: dict = {}
    for name in names:
        key = SEEN_PREFIX + name
        raw = await _db.get_watch_state(instance_id, key)
        ts = _parse_ts(raw)
        if ts is None:
            ts = now_dt
            await _db.set_watch_state(instance_id, key, now_dt.isoformat())
        out[name] = ts
    return out


#: Name sets that live in shells (``cli``, ``actionpack``) which an inner layer
#: may not import. The shells register zero-argument callables here at import
#: time (see ``actionpack/_utility.py``); this module only reads them.
_DOOR_SOURCES: list = []
_SEEDED_SOURCES: list = []


def register_door_source(fn) -> None:
    """Register a callable returning capability names with an instrumented door."""
    if fn not in _DOOR_SOURCES:
        _DOOR_SOURCES.append(fn)


def register_seeded_source(fn) -> None:
    """Register a callable returning names the seed/temple front (infrastructure)."""
    if fn not in _SEEDED_SOURCES:
        _SEEDED_SOURCES.append(fn)


def _door_names() -> set:
    """Capabilities that have a way to leave an episode naming themselves.

    Derived, not hand-listed: the terminal-subcommand map
    (``cli.capability_log.COMMAND_CAPABILITY``), the chat tool map
    (``cli.agent._TOOL_CAPABILITY``), both registered by the shells (an inner
    layer may not import them) and every allowlisted action that runs
    through dispatch without a human gate (a task executes and writes
    ``accion=<name>``). A registered action outside all three has no
    instrumented door: nothing can ever record its use.
    """
    names: set = set()
    for fn in _DOOR_SOURCES:
        try:
            names |= {str(n) for n in fn()}
        except Exception:  # pragma: no cover - defensive
            pass
    try:
        from lhassa.security.allowlist import ALLOWLIST

        names |= {n for n, spec in ALLOWLIST.items() if not spec.requires_approval}
    except Exception:  # pragma: no cover - defensive
        pass
    return names


def has_instrumented_door(name: str) -> bool:
    """True when a use of ``name`` could ever be recorded (see :func:`_door_names`)."""
    return name in _door_names()


def _seeded_names() -> set:
    names: set = set()
    for fn in _SEEDED_SOURCES:
        try:
            names |= {str(n) for n in fn()}
        except Exception:  # pragma: no cover - defensive
            pass
    return names


def excluded_reason(name: str) -> Optional[str]:
    """Why ``name`` can never be put on trial, or ``None`` if it can.

    ``"seeded"`` (the maintenance seed or temple fronts it) and
    ``"infrastructure"`` (the structural set above). Both are machinery, not
    owner-facing capability: proposing to retire them is the tribunal
    attacking its own scaffolding.
    """
    if name in _seeded_names():
        return "seeded"
    if name in INFRASTRUCTURE:
        return "infrastructure"
    return None


def _now_utc(now: Optional[datetime] = None) -> datetime:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _registered_capabilities() -> list:
    """Every action name currently in the ACTIONS registry (plugins included).

    Imported lazily so this module never forces the registry to populate; the
    caller's process has already run ``actionpack.register_all()`` if it cares.
    """
    try:
        from lhassa.core.actions import ACTIONS

        return sorted(str(name) for name in ACTIONS)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("agents.utility: could not read ACTIONS; {}", exc)
        return []


async def survey(
    instance_id: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    config: "Optional[Config]" = None,
) -> list:
    """One row per registered capability: how much it is used, and by whom.

    A pure read. SQL aggregation over the episode log plus the live ACTIONS
    registry. Returns a list of dicts sorted by ``owner_uses`` ascending then
    name (least-earned first, so the head of the list is the tribunal's
    docket)::

        {"capability", "total_uses", "owner_uses", "self_uses", "unknown_uses",
         "last_use_at", "last_owner_use_at", "days_since_owner_use",
         "state", "pinned"}

    ``state`` is ``unused`` (no use ever, by anyone), ``dormant_owner`` (the
    owner used it, but not inside ``config.utility_dormant_days``),
    ``self_driven`` (only Lhassa or its seed ever used it) or ``active``.
    ``excluded`` names why the tribunal never judges it (``seeded`` /
    ``infrastructure``) and ``has_door`` says whether a use of it could ever
    be recorded.
    Never raises: any failure degrades to an empty list.
    """
    try:
        cfg = _resolve_config(config)
        inst = instance_id or cfg.instance_id
        now_dt = _now_utc(now)
        dormant_days = int(getattr(cfg, "utility_dormant_days", DEFAULT_DORMANT_DAYS) or DEFAULT_DORMANT_DAYS)
        pinned = await load_pinned(inst, cfg)
        doors = _door_names()

        names = _registered_capabilities()
        if not names:
            return []

        # accion -> {bucket -> (count, last_ts)} from ONE pass over the log.
        tally: dict = {}
        await _db.init_db()
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT accion, despertar_por, COUNT(*) AS n, MAX(creado_en) AS last_ts
                  FROM episodes
                 WHERE instance_id = ? AND accion IS NOT NULL AND accion <> ''
              GROUP BY accion, despertar_por
                """,
                (inst,),
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                accion = str(row["accion"])
                bucket = classify_wake(row["despertar_por"])
                slot = tally.setdefault(
                    accion,
                    {WAKE_OWNER: [0, None], WAKE_SELF: [0, None], WAKE_UNKNOWN: [0, None]},
                )[bucket]
                slot[0] += int(row["n"] or 0)
                ts = _parse_ts(row["last_ts"])
                if ts and (slot[1] is None or ts > slot[1]):
                    slot[1] = ts

        # Second pass: the TASK ledger. An episode's wake source misses the case
        # that matters most; the owner asks for something whose execution is
        # DEFERRED (``/remind`` mints a send_reminder that fires days later from
        # ``task:send_reminder``, which reads as self-driven). The task row keeps
        # the real provenance in ``origen_actor``, so a capability the owner
        # explicitly minted counts as owner-driven no matter when it ran.
        # ``origen_trust`` is deliberately NOT the signal: a seed mints at
        # HUMANO trust too (``seed:digest``), and trust answers "how much may
        # this do", never "who asked for it".
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT accion, origen_actor, COUNT(*) AS n, MAX(creado_en) AS last_ts
                  FROM tasks
                 WHERE instance_id = ? AND accion IS NOT NULL AND accion <> ''
              GROUP BY accion, origen_actor
                """,
                (inst,),
            ) as cur:
                task_rows = await cur.fetchall()
        #
        # The two ledgers OVERLAP; a ``/remind`` both mints a task and later
        # writes an episode; so they are combined with max(), never summed:
        # they are two lenses on the same acts, and neither is complete. An
        # inflated count in the module whose whole job is honest measurement
        # would be the worst possible bug.
        task_owner: dict = {}
        for row in task_rows:
            if classify_wake(row["origen_actor"]) != WAKE_OWNER:
                continue
            slot = task_owner.setdefault(str(row["accion"]), [0, None])
            slot[0] += int(row["n"] or 0)
            ts = _parse_ts(row["last_ts"])
            if ts and (slot[1] is None or ts > slot[1]):
                slot[1] = ts

        cutoff = now_dt - timedelta(days=dormant_days)
        out: list = []
        for name in names:
            buckets = tally.get(name) or {
                WAKE_OWNER: [0, None],
                WAKE_SELF: [0, None],
                WAKE_UNKNOWN: [0, None],
            }
            minted = task_owner.get(name) or [0, None]
            owner_uses = max(buckets[WAKE_OWNER][0], minted[0])
            self_uses = buckets[WAKE_SELF][0]
            unknown_uses = buckets[WAKE_UNKNOWN][0]
            total = max(
                buckets[WAKE_OWNER][0] + self_uses + unknown_uses,
                owner_uses,
            )

            last_owner = max(
                (t for t in (buckets[WAKE_OWNER][1], minted[1]) if t is not None),
                default=None,
            )
            last_any = max(
                (
                    t
                    for t in list(buckets[k][1] for k in buckets) + [minted[1]]
                    if t is not None
                ),
                default=None,
            )

            if total == 0:
                state = STATE_UNUSED
            elif last_owner is not None and last_owner >= cutoff:
                state = STATE_ACTIVE
            elif owner_uses > 0:
                state = STATE_DORMANT_OWNER  # the owner used it, long ago
            else:
                state = STATE_SELF_DRIVEN  # only Lhassa or its seed ever did

            days_since_owner = (
                (now_dt - last_owner).total_seconds() / 86400.0 if last_owner else None
            )

            out.append(
                {
                    "capability": name,
                    "total_uses": total,
                    "owner_uses": owner_uses,
                    "self_uses": self_uses,
                    "unknown_uses": unknown_uses,
                    "last_use_at": last_any.isoformat() if last_any else None,
                    "last_owner_use_at": last_owner.isoformat() if last_owner else None,
                    "days_since_owner_use": days_since_owner,
                    "state": state,
                    "pinned": name in pinned,
                    "excluded": excluded_reason(name),
                    "has_door": name in doors,
                }
            )

        out.sort(key=lambda r: (r["owner_uses"], r["capability"]))
        return out

    except Exception as exc:  # a $0 sense must never raise
        logger.warning("agents.utility: survey failed; {}", exc)
        return []


async def history_days(
    instance_id: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    config: "Optional[Config]" = None,
) -> float:
    """How many days of episode history this instance actually has.

    The grace baseline: a brand-new install has not had TIME to use anything,
    so judging its capabilities would be noise. Mirrors the curator's fallback
    to a folder's mtime when no telemetry exists. Returns 0.0 on any failure.
    """
    try:
        cfg = _resolve_config(config)
        inst = instance_id or cfg.instance_id
        await _db.init_db()
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            async with conn.execute(
                "SELECT MIN(creado_en) FROM episodes WHERE instance_id = ?", (inst,)
            ) as cur:
                row = await cur.fetchone()
        oldest = _parse_ts(row[0] if row else None)
        if oldest is None:
            return 0.0
        return max(0.0, (_now_utc(now) - oldest).total_seconds() / 86400.0)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("agents.utility: history_days failed; {}", exc)
        return 0.0


async def find_dormant(
    instance_id: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    config: "Optional[Config]" = None,
) -> list:
    """The tribunal's docket: capabilities that have not earned their place.

    A capability qualifies when it is ``unused``, ``dormant_owner`` or
    ``self_driven``, is not pinned (by env or by an owner's rejection) and is
    not excluded (:func:`excluded_reason`). Further shields:

    * nothing qualifies until the instance has more than
      ``config.utility_grace_days`` of episode history;
    * an ``unused`` capability needs an instrumented door
      (:func:`has_instrumented_door`); otherwise its zero is missing
      instrumentation, not disuse;
    * an ``unused`` capability must have been OBSERVED for at least
      ``config.utility_capability_grace_days`` (``utility:seen:<cap>``), so one
      registered yesterday is not judged against the instance's age.

    Returns the same row shape as :func:`survey`. Never raises.
    """
    try:
        cfg = _resolve_config(config)
        inst = instance_id or cfg.instance_id
        grace = int(getattr(cfg, "utility_grace_days", DEFAULT_GRACE_DAYS) or DEFAULT_GRACE_DAYS)
        cap_grace_raw = getattr(cfg, "utility_capability_grace_days", None)
        cap_grace = DEFAULT_CAPABILITY_GRACE_DAYS if cap_grace_raw is None else int(cap_grace_raw)
        now_dt = _now_utc(now)
        rows = await survey(inst, now=now, config=cfg)
        seen = await mark_seen(inst, [r["capability"] for r in rows], now=now_dt)
        if await history_days(inst, now=now, config=cfg) < grace:
            return []
        docket = []
        for r in rows:
            if r["pinned"] or r["excluded"]:
                continue
            if r["state"] == STATE_ACTIVE:
                continue
            if r["state"] == STATE_UNUSED:
                if not r["has_door"]:
                    continue
                age = (now_dt - seen[r["capability"]]).total_seconds() / 86400.0
                if age < cap_grace:
                    continue
            docket.append(r)
        return docket
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("agents.utility: find_dormant failed; {}", exc)
        return []
