# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small LLM-free helpers shared across several command modules.

Moved verbatim out of the former ``lhassa/notify/commands.py``: see the
package ``__init__.py`` for where each command handler now lives.
"""

from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger


def _oops(what: str) -> str:
    """A degraded-path reply in Lhassa's voice; calm, owned, with a next step.

    Every LLM-free command that fails says it the same way: first person, no
    alarm, no blame, and an invitation to retry. One voice, one place.
    """
    return f"I couldn't {what} just now. Give it a moment and try again."


def _config(config):
    """Return ``config`` or a freshly loaded default. Lazy import keeps this light."""
    if config is not None:
        return config
    from lhassa.config import Config

    return Config()


def _routine_provider_model(config) -> Tuple[str, str]:
    """Derive the active *routine* provider label + model. LLM-free, no model call.

    Mirrors :func:`lhassa.llm.router.pick_provider` / ``pick_model`` for the routine
    (heartbeat) path off plain config, **without** constructing a provider or making
    a network call. Returns ``(provider_label, model_name)``.
    """
    import shutil

    heartbeat = (getattr(config, "heartbeat_provider", "auto") or "auto").lower()
    local_url = (getattr(config, "local_model_url", "") or "").strip()
    cloud_model = getattr(config, "model", "") or "cloud"

    def _opencode_model() -> str:
        return (getattr(config, "opencode_model", "") or "") or cloud_model

    def _local_model() -> str:
        return (getattr(config, "local_model", "") or "") or cloud_model

    if heartbeat == "cloud":
        return "cloud", cloud_model
    if heartbeat == "opencode":
        return "opencode", _opencode_model()
    if heartbeat == "local" or (heartbeat == "auto" and local_url):
        if local_url:
            return "local", _local_model()
        # heartbeat='local' but unconfigured → cloud fallback (mirrors the router).
        return "cloud", cloud_model
    if heartbeat == "auto" and not local_url:
        # auto + no local backend → prefer the opencode CLI when its binary exists.
        opencode_bin = (getattr(config, "opencode_bin", "") or "").strip()
        if opencode_bin and shutil.which(opencode_bin):
            return "opencode", _opencode_model()
    return "cloud", cloud_model


def _health_line() -> Optional[str]:
    """A one-line health summary from :func:`lhassa.health.checks.run_all`.

    LLM-free; returns ``None`` (omit the line) if anything goes wrong.
    """
    try:
        from lhassa.health import checks

        digest = checks.run_all(notify=False)
        worst = digest.get("worst_level", "info")
        ok = "ok" if digest.get("ok") else "needs attention"
        return f"Health: {worst} ({ok})"
    except Exception as exc:
        logger.debug(f"commands: health summary unavailable: {exc}")
        return None


async def _today_spend(config) -> Optional[float]:
    """Today's LLM spend (USD) from the cost ledger; the same data /api/llm/limits
    reads. Defensive: returns ``None`` (omit the line) when the ledger is absent or
    unreadable. Reads the ledger DB directly so no model call is made.
    """
    try:
        from datetime import datetime, timezone
        from pathlib import Path

        from lhassa._vendor import aiosqlite

        override = getattr(config, "llm_gateway_db_path", None)
        path = Path(override) if override else Path.home() / ".lhassa" / "llm_gateway.db"
        if not path.exists():
            return None

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM ledger WHERE ts LIKE ?",
                (today + "%",),
            ) as cur:
                row = await cur.fetchone()
        return float(row[0]) if row else 0.0
    except Exception as exc:
        logger.debug(f"commands: today's spend unavailable: {exc}")
        return None


def _approval_age(creado_en) -> str:
    """A compact relative age ('3h ago') for an approval row; '' when unparseable."""
    from datetime import datetime, timezone

    try:
        ts = datetime.fromisoformat(str(creado_en or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    seconds = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"
