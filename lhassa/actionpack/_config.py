# SPDX-License-Identifier: AGPL-3.0-or-later
"""set_config — natural-language settings changes through the ONE settings seam.

The owner says "agrega ~/Documents a tus carpetas de lectura" in chat; the model
emits ``[[action: set_config key=knowledge_roots value=~/Documents]]``; this
action applies it via :func:`lhassa.settings.set` — the SAME editable allowlist,
type coercion, provider guard, live-apply and ``settings.env`` persistence the
dashboard panel, ``lhassa config`` and ``/config`` use. Nothing outside EDITABLE
can be touched (secrets are not EDITABLE, so they are unreachable here by
construction). The returned line always states old → new so the episode — and
the chat follow-up built from it — shows exactly what changed. Never raises.
"""

from __future__ import annotations


def set_config(params: dict) -> str:
    """Change one editable runtime setting. Never raises.

    params:
        key   — the EDITABLE setting key (required).
        value — the new value (string form; coerced by the settings service).
    """
    try:
        if not isinstance(params, dict):
            params = {}
        key = str(params.get("key", "") or "").strip()
        if not key:
            return "set_config: no key given — pass key=<setting> value=<new value>."
        if "value" not in params:
            return f"set_config: no value given for {key!r} — pass value=<new value>."
        value = str(params.get("value") or "")

        from lhassa import settings as _settings

        before_row = _settings.get(key)
        if before_row.get("error"):
            return (
                f"set_config: {key!r} is not an editable setting — "
                "see /config for the list."
            )
        before = before_row.get("value")

        result = _settings.set(key, value)
        if not result.get("applied"):
            return f"set_config: couldn't set {key!r}: {result.get('error', 'unknown error')}"
        line = f"set_config: {key}: {before!r} → {result.get('value')!r}"
        if result.get("restart_needed"):
            line += f" (needs restart: {result.get('restart_hint', '')})"
        return line
    except Exception as exc:
        return f"set_config errored: {exc}"
