"""The scheduled operative digest — Lhassa's twice-daily check-in message.

Composes a compact snapshot of the brain's day: the monitor's environment
signals (:func:`lhassa.org.monitor.gather_signals`), today's LLM spend, pending
approvals, a recent-episodes summary, running/finished agent job counts, and
tasks due soon. The template is written in Lhassa's own first-person voice, but
:func:`build_digest` stays deterministic and $0 — NO LLM is ever called there;
every section is a read of state already on disk, and every timestamp is shown
in the OWNER's local timezone (the raw rows are UTC).

``digest_style = "persona"`` adds one optional cheap-model pass in
:func:`send_digest` that re-voices the plain text as Lhassa (same facts, warmer
words); ANY failure there degrades to the plain text — the digest always arrives.

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

    The DB stores UTC; the owner reads the digest on their phone — showing raw
    UTC here is how "your 21:00 digest" ends up labelled "02:00".
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%H:%M")


def _greeting() -> str:
    """A deterministic header that matches the send slot (morning vs evening)."""
    hour = datetime.now().hour  # local — the digest hours are configured local too
    slot = "morning" if hour < 14 else "evening"
    return f"🧭 Lhassa — {slot} check-in"


def _signals_section(config, instance_id: str) -> list[str]:
    """The monitor's environment snapshot — health, failures, flows. Never raises.

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
            lines = [f"🩺 I'm healthy (worst check: {worst})."]
        else:
            lines = [f"🩺 Health needs attention (worst check: {worst})."]
        if failing:
            lines.append(f"   failing: {', '.join(failing)}")
        failures = signals.get("failures") or []
        if failures:
            names = "; ".join(
                str(f.get("decision") or f.get("accion") or "episode")[:40]
                for f in failures[:_FAILURE_LIST_CAP]
            )
            more = f" …+{len(failures) - _FAILURE_LIST_CAP}" if len(failures) > _FAILURE_LIST_CAP else ""
            lines.append(f"   recent failures I'm tracking ({len(failures)}): {names}{more}")
        n_flows = len(signals.get("flows") or [])
        if n_flows:
            lines.append(f"   {n_flows} work-flow(s) ready to run")
        return lines
    except Exception as exc:
        logger.debug(f"digest: signals section unavailable: {exc}")
        return ["🩺 I couldn't read my health right now."]


async def _spend_section(config) -> list[str]:
    """Today's LLM spend — reuses the /status command's ledger helper."""
    try:
        from lhassa.notify.commands import _today_spend

        spend = await _today_spend(config)
        if spend is None:
            return []
        return [f"💰 I've spent ${spend:.4f} today."]
    except Exception as exc:
        logger.debug(f"digest: spend section unavailable: {exc}")
        return []


async def _approvals_section(instance_id: str) -> list[str]:
    """Pending human-in-the-loop approvals."""
    try:
        from lhassa.security import approval as _approval

        rows = await _approval.pending(instance_id)
        if not rows:
            return ["✅ Nothing waiting on you."]
        names = "; ".join(_approval.describe(r)["title"] for r in rows[:2])
        more = f" …+{len(rows) - 2}" if len(rows) > 2 else ""
        return [
            f"⏳ Waiting on you: {len(rows)} approval(s): {names}{more} — /approvals for details."
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
            return [f"📜 No wake-ups in my last {_EPISODE_WINDOW} yet."]

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
            return ["🤖 No agent jobs running."]
        running = sum(1 for r in runs if r.status == "running")
        finished = sum(1 for r in runs if r.status != "running")
        return [f"🤖 Agent jobs: {running} running, {finished} finished."]
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
            return ["🗓️ Nothing scheduled in the next 24h."]

        due_soon.sort(key=lambda pair: pair[0])
        names = [
            f"{t.get('accion', '?')} at {_local_hhmm(due)}"
            for due, t in due_soon[:_DUE_SOON_LIST_CAP]
        ]
        more = f" …+{len(due_soon) - _DUE_SOON_LIST_CAP}" if len(due_soon) > _DUE_SOON_LIST_CAP else ""
        return [f"🗓️ Coming up, your time ({len(due_soon)}): {', '.join(names)}{more}."]
    except Exception as exc:
        logger.debug(f"digest: due-soon section unavailable: {exc}")
        return []


async def build_digest(config: Optional[object] = None) -> str:
    """Compose the check-in digest. Deterministic; NEVER calls an LLM.

    Every section is read from state already on disk (the monitor's signals, the
    cost ledger, the approvals queue, the episode log, tracked agent jobs, and the
    task table); a source that errors degrades to an omitted or "unavailable" line
    rather than breaking the whole digest. The wording is Lhassa's first-person
    voice, but it is a fixed template — the optional model re-voicing lives in
    :func:`send_digest` (``digest_style = "persona"``), never here.
    """
    cfg = _resolve_config(config)
    instance_id = _instance_id(cfg)

    lines: list[str] = [_greeting()]
    lines.extend(_signals_section(cfg, instance_id))
    lines.extend(await _spend_section(cfg))
    lines.extend(await _approvals_section(instance_id))
    lines.extend(await _episodes_section(instance_id))
    lines.extend(_jobs_section())
    lines.extend(await _due_soon_section(instance_id))
    return "\n".join(lines)


async def _persona_pass(text: str, config) -> str:
    """Re-voice the plain digest as Lhassa via ONE cheap, fast model call.

    Uses the API gateway with the routine (cheap) model — the same fast seat the
    Telegram chat pin uses — and the instance's persona block as the system
    prompt. The model must keep every fact; it only changes the voice. Raises on
    failure — :func:`send_digest` catches and falls back to the plain text.
    """
    from lhassa.llm import router
    from lhassa.llm.provider import get_model_provider
    from lhassa.persona import load_identity

    provider = get_model_provider(config)
    model = router.pick_model(provider, hard=False, config=config)
    identity = load_identity(_instance_id(config))
    lang = (getattr(config, "digest_language", "") or "").strip()
    lang_line = f" Write it in {lang}." if lang else ""
    system = (
        identity.persona_block
        + "\n\n"
        + "Rewrite the check-in digest below as yourself speaking to your owner: "
          "first person, warm, plain and BRIEF (under 120 words). Keep every fact, "
          "number and time exactly as given — never invent or drop one. Keep the "
          "emoji section markers." + lang_line
    )
    completion = await provider.complete(
        [{"role": "user", "content": text}],
        model=model,
        max_tokens=400,
        system=system,
    )
    out = (completion.text or "").strip()
    return out or text


async def send_digest(params: Optional[dict] = None, *, config: Optional[object] = None) -> str:
    """Build the digest and fan it out via :func:`lhassa.notify.dispatch.notify`.

    With ``digest_style = "persona"`` the plain text is first re-voiced by
    :func:`_persona_pass`; any failure there silently degrades to the plain
    text — a missing/broken model NEVER costs the owner their digest.

    The actionpack ``send_digest`` action (see :mod:`lhassa.actionpack._digest`) is a
    thin ``(params) -> str`` wrapper over this so it fits the deterministic-action
    calling convention; this function is the reusable core, callable directly too.
    Never raises — a delivery failure is reported as a string, not an exception.
    """
    cfg = _resolve_config(config)
    try:
        text = await build_digest(cfg)
        if (getattr(cfg, "digest_style", "plain") or "plain").strip().lower() == "persona":
            try:
                text = await _persona_pass(text, cfg)
            except Exception as exc:
                logger.debug(f"digest: persona pass degraded to plain: {exc}")
        from lhassa.notify.dispatch import notify

        delivered = await notify(text, level="info", config=cfg)
        return "send_digest: delivered" if delivered else "send_digest: no channel delivered"
    except Exception as exc:
        return f"send_digest: error: {exc}"
