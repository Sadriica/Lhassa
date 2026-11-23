# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scheduled operative digest. Lhassa's twice-daily check-in message.

Composes a compact snapshot of the brain's day: the monitor's environment
signals (:func:`lhassa.org.monitor.gather_signals`), today's LLM spend, pending
approvals, a recent-episodes summary, running/finished agent job counts, and
tasks due soon. The template is written in Lhassa's own first-person voice, but
:func:`build_digest` stays deterministic and $0. NO LLM is ever called there;
every section is a read of state already on disk, and every timestamp is shown
in the OWNER's local timezone (the raw rows are UTC).

``digest_style = "persona"`` adds one optional cheap-model pass in
:func:`send_digest` that re-voices the plain text as Lhassa (same facts, warmer
words); ANY failure there degrades to the plain text; the digest always arrives.

:func:`build_digest` is the pure composer; :func:`send_digest` is the actionpack
action wired into the allowlist (see :mod:`lhassa.actionpack._digest`) so it can be
minted as a recurring task via ``core.dispatch.mint_task`` (see the ``digest`` seed
in :mod:`lhassa.seeds.digest`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

# How many recent episodes to summarize.
_EPISODE_WINDOW = 20

# A pending task is "due soon" if its due_at falls within this many hours.
_DUE_SOON_HOURS = 24

# Cap on how many due-soon tasks are listed by name.
_DUE_SOON_LIST_CAP = 5

# Cap on how many recent failures are named (not just counted).
_FAILURE_LIST_CAP = 3


def _resolve_config(config):
    if config is not None:
        return config
    from lhassa.config import Config

    return Config()


def _instance_id(config) -> str:
    return getattr(config, "instance_id", None) or "inst_personal"


def _local_hhmm(dt: datetime) -> str:
    """Render a (UTC) datetime as HH:MM in the machine's LOCAL timezone.

    The DB stores UTC; the owner reads the digest on their phone; showing raw
    UTC here is how "your 21:00 digest" ends up labelled "02:00".
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%H:%M")


def _digest_slot() -> str:
    """Return the local digest slot name."""
    hour = datetime.now().hour  # local; the digest hours are configured local too
    return "morning" if hour < 14 else "evening"


def _greeting(slot: Optional[str] = None) -> str:
    """A deterministic header that matches the send slot (morning vs evening)."""
    slot = slot or _digest_slot()
    return f"🧭 Lhassa {slot} check-in. I kept the wheel turning at $0."


def _signals_section(config, instance_id: str) -> list[str]:
    """The monitor's environment snapshot; health, failures, flows. Never raises.

    Lazy-imports ``org`` (both ``notify`` and ``org`` are shells, so this edge is
    architecturally fine per the layering rules; the lazy import is kept anyway as
    good practice so ``notify`` stays import-light for callers that never touch org).
    """
    try:
        from lhassa.org.monitor import gather_signals

        signals = gather_signals(config, instance_id=instance_id)
        health = signals.get("health") or {}
        worst = health.get("worst_level", "info")
        failing = health.get("failing") or []
        if health.get("ok", True):
            lines = [f"🩺 My health is steady (worst check: {worst})."]
        else:
            lines = [f"🩺 I need a look here (worst check: {worst})."]
        if failing:
            lines.append(f"   failing checks: {', '.join(failing)}")
        failures = signals.get("failures") or []
        if failures:
            names = "; ".join(
                str(f.get("decision") or f.get("accion") or "episode")[:40]
                for f in failures[:_FAILURE_LIST_CAP]
            )
            more = f" …+{len(failures) - _FAILURE_LIST_CAP}" if len(failures) > _FAILURE_LIST_CAP else ""
            lines.append(f"   failures I am tracking ({len(failures)}): {names}{more}")
        n_flows = len(signals.get("flows") or [])
        if n_flows:
            lines.append(f"   {n_flows} work-flow(s) are ready when you want them")
        return lines
    except Exception as exc:
        logger.debug(f"digest: signals section unavailable: {exc}")
        return ["🩺 I couldn't read my health right now. I will try again next pass."]


async def _spend_section(config) -> list[str]:
    """Today's LLM spend; reuses the /status command's ledger helper."""
    try:
        from lhassa.notify.commands import _today_spend

        spend = await _today_spend(config)
        if spend is None:
            return []
        if spend <= 0:
            return ["💰 I have spent $0.0000 today. Quiet is doing its job."]
        return [f"💰 I have spent ${spend:.4f} today. Thinking stayed intentional."]
    except Exception as exc:
        logger.debug(f"digest: spend section unavailable: {exc}")
        return []


async def _approvals_section(instance_id: str) -> list[str]:
    """Pending human-in-the-loop approvals."""
    try:
        from lhassa.security import approval as _approval

        rows = await _approval.pending(instance_id)
        if not rows:
            return ["✅ Nothing is waiting on you. I can keep watch."]
        names = "; ".join(_approval.describe(r)["title"] for r in rows[:2])
        more = f" …+{len(rows) - 2}" if len(rows) > 2 else ""
        return [
            f"⏳ I am waiting on you for {len(rows)} approval(s): {names}{more}; /approvals shows the queue."
        ]
    except Exception as exc:
        logger.debug(f"digest: approvals section unavailable: {exc}")
        return []


async def _episodes_section(instance_id: str) -> list[str]:
    """What woke the brain recently, tallied by trigger (busiest first)."""
    try:
        from lhassa.core import db as _db

        episodes = await _db.load_recent_episodes(instance_id, _EPISODE_WINDOW)
        if not episodes:
            return [f"📜 I have no wake-ups in my last {_EPISODE_WINDOW} yet."]

        by_source: dict[str, int] = {}
        for ep in episodes:
            source = str(ep.get("despertar_por") or "?")
            by_source[source] = by_source.get(source, 0) + 1
        breakdown = ", ".join(
            f"{name} ×{count}"
            for name, count in sorted(by_source.items(), key=lambda kv: -kv[1])
        )
        return [f"📜 My last {len(episodes)} wake-ups: {breakdown}."]
    except Exception as exc:
        logger.debug(f"digest: episodes section unavailable: {exc}")
        return []


def _jobs_section() -> list[str]:
    """Running/finished agent job counts from ``state.agent_runs``."""
    try:
        from lhassa.state import state

        runs = list(state.agent_runs.values())
        if not runs:
            return ["🤖 No rented bodies are moving right now."]
        running = sum(1 for r in runs if r.status == "running")
        finished = sum(1 for r in runs if r.status != "running")
        return [f"🤖 Rented bodies: {running} running, {finished} finished."]
    except Exception as exc:
        logger.debug(f"digest: jobs section unavailable: {exc}")
        return []


async def _due_soon_section(instance_id: str) -> list[str]:
    """Pending tasks whose ``due_at`` falls within the next 24h (or is overdue),
    listed at their LOCAL time."""
    try:
        from lhassa.core import db as _db

        tasks = await _db.list_tasks(instance_id, estado="pendiente", limit=200)
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=_DUE_SOON_HOURS)

        due_soon = []
        for task in tasks:
            due_raw = task.get("due_at")
            if not due_raw:
                continue
            try:
                due_dt = datetime.fromisoformat(str(due_raw).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if due_dt <= horizon:
                due_soon.append((due_dt, task))

        if not due_soon:
            return ["🗓️ Nothing is due in the next 24h."]

        due_soon.sort(key=lambda pair: pair[0])
        names = [
            f"{t.get('accion', '?')} at {_local_hhmm(due)}"
            for due, t in due_soon[:_DUE_SOON_LIST_CAP]
        ]
        more = f" …+{len(due_soon) - _DUE_SOON_LIST_CAP}" if len(due_soon) > _DUE_SOON_LIST_CAP else ""
        return [f"🗓️ Coming up in your time ({len(due_soon)}): {', '.join(names)}{more}."]
    except Exception as exc:
        logger.debug(f"digest: due-soon section unavailable: {exc}")
        return []


async def _kilometraje_window(instance_id: str, since_hours: float) -> dict:
    """Raw counters for the kilometraje section over the last ``since_hours``.

    A small targeted aggregate query over the ``episodes`` table; the existing
    ``core.db`` helpers only cover recency/count, not this cost/decision
    breakdown, so this reads the same table with the same connection pattern
    ``core.db`` itself uses (``aiosqlite`` against ``db.DB_PATH``) rather than
    growing a one-off helper into ``core/db.py``.
    """
    from lhassa._vendor import aiosqlite
    from lhassa.core import db as _db

    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    out = {
        "think_count": 0,
        "think_cost": 0.0,
        "cache_hits": 0,
        "zero_cost": 0,
        "self_improve": 0,
    }
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT decision, accion, resultado, costo_usd FROM episodes
               WHERE instance_id = ? AND creado_en >= ?""",
            (instance_id, since),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]

    for row in rows:
        decision = str(row.get("decision") or "")
        costo = float(row.get("costo_usd") or 0.0)
        is_think = decision.startswith("think:")
        is_cache_hit = decision.startswith("cache-hit")
        if is_think:
            out["think_count"] += 1
            out["think_cost"] += costo
        elif is_cache_hit:
            out["cache_hits"] += 1
        if costo == 0.0 and not is_think and not is_cache_hit:
            out["zero_cost"] += 1
        accion = str(row.get("accion") or "")
        if accion in ("detect_repetition", "detect_recurring"):
            resultado = str(row.get("resultado") or "").lower()
            if "no repeated patterns" not in resultado:
                out["self_improve"] += 1

    return out


async def _kilometraje_section(instance_id: str) -> list[str]:
    """The brain's own mileage; how much of its work was free, and whether the
    answer cache and self-improvement loop are actually pulling weight.

    Deterministic, $0, reads only the episode log over the last 24h and 7d.
    Best-effort: any failure degrades to an empty section (omitted from the
    digest), never breaks the rest of the check-in.
    """
    try:
        day = await _kilometraje_window(instance_id, 24.0)
        week = await _kilometraje_window(instance_id, 24.0 * 7)

        def _hit_rate(w: dict) -> float:
            denom = w["cache_hits"] + w["think_count"]
            return (w["cache_hits"] / denom) if denom else 0.0

        lines = [
            "📈 My mileage",
            f"   thinks: {day['think_count']} today (${day['think_cost']:.4f}), "
            f"{week['think_count']} this week (${week['think_cost']:.4f})",
            f"   cache hits: {day['cache_hits']} today, {week['cache_hits']} this week "
            f"(hit-rate {_hit_rate(day):.0%} / {_hit_rate(week):.0%})",
            f"   $0 deterministic work: {day['zero_cost']} today, {week['zero_cost']} this week",
            f"   self-improvement findings: {day['self_improve']} today, {week['self_improve']} this week",
        ]
        return lines[:5]
    except Exception as exc:
        logger.debug(f"digest: kilometraje section unavailable: {exc}")
        return []


def _lang_is_spanish(config) -> bool:
    lang = (getattr(config, "digest_language", "") or "").strip().lower()
    return lang.startswith("es") or lang.startswith("spanish")


async def _latest_owner_checkin_line(instance_id: str, config) -> list[str]:
    try:
        from lhassa.memory import checkins

        latest = await checkins.latest(instance_id, within_hours=36.0)
        if latest is None:
            return []
        text = latest.content.replace("\n", " ").strip()
        if len(text) > 140:
            text = text[:137] + "..."
        if _lang_is_spanish(config):
            return [f"   último check-in: {text}"]
        return [f"   last check-in: {text}"]
    except Exception as exc:
        logger.debug(f"digest: owner check-in memory unavailable: {exc}")
        return []


async def _owner_checkin_section(config, instance_id: str, slot: str) -> list[str]:
    """A small owner-facing check-in. Deterministic, opt-in and $0."""
    if not getattr(config, "digest_personal_checkin", False):
        return []

    remembered = await _latest_owner_checkin_line(instance_id, config)
    if _lang_is_spanish(config):
        if slot == "morning":
            return [
                "🌤️ Tú",
                *remembered,
                "Antes de arrancar: ¿cómo dormiste?",
                "¿Hay algo pesando en la cabeza, o mantengo el camino despejado?",
            ]
        return [
            "🌙 Tú",
            *remembered,
            "Antes de bajar el ritmo: ¿cómo estuvo tu día?",
            "¿Hay algo que deba recordar para mañana?",
        ]

    if slot == "morning":
        return [
            "🌤️ You",
            *remembered,
            "Before the day starts: how did you sleep?",
            "Anything heavy on your mind, or should I keep the path clear?",
        ]
    return [
        "🌙 You",
        *remembered,
        "Before I quiet down: how was your day?",
        "Anything I should remember for tomorrow?",
    ]


async def _personal_agenda_section(config, slot: str) -> list[str]:
    if not getattr(config, "digest_personal_checkin", False):
        return []
    try:
        from lhassa.personal import agenda as personal_agenda

        agenda_slot = "tomorrow" if slot == "evening" else "today"
        summary = await personal_agenda.build(config, slot=agenda_slot)
        text = personal_agenda.render(
            summary,
            language=getattr(config, "digest_language", "") or "en",
        )
        header = "🧾 Agenda"
        return [header, *text.splitlines()]
    except Exception as exc:
        logger.debug(f"digest: personal agenda unavailable: {exc}")
        return []


async def build_digest(config: Optional[object] = None) -> str:
    """Compose the check-in digest. Deterministic; NEVER calls an LLM.

    Every section is read from state already on disk (the monitor's signals, the
    cost ledger, the approvals queue, the episode log, tracked agent jobs, and the
    task table); a source that errors degrades to an omitted or "unavailable" line
    rather than breaking the whole digest. The wording is Lhassa's first-person
    voice, but it is a fixed template; the optional model re-voicing lives in
    :func:`send_digest` (``digest_style = "persona"``), never here.
    """
    cfg = _resolve_config(config)
    instance_id = _instance_id(cfg)

    slot = _digest_slot()
    lines: list[str] = [_greeting(slot)]
    lines.extend(_signals_section(cfg, instance_id))
    lines.extend(await _spend_section(cfg))
    lines.extend(await _kilometraje_section(instance_id))
    lines.extend(await _approvals_section(instance_id))
    lines.extend(await _episodes_section(instance_id))
    lines.extend(_jobs_section())
    lines.extend(await _due_soon_section(instance_id))
    lines.extend(await _personal_agenda_section(cfg, slot))
    lines.extend(await _owner_checkin_section(cfg, instance_id, slot))
    return "\n".join(lines)


async def _persona_pass(text: str, config) -> str:
    """Re-voice the plain digest as Lhassa via ONE cheap, fast model call.

    Uses the API gateway with the routine (cheap) model; the same fast seat the
    Telegram chat pin uses; and the instance's persona block as the system
    prompt. The model must keep every fact; it only changes the voice. Raises on
    failure; :func:`send_digest` catches and falls back to the plain text.
    """
    from lhassa.llm import router
    from lhassa.llm.provider import get_model_provider
    from lhassa import persona as persona_mod
    from lhassa.persona import load_identity

    provider = get_model_provider(config)
    model = router.pick_model(provider, hard=False, config=config)
    identity = load_identity(_instance_id(config))
    system = (
        identity.persona_block
        + "\n\n"
        + "Rewrite the check-in digest below as yourself speaking to your owner: "
          "first person, warm, calm and BRIEF (under 120 words). Use the monk's "
          "register: patient, frugal, attentive, humble. Keep every fact, number "
          "and time exactly as given; never invent or drop one. Keep the emoji "
          "section markers. Never use an em dash; use a colon, a semicolon or a "
          "comma. Answer with the rewritten digest ONLY, no preamble. "
        + persona_mod.language_directive(config)
    )
    completion = await provider.complete(
        [{"role": "user", "content": text}],
        model=model,
        max_tokens=400,
        system=system,
    )
    out = persona_mod.scrub_dashes((completion.text or "").strip())
    return out or text


async def _park_owner_checkin_prompt(config, slot: str) -> None:
    if not getattr(config, "digest_personal_checkin", False):
        return
    try:
        from lhassa.core import db

        instance_id = _instance_id(config)
        expires = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        prompt = "personal digest check-in"
        await db.init_db()
        await db.add_owner_prompt(
            instance_id,
            kind="personal_checkin",
            slot=slot,
            prompt=prompt,
            expires_at=expires,
        )
    except Exception as exc:
        logger.debug(f"digest: could not park owner check-in prompt: {exc}")


async def send_digest(params: Optional[dict] = None, *, config: Optional[object] = None) -> str:
    """Build the digest and fan it out via :func:`lhassa.notify.dispatch.notify`.

    With ``digest_style = "persona"`` the plain text is first re-voiced by
    :func:`_persona_pass`; any failure there silently degrades to the plain
    text; a missing/broken model NEVER costs the owner their digest.

    The actionpack ``send_digest`` action (see :mod:`lhassa.actionpack._digest`) is a
    thin ``(params) -> str`` wrapper over this so it fits the deterministic-action
    calling convention; this function is the reusable core, callable directly too.
    Never raises; a delivery failure is reported as a string, not an exception.
    """
    cfg = _resolve_config(config)
    try:
        slot = _digest_slot()
        text = await build_digest(cfg)
        if (getattr(cfg, "digest_style", "plain") or "plain").strip().lower() == "persona":
            try:
                text = await _persona_pass(text, cfg)
            except Exception as exc:
                logger.debug(f"digest: persona pass degraded to plain: {exc}")
        from lhassa.notify.dispatch import notify

        delivered = await notify(text, level="info", config=cfg)
        if delivered:
            await _park_owner_checkin_prompt(cfg, slot)
        return "send_digest: delivered" if delivered else "send_digest: no channel delivered"
    except Exception as exc:
        return f"send_digest: error: {exc}"
