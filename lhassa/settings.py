"""Runtime SETTINGS service — the panel/CLI editable knobs (slice BE).

A thin, safe layer over :mod:`lhassa.config` that lets the dashboard and
``lhassa config`` read and change a small ALLOWLIST of runtime settings WITHOUT
ever touching ``.env`` (where secrets live) and WITHOUT editing core. Two rules
keep it safe and elastic:

  - **Allowlist only.** Only the keys in :data:`EDITABLE` (plus advanced
    ``LHASSA_*`` vars via :func:`set_env`) can be changed. Everything else is
    refused with a clear error — never an exception into the caller.
  - **Write to ``settings.env``, never ``.env``.** Edits are persisted to
    ``~/.lhassa/settings.env`` (a simple ``KEY=value`` file that :mod:`lhassa.config`
    already loads AFTER ``.env`` with ``override=True``), so a new heartbeat /
    telegram / app process picks them up on start, and the live :class:`Config`
    class attribute is updated in-process so the running API reflects the change
    immediately.

The service is the backend for the dashboard "Settings" panel: :func:`effective`
is the read model (current value + default + whether it is overridden, secrets
masked), and :func:`set` / :func:`get` / :func:`set_env` are the write/read ops.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

# ── settings.env location ────────────────────────────────────────────────────────
# A dedicated overrides file (loaded by lhassa.config AFTER .env, override=True). We
# NEVER write to .env. Resolved through Path.home() so tests can redirect it with a
# temp HOME and a fresh ~/.lhassa.


def settings_env_path() -> Path:
    """The ``~/.lhassa/settings.env`` overrides file we read-modify-write."""
    return Path.home() / ".lhassa" / "settings.env"


# ── the editable allowlist ───────────────────────────────────────────────────────
# Each entry: key (stable id used by the panel/CLI), attr (the Config attribute),
# env_var (the name written to settings.env), type, label, group, optional options
# (for enums), secret flag, a documented default, and a restart hint describing which
# long-running service must restart for the change to take full effect.
#
# Groups order the panel: Heartbeat, Models, Budget & Safety, Memory, Surfaces, Org.

# A change to a setting consumed only by the heartbeat takes effect on the NEXT beat
# (each beat is a fresh process that re-reads env) — no manual restart needed. A
# setting consumed by a long-running service needs that service restarted.
RESTART_NONE = "none"  # picked up live / next heartbeat — no restart needed
RESTART_APP = "app"  # restart the main app/API service
RESTART_VOICE = "voice"  # restart the voice surface
RESTART_TELEGRAM = "telegram"  # restart the telegram poller

# ── static model hints for the dropdowns (no network) ────────────────────────────
# These are SUGGESTIONS the panel renders as a select/datalist; the fields stay free
# text (``allow_custom``) so any model id can still be typed. [SUPUESTO] — static,
# not verified against a live catalogue; refresh when the providers rotate ids.

# Cheap/free opencode models (reused by the opencode_model dropdown + available()).
OPENCODE_MODEL_HINTS = [
    "opencode/deepseek-v4-flash-free",
    "opencode/grok-code-fast",
    "opencode/qwen3-coder-free",
]

# Cloud model ids per provider, used to populate the cheap/smart model dropdowns.
# [SUPUESTO] current-ish ids — kept small and defensive.
MODEL_HINTS: dict[str, list[str]] = {
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
    ],
    "anthropic": [
        "claude-sonnet-4-6",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-haiku-4-5-20251001",
        "claude-haiku-4-5",
    ],
    "openrouter": [
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-opus-4-8",
        "anthropic/claude-haiku-4-5",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
    ],
    "cerebras": [
        "llama3.3-70b",
        "llama3.1-8b",
    ],
    "ollama": [
        "llama3.2",
        "llama3.3",
        "qwen2.5-coder",
        "nomic-embed-text",
    ],
}


def _model_options() -> list[str]:
    """Union of the cloud + opencode model hints for the cheap/smart dropdowns."""
    seen: list[str] = []
    for ids in list(MODEL_HINTS.values()) + [OPENCODE_MODEL_HINTS]:
        for mid in ids:
            if mid not in seen:
                seen.append(mid)
    return seen


# Provider names valid in the fallback chain / heartbeat CLI selection.
FALLBACK_PROVIDER_OPTIONS = ["opencode", "claude", "codex", "local", "default"]

EDITABLE: list[dict] = [
    # ── Heartbeat / routine thinking ─────────────────────────────────────────────
    {
        "key": "heartbeat_provider",
        "attr": "heartbeat_provider",
        "env_var": "LHASSA_HEARTBEAT_PROVIDER",
        "type": "enum",
        "options": ["auto", "local", "cloud", "opencode", "claude", "codex"],
        "label": "Heartbeat provider",
        "help": (
            "Which provider runs the routine/heartbeat think. 'auto' prefers a local "
            "model, then opencode, then cloud; 'opencode'/'claude'/'codex' force the "
            "matching CLI subscription (claude → the Claude Code CLI, codex → the Codex "
            "CLI); 'local' forces the local endpoint; 'cloud' forces the cost gateway."
        ),
        "group": "Heartbeat",
        "default": "auto",
        "restart": RESTART_NONE,
    },
    {
        "key": "opencode_model",
        "attr": "opencode_model",
        "env_var": "LHASSA_OPENCODE_MODEL",
        "type": "str",
        "options": list(OPENCODE_MODEL_HINTS),
        "allow_custom": True,
        "label": "OpenCode model (routine beat)",
        "help": "Suggested opencode models; any model id may still be typed.",
        "group": "Heartbeat",
        "default": "opencode/deepseek-v4-flash-free",
        "restart": RESTART_NONE,
    },
    {
        "key": "fallback_providers",
        "attr": "fallback_providers",
        "env_var": "LHASSA_FALLBACK_PROVIDERS",
        "type": "multi",
        "options": list(FALLBACK_PROVIDER_OPTIONS),
        "label": "Fallback provider chain",
        "help": (
            "Ordered providers tried when the primary heartbeat provider fails "
            "(stored as a CSV string). Accepts a list or a CSV string on set."
        ),
        "group": "Heartbeat",
        "default": "claude,codex,default",
        "restart": RESTART_NONE,
    },
    # ── Models / router ──────────────────────────────────────────────────────────
    {
        "key": "llm_provider",
        "attr": "llm_provider",
        "env_var": "LHASSA_LLM_PROVIDER",
        "type": "enum",
        "options": [
            "groq", "anthropic", "openai", "openrouter", "cerebras", "ollama",
            "claude", "codex", "opencode",
        ],
        "label": "Chat provider (CLI / voice / agent)",
        "help": "Powers the CLI chat, voice, and the built-in agent. API providers "
                "(groq/anthropic/…) need their key in .env; claude/codex/opencode run the "
                "LOCAL CLI via your subscription (no API key); ollama is local.",
        "group": "Models",
        "default": "(auto-detected from the key present)",
        "restart": RESTART_NONE,
    },
    {
        "key": "model",
        "attr": "model",
        "env_var": "LHASSA_MODEL",
        "type": "str",
        "options": _model_options(),
        "allow_custom": True,
        "label": "Chat model (CLI / voice / agent)",
        "help": "The model the CLI chat, voice, and the built-in agent use — this is the "
                "'main' model, separate from the background heartbeat. Any model id may be typed.",
        "group": "Models",
        "default": "(provider default)",
        "restart": RESTART_NONE,
    },
    {
        "key": "model_cheap",
        "attr": "model_cheap",
        "env_var": "LHASSA_MODEL_CHEAP",
        "type": "str",
        "options": _model_options(),
        "allow_custom": True,
        "label": "Cheap model (router routine judgment)",
        "help": "Suggested model ids; any model id may still be typed.",
        "group": "Models",
        "default": "(falls back to LHASSA_MODEL)",
        "restart": RESTART_NONE,
    },
    {
        "key": "model_smart",
        "attr": "model_smart",
        "env_var": "LHASSA_MODEL_SMART",
        "type": "str",
        "options": _model_options(),
        "allow_custom": True,
        "label": "Smart model (hard judgment)",
        "help": "Suggested model ids; any model id may still be typed.",
        "group": "Models",
        "default": "(falls back to LHASSA_MODEL)",
        "restart": RESTART_NONE,
    },
    {
        "key": "router_mode",
        "attr": "router_mode",
        "env_var": "LHASSA_ROUTER",
        "type": "enum",
        "options": ["rules", "off"],
        "label": "Router mode",
        "group": "Models",
        "default": "rules",
        "restart": RESTART_NONE,
    },
    # ── Budget & safety ──────────────────────────────────────────────────────────
    {
        "key": "llm_kill_switch",
        "attr": "llm_kill_switch",
        "env_var": "LHASSA_KILL_SWITCH",
        "type": "bool",
        "label": "LLM kill-switch (hard stop)",
        "group": "Budget & Safety",
        "default": False,
        "restart": RESTART_NONE,
    },
    {
        "key": "llm_daily_token_budget",
        "attr": "llm_daily_token_budget",
        "env_var": "LHASSA_LLM_DAILY_TOKEN_BUDGET",
        "type": "int",
        "label": "Daily output-token budget (0 = unlimited)",
        "group": "Budget & Safety",
        "default": 0,
        "restart": RESTART_NONE,
    },
    {
        "key": "pseudonymize_remote",
        "attr": "pseudonymize_remote",
        "env_var": "LHASSA_PSEUDONYMIZE_REMOTE",
        "type": "bool",
        "label": "Pseudonymize PII before remote providers",
        "group": "Budget & Safety",
        "default": True,
        "restart": RESTART_NONE,
    },
    # ── Memory ───────────────────────────────────────────────────────────────────
    {
        "key": "memory_layer4_enabled",
        "attr": "memory_layer4_enabled",
        "env_var": "LHASSA_MEMORY_LAYER4",
        "type": "bool",
        "label": "Memory layer 4 (semantic retrieval)",
        "group": "Memory",
        "default": False,
        "restart": RESTART_APP,
    },
    # ── Surfaces ─────────────────────────────────────────────────────────────────
    {
        "key": "voice_enabled",
        "attr": "voice_enabled",
        "env_var": "LHASSA_VOICE_ENABLED",
        "type": "bool",
        "label": "Voice surface (brain-answered turns)",
        "group": "Surfaces",
        "default": False,
        "restart": RESTART_VOICE,
    },
    {
        "key": "telegram_poll_enabled",
        "attr": "telegram_poll_enabled",
        "env_var": "LHASSA_TELEGRAM_POLL",
        "type": "bool",
        "label": "Telegram inbound polling",
        "group": "Surfaces",
        "default": False,
        "restart": RESTART_TELEGRAM,
    },
    {
        "key": "notify_channels",
        "attr": "notify_channels",
        "env_var": "LHASSA_NOTIFY_CHANNELS",
        "type": "str",
        "label": "Active notify channels (CSV)",
        "group": "Surfaces",
        "default": "telegram,desktop",
        "restart": RESTART_APP,
    },
    # ── Org ──────────────────────────────────────────────────────────────────────
    {
        "key": "org_departments",
        "attr": "org_departments",
        "env_var": "LHASSA_ORG_DEPARTMENTS",
        "type": "str",
        "label": "Default departments (CSV, derived chart)",
        "group": "Org",
        "default": "optimization,documentation,finance",
        "restart": RESTART_NONE,
    },
]

_BY_KEY: dict[str, dict] = {e["key"]: e for e in EDITABLE}

# Chat providers backed by a local CLI (subscription auth, no API key) vs the
# API providers that need their key in .env.
_CLI_CHAT_PROVIDERS = ("claude", "codex", "opencode")
_API_KEY_ATTR = {
    "groq": "groq_api_key",
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
    "cerebras": "cerebras_api_key",
}


def _chat_provider_error(provider: str, config) -> "Optional[str]":
    """Return a reason string if this chat provider can't actually run, else None.

    API providers need their key in ``.env``; the CLI providers (claude/codex/opencode)
    need the binary on PATH; ollama is always allowed (local server checked at call time).
    """
    import shutil

    p = (provider or "").strip().lower()
    if p in _CLI_CHAT_PROVIDERS:
        if shutil.which(p) is None:
            return f"the {p!r} CLI is not installed — install it (it runs via your subscription, no API key)."
        return None
    if p == "ollama":
        return None
    attr = _API_KEY_ATTR.get(p)
    if attr is None:
        return f"unknown chat provider {p!r}."
    if not getattr(config, attr, ""):
        return (
            f"{p!r} needs {attr.upper()} in .env (none is set). Use a provider with a key, "
            f"or pick claude/codex/opencode to chat via the local CLI with no key."
        )
    return None

# Advanced set_env() only accepts names with one of these prefixes (everything else
# is refused) so the "advanced" panel can never write an arbitrary host env var.
ALLOWED_ENV_PREFIXES = ("LHASSA_",)
# A small allowlist of well-known integration credential names the advanced panel may
# set too (always masked on read). These are provider/integration keys from config.py.
ALLOWED_ENV_NAMES = {
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
    "CEREBRAS_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "BRAVE_API_KEY",
    "GITHUB_TOKEN", "MS_TENANT_ID", "MS_CLIENT_ID",
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class SettingsError(ValueError):
    """Raised internally on a bad set; never propagated to callers (turned into an
    error result instead). Subclasses :class:`ValueError` so the API maps it to 400.
    """


# ── secret masking ───────────────────────────────────────────────────────────────


def _is_secret(name: str, entry: Optional[dict] = None) -> bool:
    """Whether a value is secret-shaped and must be masked on read."""
    if entry and entry.get("secret"):
        return True
    upper = (name or "").upper()
    return upper.endswith("_TOKEN") or upper.endswith("_KEY")


def _mask(value: Any) -> str:
    """Mask a secret value to ``"set"``/``"unset"`` — never echo the value."""
    return "set" if value not in (None, "", 0, False) else "unset"


# ── settings.env read-modify-write ───────────────────────────────────────────────


def _read_env_file() -> dict[str, str]:
    """Parse ``settings.env`` into an ordered ``{KEY: value}`` dict (empty if absent)."""
    path = settings_env_path()
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


def _write_env_var(name: str, value: str) -> None:
    """Read-modify-write a single ``KEY=value`` into ``settings.env`` (others kept)."""
    path = settings_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_env_file()
    existing[name] = value
    body = "".join(f"{k}={v}\n" for k, v in existing.items())
    header = (
        "# Lhassa runtime overrides — written by the settings panel / `lhassa config`.\n"
        "# Loaded AFTER .env with override=True. Do NOT put secrets meant for .env here.\n"
    )
    path.write_text(header + body, encoding="utf-8")


def _overridden(env_var: str) -> bool:
    """Whether ``env_var`` is present in our settings.env (i.e. user-overridden)."""
    return env_var in _read_env_file()


# ── type coercion / validation ───────────────────────────────────────────────────


def _coerce(entry: dict, value: Any) -> Any:
    """Coerce ``value`` to the entry's Python type, validating enums. Raises
    :class:`SettingsError` on a bad value."""
    t = entry["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        token = str(value).strip().lower()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
        raise SettingsError(f"{entry['key']!r} expects a boolean, got {value!r}")
    if t == "int":
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise SettingsError(f"{entry['key']!r} expects an integer, got {value!r}") from None
    if t == "float":
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            raise SettingsError(f"{entry['key']!r} expects a number, got {value!r}") from None
    if t == "enum":
        token = str(value).strip()
        options = entry.get("options") or []
        if token not in options:
            raise SettingsError(
                f"{entry['key']!r} must be one of {options}, got {token!r}"
            )
        return token
    if t == "multi":
        # Accept a list/tuple OR a CSV string; normalize to a clean CSV string so the
        # stored/serialized value stays backward-compatible (config reads a CSV).
        if isinstance(value, (list, tuple)):
            parts = [str(v).strip() for v in value]
        else:
            parts = [p.strip() for p in str(value).split(",")]
        parts = [p for p in parts if p]  # drop empties (also makes "" → "")
        return ",".join(parts)
    return str(value)


def _serialize(entry: dict, value: Any) -> str:
    """Render a coerced value to its settings.env string form."""
    if entry["type"] == "bool":
        return "1" if value else "0"
    return str(value)


# ── live Config application ───────────────────────────────────────────────────────


def _apply_live(entry: dict, value: Any) -> None:
    """Update the live :class:`Config` CLASS attribute so the running API reflects the
    change immediately (instances read class attrs). Best-effort; never raises out."""
    from lhassa.config import Config

    setattr(Config, entry["attr"], value)


def _config():
    from lhassa.config import Config

    return Config()


# ── available lists for the panel ────────────────────────────────────────────────


def available() -> dict:
    """Read-only lists the panel needs: provider names, harness adapters, opencode
    model hints, a per-provider model map (so the cheap/smart dropdowns can union
    them), and the enum option sets. No network calls — every list is a static
    ``[SUPUESTO]`` hint."""
    providers: list[str] = []
    harnesses: list[str] = []
    try:
        from lhassa.llm.provider import PROVIDERS

        providers = list(PROVIDERS.names())
    except Exception:  # pragma: no cover - defensive
        providers = []
    try:
        from lhassa.harness.adapter import ADAPTERS

        harnesses = list(ADAPTERS.names())
    except Exception:  # pragma: no cover - defensive
        harnesses = []
    return {
        "providers": providers,
        "harnesses": harnesses,
        # {provider: [model ids]} static hints for the model dropdowns.
        "models": {k: list(v) for k, v in MODEL_HINTS.items()},
        "opencode_models": list(OPENCODE_MODEL_HINTS),
        # Flat union the cheap/smart dropdowns can render directly.
        "model_options": _model_options(),
        "fallback_providers": list(FALLBACK_PROVIDER_OPTIONS),
        "heartbeat_providers": list(_BY_KEY["heartbeat_provider"]["options"]),
        "router_modes": list(_BY_KEY["router_mode"]["options"]),
    }


# ── the read model ───────────────────────────────────────────────────────────────


def _effective_entry(entry: dict, config) -> dict:
    """One editable setting's current value + default + overridden flag (masked)."""
    raw = getattr(config, entry["attr"], None)
    secret = _is_secret(entry["env_var"], entry)
    row = {
        "key": entry["key"],
        "label": entry["label"],
        "group": entry["group"],
        "type": entry["type"],
        "env_var": entry["env_var"],
        "default": entry["default"],
        "overridden": _overridden(entry["env_var"]),
        "secret": secret,
        "restart": entry.get("restart", RESTART_NONE),
    }
    if entry.get("options"):
        row["options"] = list(entry["options"])
    if entry.get("allow_custom"):
        # Frontend hint: render a select/datalist but still allow a typed value.
        row["allow_custom"] = True
    if entry.get("help"):
        row["help"] = entry["help"]
    row["value"] = _mask(raw) if secret else raw
    return row


def effective(config=None) -> dict:
    """The full read model for the panel: every editable setting (grouped, masked)
    plus the read-only ``available`` lists.

    Shape::

        {"settings": [ {key, label, group, type, value, default, overridden,
                        secret, restart, options?}, ... ],
         "groups": ["Heartbeat", "Models", ...],
         "available": {...}}
    """
    config = config or _config()
    rows = [_effective_entry(e, config) for e in EDITABLE]
    groups: list[str] = []
    for e in EDITABLE:
        if e["group"] not in groups:
            groups.append(e["group"])
    return {"settings": rows, "groups": groups, "available": available()}


def get(key: str, config=None) -> dict:
    """One editable setting's effective row, or an error result for an unknown key.

    Never raises — an unknown key returns ``{"key", "error"}``.
    """
    entry = _BY_KEY.get(key)
    if entry is None:
        return {"key": key, "error": f"unknown setting {key!r}"}
    return _effective_entry(entry, config or _config())


def set(key: str, value: Any) -> dict:  # noqa: A001 - deliberate panel verb
    """Validate, persist to settings.env, and apply live a single editable setting.

    Returns ``{"key", "value", "applied", "restart_needed", "restart_hint"}`` on
    success, or ``{"key", "value", "applied": False, "error"}`` on a bad key/value.
    NEVER raises into the caller.
    """
    entry = _BY_KEY.get(key)
    if entry is None:
        return {"key": key, "value": value, "applied": False,
                "error": f"unknown setting {key!r} (not in the editable allowlist)"}
    try:
        coerced = _coerce(entry, value)
    except SettingsError as exc:
        return {"key": key, "value": value, "applied": False, "error": str(exc)}
    # Guard: don't let the chat provider be set to something that can't actually run
    # (an API provider with no key, or a CLI that isn't installed) — that breaks chat.
    if key == "llm_provider":
        err = _chat_provider_error(coerced, _config())
        if err:
            return {"key": key, "value": value, "applied": False, "error": err}
    try:
        _write_env_var(entry["env_var"], _serialize(entry, coerced))
        _apply_live(entry, coerced)
    except Exception as exc:  # pragma: no cover - defensive (fs/perm errors)
        return {"key": key, "value": value, "applied": False, "error": str(exc)}

    restart = entry.get("restart", RESTART_NONE)
    needed = restart != RESTART_NONE
    hint = (
        "Applied live; new heartbeats pick it up automatically — no restart needed."
        if not needed
        else f"Applied live, but restart the '{restart}' service for it to take full effect."
    )
    stored = _mask(coerced) if _is_secret(entry["env_var"], entry) else coerced
    return {
        "key": key,
        "value": stored,
        "applied": True,
        "restart_needed": needed,
        "restart_hint": hint,
    }


def set_env(name: str, value: str) -> dict:
    """Advanced escape hatch: set an arbitrary ``LHASSA_*`` (or known integration)
    env var in settings.env. Refuses any name without an allowed prefix.

    Returns ``{"name", "applied", "secret", ...}``; never raises. Writes only to
    settings.env (never ``.env``) and mirrors into ``os.environ`` so the live process
    sees it; class-level Config attrs are import-time, so most such vars still need a
    service restart to fully apply (reflected in ``restart_hint``).
    """
    name = (name or "").strip()
    if not name:
        return {"name": name, "applied": False, "error": "an env var needs a name"}
    allowed = name in ALLOWED_ENV_NAMES or any(
        name.startswith(p) for p in ALLOWED_ENV_PREFIXES
    )
    if not allowed:
        return {
            "name": name,
            "applied": False,
            "error": (
                f"{name!r} is not an allowed advanced var — must start with one of "
                f"{ALLOWED_ENV_PREFIXES} or be a known integration key."
            ),
        }
    try:
        _write_env_var(name, str(value))
        os.environ[name] = str(value)
    except Exception as exc:  # pragma: no cover - defensive
        return {"name": name, "applied": False, "error": str(exc)}
    secret = _is_secret(name)
    return {
        "name": name,
        "applied": True,
        "secret": secret,
        "value": _mask(value) if secret else str(value),
        "restart_hint": "Written to settings.env; restart the affected service to apply.",
    }
