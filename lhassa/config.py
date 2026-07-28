import os
import platform
import shutil
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
# Runtime overrides written by the settings panel / `lhassa config` live in a
# dedicated file so secrets in ``.env`` are never rewritten. They load AFTER ``.env``
# with ``override=True`` so an edited setting wins; new processes (heartbeat, telegram,
# app) pick them up on next start. Skipped under tests (LHASSA_NO_RUNTIME_OVERRIDES=1,
# set by tests/conftest.py) so the owner's live settings.env never leaks into the suite.
if os.getenv("LHASSA_NO_RUNTIME_OVERRIDES") != "1":
    load_dotenv(Path.home() / ".lhassa" / "settings.env", override=True)

_PROVIDERS = {"groq", "anthropic", "openai", "openrouter", "ollama", "cerebras", "deepseek"}

# Detect the claude CLI once at import time so the default is resolved before
# Config is instantiated and the result is stable across the process lifetime.
_CLAUDE_AVAILABLE = shutil.which("claude") is not None

# Local AI coding CLIs that the run_ai_cli tool can drive, in auto-detect priority.
# Kept here (rather than importing lhassa.agents.local_cli) so config stays free of
# heavier imports; the per-CLI argv table lives in lhassa/agents/local_cli.py.
_AI_CLI_BINARIES = ("claude", "codex", "opencode")


def _detect_ai_cli() -> str:
    """
    Resolve LHASSA_AI_CLI.

    If LHASSA_AI_CLI is set explicitly, use it (lowercased). Otherwise auto-detect
    the first installed CLI in _AI_CLI_BINARIES priority order, falling back to
    "claude" when none is installed (the run_ai_cli tool reports a clear
    not-installed message at call time).
    """
    explicit = os.getenv("LHASSA_AI_CLI")
    if explicit:
        return explicit.strip().lower()
    for cli in _AI_CLI_BINARIES:
        if shutil.which(cli) is not None:
            return cli
    return "claude"

# Default models per provider — used when LHASSA_MODEL is not set.
_DEFAULT_MODELS = {
    "anthropic":  "claude-haiku-4-5-20251001",
    "openai":     "gpt-4o-mini",
    "groq":       "llama-3.3-70b-versatile",
    "openrouter": "anthropic/claude-haiku-4-5-20251001",
    "cerebras":   "llama3.3-70b",
    "ollama":     "llama3.2",
    "deepseek":   "deepseek-v4-flash",
}


def _detect_provider() -> str:
    """
    Resolve LHASSA_LLM_PROVIDER.

    If LHASSA_LLM_PROVIDER is set explicitly, use it.
    Otherwise, auto-detect from whichever API key is present:
      ANTHROPIC_API_KEY → anthropic
      OPENAI_API_KEY    → openai
      GROQ_API_KEY      → groq
      DEEPSEEK_API_KEY  → deepseek
      OPENROUTER_API_KEY → openrouter
      CEREBRAS_API_KEY  → cerebras
    Falls back to "groq" (will fail validate() if no key is set).
    """
    explicit = os.getenv("LHASSA_LLM_PROVIDER")
    if explicit:
        return explicit.lower()
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.getenv("CEREBRAS_API_KEY"):
        return "cerebras"
    return "groq"  # fallback; validate() will catch the missing key


def _detect_model(provider: str) -> str:
    """
    Resolve LHASSA_MODEL. Returns the explicit value if set,
    otherwise the sensible default for the given provider.
    """
    explicit = os.getenv("LHASSA_MODEL")
    if explicit:
        return explicit
    return _DEFAULT_MODELS.get(provider, "llama-3.3-70b-versatile")


def _safe_int(val: str, default: int) -> int:
    """Parse val as int, returning default if it's not a valid integer."""
    try:
        return int(val)
    except ValueError:
        return default


def _safe_float(val: str, default: float) -> float:
    """Parse val as float, returning default if it's not a valid number."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_csv_set(raw: str) -> "set[str]":
    """Parse a CSV env var into a set of trimmed, non-empty tokens."""
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


def _parse_csv_list(raw: str, default: "list[str]") -> "list[str]":
    """Parse a CSV env var into an ORDER-PRESERVING list; empty → default."""
    items = [item.strip() for item in (raw or "").split(",") if item.strip()]
    return items or default


def _parse_repos(raw: str) -> "set[str] | str":
    """Parse LHASSA_ALLOWLIST_REPOS (CSV) into a set, or "*" for "allow any".

    Empty or "*" → wildcard (the dev default); otherwise the set of repo names
    that the task-level allowlist accepts as ``sync_repo`` objetivos.
    """
    raw = (raw or "").strip()
    if not raw or raw == "*":
        return "*"
    return {item.strip() for item in raw.split(",") if item.strip()}


class Config:
    # ── LLM provider (voice pipeline) ─────────────────────────────────────────
    # Which provider powers the voice pipeline and the built-in agent tool loop.
    # Options: groq | anthropic | openai | openrouter | ollama | cerebras
    #
    # Auto-detected from whichever API key is present if LHASSA_LLM_PROVIDER is
    # not set. Priority: anthropic → openai → groq → openrouter → cerebras.
    # This means setting only ANTHROPIC_API_KEY is enough — no LHASSA_LLM_PROVIDER
    # needed, and vision tools will also work automatically.
    llm_provider: str = _detect_provider()

    # Model name — interpreted by whichever provider is selected.
    # Auto-set to the provider default if LHASSA_MODEL is not set:
    #   anthropic  → claude-haiku-4-5-20251001
    #   openai     → gpt-4o-mini
    #   groq       → llama-3.3-70b-versatile
    #   openrouter → anthropic/claude-haiku-4-5-20251001
    #   cerebras   → llama3.3-70b
    #   ollama     → llama3.2
    model: str = _detect_model(llm_provider)

    # ── Provider API keys (only the active provider's key is required) ────────
    groq_api_key: str       = os.getenv("GROQ_API_KEY", "")
    anthropic_api_key: str  = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str     = os.getenv("OPENAI_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    cerebras_api_key: str   = os.getenv("CEREBRAS_API_KEY", "")
    deepseek_api_key: str   = os.getenv("DEEPSEEK_API_KEY", "")

    # Ollama — local, no key needed.
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # ── STT ───────────────────────────────────────────────────────────────────
    deepgram_api_key: str = os.getenv("DEEPGRAM_API_KEY", "")
    # BCP-47 language code for speech recognition. Deepgram nova-3-general
    # supports: en, es, fr, de, pt, nl, hi, ja, and more.
    stt_language: str = os.getenv("LHASSA_STT_LANGUAGE", "en")

    # ── TTS ───────────────────────────────────────────────────────────────────
    cartesia_api_key: str = os.getenv("CARTESIA_API_KEY", "")
    voice_id: str = os.getenv("LHASSA_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091")

    # ── Audio ─────────────────────────────────────────────────────────────────
    # Substring matched against PyAudio device names (case-insensitive).
    # Set to "" to use the system default device.
    # Defaults to "pipewire" on Linux (common audio server), empty elsewhere.
    mic_name: str = os.getenv(
        "LHASSA_MIC_NAME",
        "pipewire" if platform.system() == "Linux" else "",
    )

    # ── Instance identity + brain DB (data spine) ─────────────────────────────
    # One instance, one owner — never an assumed single owner. Every row in the
    # "grande" brain DB carries this id and every query filters by it, so the
    # multi-tenant shape is seeded from day one (personal use keeps one value).
    instance_id: str = os.getenv("LHASSA_INSTANCE_ID", "inst_personal")

    # The "grande": append-only SQLite (WAL) source of truth, separate from the
    # conversational memory DB at ~/.lhassa/memory.db.
    brain_db_path: str = os.getenv(
        "LHASSA_BRAIN_DB_PATH", str(Path.home() / ".lhassa" / "brain.db")
    )

    # The "chica": the persistent working-memory cache, DERIVED from the grande
    # and independently clearable ("matas la chica → se regenera de la grande").
    # Its own small SQLite file so it can be wiped without touching the grande.
    chica_db_path: str = os.getenv(
        "LHASSA_CHICA_DB_PATH", str(Path.home() / ".lhassa" / "chica.db")
    )
    # Threshold at which the chica consolidates (mechanical LRU eviction of the
    # least-recently-used NON-pinned items down to this count). No tokens, no LLM.
    chica_max_items: int = _safe_int(os.getenv("LHASSA_CHICA_MAX_ITEMS", "200"), 200)
    # Phase 2 — semantic consolidation (the cheap-LLM second pass on top of the
    # mechanical LRU above). Default ON, but it only ever FIRES when the chica is
    # over ``chica_max_items`` (threshold-driven, never on a clock). Set to 0 to
    # disable the LLM pass entirely and keep only the mechanical LRU.
    semantic_consolidation: bool = os.getenv(
        "LHASSA_SEMANTIC_CONSOLIDATION", "1"
    ).lower() in ("1", "true", "yes")

    # Incremental backup mirror of the grande (brain.db). Cheap to re-run; the
    # mirror is overwritten/rotated each time. Restorations are verified.
    backup_dir: str = os.getenv(
        "LHASSA_BACKUP_DIR", str(Path.home() / ".lhassa" / "backups")
    )

    # ── Security: signing + task-level allowlist (§2.6) ───────────────────────
    # Ed25519 private key location (public key written alongside as <path>.pub).
    # The brain holds the private key and signs Tasks; a machine holds the public
    # key and verifies them. Local-first: both live here for now.
    signing_key_path: str = os.getenv(
        "LHASSA_SIGNING_KEY_PATH", str(Path.home() / ".lhassa" / "keys" / "ed25519")
    )
    # Repo names the task-level allowlist accepts as `sync_repo` objetivos.
    # CSV → set; "*" (default) means allow any (dev). This is the TASK-level
    # allowlist, separate from the command-level sudo allowlist in tools/privileged.
    allowlist_repos: "set[str] | str" = _parse_repos(
        os.getenv("LHASSA_ALLOWLIST_REPOS", "*")
    )

    # ── Approval frontier + autonomy knob (§2.6 — HITL per flow) ──────────────
    # The autonomy knob lowers approval friction for repeatedly-approved human
    # actions: after this many CONSECUTIVE human-origin approvals of the same
    # (accion, actor) the action auto-approves ("una humano repetida se vuelve
    # automática"). A denial resets the streak. Per-action and per-actor, never
    # global, and it never promotes an Origin's trust level.
    autonomy_threshold: int = _safe_int(
        os.getenv("LHASSA_AUTONOMY_THRESHOLD", "3"), 3
    )
    # High-risk actions that ALWAYS require human approval, no matter the history
    # ("mover-dinero exige aprobación aunque un borrador no"). These are NEVER
    # auto-promoted by the autonomy knob. CSV → set.
    always_approve_actions: "set[str]" = _parse_csv_set(
        os.getenv("LHASSA_ALWAYS_APPROVE_ACTIONS", "move_money,delete_data")
    )

    # ── Health checks ──────────────────────────────────────────────────────────
    # Directory NAMES under $HOME that `health/checks.py:git_repos_under` scans
    # for git repos to report on (uncommitted/unpushed work). CSV, order-preserving;
    # default is just the one generic project root every dev box has.
    watch_repo_dirs: "list[str]" = _parse_csv_list(
        os.getenv("LHASSA_WATCH_REPO_DIRS", "Code"), ["Code"]
    )

    # ── Integrations: MCP (§2.11) — "plug in, don't program" ──────────────────
    # Server definitions for the MCP client. JSON shape:
    #   {"servers": {"<name>": {"type":"stdio","command":[...]}
    #                        | {"type":"url","url":"https://…"}}}
    # Missing file → zero MCP tools (the brain runs fine with none). Adding an
    # integration is editing this file, not writing code.
    mcp_config_path: str = os.getenv(
        "LHASSA_MCP_CONFIG_PATH",
        str(Path.home() / ".config" / "lhassa" / "mcp.json"),
    )

    # ── General ───────────────────────────────────────────────────────────────
    log_level: str = os.getenv("LHASSA_LOG_LEVEL", "INFO")
    debug: bool = os.getenv("LHASSA_DEBUG", "").lower() in ("1", "true", "yes")

    # Seconds of silence before Lhassa goes back to sleep after being woken.
    # Default: 300 (5 minutes). Set lower to save API costs on idle sessions.
    wake_timeout: float = float(os.getenv("LHASSA_WAKE_TIMEOUT", "300"))

    # ── LLM gateway (cost control) ────────────────────────────────────────────
    # Single chokepoint for cloud-LLM calls: cache + budget + cost ledger.
    # Daily output-token budget; 0 = unlimited. When exceeded, the gateway
    # refuses further calls until the next day (UTC).
    llm_daily_token_budget: int = _safe_int(
        os.getenv("LHASSA_LLM_DAILY_TOKEN_BUDGET", "0"), 0
    )
    # Cache identical prompts so repeat questions never hit the cloud.
    llm_cache_enabled: bool = os.getenv("LHASSA_LLM_CACHE", "1").lower() in (
        "1", "true", "yes",
    )
    # Cache time-to-live in days: a stored answer older than this is treated as a
    # miss (the cloud is hit again) and expired rows are opportunistically purged.
    # Default 7; 0 = never expire (the old unbounded behaviour).
    llm_cache_ttl_days: int = _safe_int(
        os.getenv("LHASSA_LLM_CACHE_TTL_DAYS", "7"), 7
    )
    # Extra/override model pricing merged OVER the built-in gateway table at init —
    # a JSON object {"model-name": [input_per_1M, output_per_1M]} so the owner can
    # price any model without a code edit. Empty = built-in table only. Malformed
    # JSON is logged and ignored (never crashes).
    pricing_json: str = os.getenv("LHASSA_PRICING_JSON", "")
    # Kill-switch — a hard manual stop, independent of the daily token budget.
    # When on, the gateway refuses every cloud-LLM call before it reaches a
    # provider. Default off; flip it at runtime from the dashboard. This is the
    # boot default; gateway.set_kill_switch() overrides it for the live process.
    llm_kill_switch: bool = os.getenv("LHASSA_KILL_SWITCH", "").lower() in (
        "1", "true", "yes", "on",
    )

    # ── Router (§2.4) — two axes + cascade ────────────────────────────────────
    # "rules" (default) → pick cheap/smart by hardness, route sensitive data to a
    # zero-retention provider. "off" → always use ``model`` (no routing).
    router_mode: str = os.getenv("LHASSA_ROUTER", "rules").lower()
    # Cheap model for routine judgment / smart model for hard judgment. Both
    # default to ``model`` so a single-model setup needs no extra config.
    model_cheap: str = os.getenv("LHASSA_MODEL_CHEAP", "") or model
    model_smart: str = os.getenv("LHASSA_MODEL_SMART", "") or model

    # Token budget for the layered memory load when the brain wakes to think.
    context_token_budget: int = _safe_int(
        os.getenv("LHASSA_CONTEXT_TOKEN_BUDGET", "2000"), 2000
    )
    # Token budget for a "minimal" context load — the tier the deterministic
    # policy layer (core/policy.py) requests for routine/default judgment (no
    # reflex flagged it hard). Cheaper than the full budget above; fewer recent
    # episodes are pulled too (see core/step.py's _judge tier handling).
    context_token_budget_minimal: int = _safe_int(
        os.getenv("LHASSA_CONTEXT_TOKEN_BUDGET_MINIMAL", "600"), 600
    )

    # ── Deterministic policy layer ("reflexes", core/policy.py) ───────────────
    # Repos that reconcile themselves automatically (sync_repo, no judgment) the
    # moment the watchlist sees a new commit. CSV of absolute paths; empty = no
    # repo auto-syncs (every repo_changed trigger routes to judgment, as before).
    auto_sync_repos: str = os.getenv("LHASSA_AUTO_SYNC_REPOS", "")

    # ── Budget soft-limit (router downgrade) ──────────────────────────────────
    # Once today's spend crosses this fraction of the daily token budget, the
    # router never picks the smart model for a "hard" judgment (it downgrades to
    # the cheap/routine path, which also prefers $0 providers) — a soft brake
    # before BudgetExceeded hits outright. Only meaningful when
    # llm_daily_token_budget > 0 (0 = unlimited, so no soft limit either).
    budget_soft_pct: float = _safe_float(
        os.getenv("LHASSA_BUDGET_SOFT_PCT", "0.8"), 0.8
    )

    # ── Deferred-task retry sweep (resilience) ────────────────────────────────
    # A task the judgment path could not run (no provider / budget spent) is
    # marked "diferida". Each heartbeat re-queues a "diferida" task back to
    # "pendiente" once it has sat for at least this many seconds, so a transient
    # outage/budget window doesn't strand it forever.
    deferred_retry_backoff_seconds: float = _safe_float(
        os.getenv("LHASSA_DEFERRED_RETRY_BACKOFF", "300"), 300.0
    )
    # After this many retries a still-deferred task is marked "rechazada"
    # (terminal — never retried again) instead of requeued indefinitely.
    deferred_max_retries: int = _safe_int(
        os.getenv("LHASSA_DEFERRED_MAX_RETRIES", "3"), 3
    )

    # ── Dashboard API ─────────────────────────────────────────────────────────
    # Bind to localhost only by default. If you expose the API beyond loopback,
    # set LHASSA_API_TOKEN; unauthenticated non-loopback requests are rejected.
    api_host: str = os.getenv("LHASSA_API_HOST", "127.0.0.1")
    # Dedicated port for lhassa (uncommon, unlikely to clash).
    api_port: int = _safe_int(os.getenv("LHASSA_API_PORT", "7766"), 7766)
    # Optional bearer token for the REST API and WebSocket. Local loopback access
    # stays usable without a token; network access requires this token.
    api_token: str = os.getenv("LHASSA_API_TOKEN", "")
    # CSV list of browser origins allowed to call the API. Empty = localhost dev
    # origins plus this API port. Avoid "*" because this process can mutate state.
    api_cors_origins: str = os.getenv("LHASSA_API_CORS_ORIGINS", "")

    # ── Microsoft Graph — Outlook email + calendar (optional) ─────────────────
    ms_tenant_id: str = os.getenv("MS_TENANT_ID", "")
    ms_client_id: str = os.getenv("MS_CLIENT_ID", "")

    # ── Calendar ICS feed (read-only, no admin/API) ──────────────────────────
    # A published calendar's secret ICS URL — subscribing makes /today and the
    # briefing show your real events. Read-only; needs no app registration
    # (works for institutional calendars an admin would otherwise gate). SECRET.
    calendar_ics_url: str = os.getenv("LHASSA_CALENDAR_ICS_URL", "")
    # How long a fetched feed is cached before re-download (seconds; 1h default).
    calendar_feed_ttl: float = _safe_float(os.getenv("LHASSA_CALENDAR_FEED_TTL", "3600"), 3600.0)
    # Multiple NAMED calendars (institutional/personal/project/...) — a JSON list
    # of {"name", "url"} at this path; see lhassa/calendar/store.py. The single
    # LHASSA_CALENDAR_ICS_URL above stays a convenience shortcut merged in as an
    # implicit "default" feed — the store is for anyone with MORE than one.
    calendars_file: str = os.getenv(
        "LHASSA_CALENDARS_FILE", str(Path.home() / ".lhassa" / "calendars.json")
    )

    # ── Web search ────────────────────────────────────────────────────────────
    # Brave Search API key — optional, falls back to DuckDuckGo if not set.
    brave_api_key: str = os.getenv("BRAVE_API_KEY", "")

    # ── Agent backend ─────────────────────────────────────────────────────────
    # "claude_code" — agents run via `claude --print` (your Claude subscription).
    # "builtin"     — agents use the internal LLM tool loop (LHASSA_LLM_PROVIDER).
    #
    # If LHASSA_AGENT_BACKEND is not set, Lhassa auto-detects:
    #   - claude CLI found in PATH  → claude_code
    #   - claude CLI not found      → builtin
    agent_backend: str = os.getenv(
        "LHASSA_AGENT_BACKEND",
        "claude_code" if _CLAUDE_AVAILABLE else "builtin",
    ).lower()

    # Working directory for `claude --print`.
    # Only used when agent_backend=claude_code.
    # Defaults to the cwd where Lhassa was launched if not set.
    agent_dir: str = os.getenv("LHASSA_AGENT_DIR", "")

    # ── Local AI coding CLI (run_ai_cli tool) ─────────────────────────────────
    # Which local AI coding CLI the run_ai_cli tool dispatches tasks to, taking
    # advantage of the user's existing subscription. Options: claude | codex | opencode.
    # Auto-detected from the first installed CLI if LHASSA_AI_CLI is not set.
    # This is independent of agent_backend and never affects it.
    ai_cli: str = _detect_ai_cli()

    # ── Delegated code work — harness override (Axis 2, §2.2/§2.4) ─────────────
    # Force which HarnessAdapter the router delegates code work to: opencode |
    # claude | codex. Empty → the router auto-picks the cheapest available muscle
    # (free/metered before subscription). This is the LHASSA_HARNESS override.
    harness: str = os.getenv("LHASSA_HARNESS", "").strip().lower()

    # ── Vision tools ──────────────────────────────────────────────────────────
    # Provider for analyze_screen / analyze_image.
    # "auto"       — use the active LLM provider if it supports vision, else
    #                fall back to ANTHROPIC_API_KEY → OPENAI_API_KEY
    # "anthropic"  — Claude Haiku (fast, great vision)
    # "openai"     — GPT-4o-mini
    # "openrouter" — any OpenRouter vision model
    vision_provider: str = os.getenv("LHASSA_VISION_PROVIDER", "auto").lower()

    # Vision model override. Leave empty to use the provider default:
    #   anthropic  → claude-haiku-4-5-20251001
    #   openai     → gpt-4o-mini
    #   openrouter → anthropic/claude-haiku-4-5-20251001
    vision_model: str = os.getenv("LHASSA_VISION_MODEL", "")

    # ── GitHub (optional) ─────────────────────────────────────────────────────
    github_token: str = os.getenv("GITHUB_TOKEN", "")

    # ── Tool versioning (§2.8 — "versionado/rollback gratis") ─────────────────
    # Local git repo holding auto-created tools. Each candidate tool is committed
    # on a ``candidate/<name>`` branch; "aprobación = merge" merges it into main;
    # rollback is a git revert. Offline-first: this is a plain local repo. When
    # ``github_token`` is set the versioning layer ALSO pushes the branch and opens
    # a PR (the remote path) — but never requires a token to work locally.
    tools_repo_dir: str = os.getenv(
        "LHASSA_TOOLS_REPO_DIR", str(Path.home() / ".lhassa" / "tools")
    )

    # ── Phase 6 — local heartbeat model (§3-Fase 6, invariant #3) ─────────────
    # Migrate the heartbeat off the cloud onto a LOCAL model behind the same
    # ModelProvider interface. ``local_model_url`` is an OpenAI-compatible base URL
    # (Ollama ``http://127.0.0.1:11434/v1``, llama.cpp/LM Studio, etc.); empty =
    # no local backend configured (the provider degrades gracefully). ``local_model``
    # is the served model name. A local model needs no API key.
    local_model_url: str = os.getenv("LHASSA_LOCAL_MODEL_URL", "")
    local_model: str = os.getenv("LHASSA_LOCAL_MODEL", "")
    # Which provider the heartbeat/routine path prefers: ``auto`` uses the local
    # model when ``local_model_url`` is set (else cloud); ``local`` forces it;
    # ``cloud`` keeps the gateway. The smart/hard path is unaffected.
    heartbeat_provider: str = os.getenv("LHASSA_HEARTBEAT_PROVIDER", "auto").lower()

    # ── Phase 6 — privacy / pseudonymization hook (§3-Fase 0/6) ───────────────
    # Pseudonymize PII before sending context to a NON-local, non-zero-retention
    # provider (the mandatory hook while the heartbeat used free cloud models).
    # When the active provider is local the data never leaves the machine, so the
    # hook RELAXES (no scrubbing) — set by the local provider, overridable here.
    pseudonymize_remote: bool = os.getenv("LHASSA_PSEUDONYMIZE_REMOTE", "1") not in ("0", "false", "")

    # ── Phase 6 — memory layer 4 (semantic retrieval, "si se ganó") ───────────
    # The embeddings layer deferred since Phase 1. Off unless explicitly enabled.
    # When on, ``load()`` adds a semantic layer: facts whose embedding is closest to
    # the active query, after pinned/recent/tags. ``embedding_model`` names an
    # embedding model on the local/cloud provider; empty → a deterministic offline
    # fallback embedder (hashing) so the layer works with no model configured.
    memory_layer4_enabled: bool = os.getenv("LHASSA_MEMORY_LAYER4", "1") not in ("0", "false", "")
    embedding_model: str = os.getenv("LHASSA_EMBEDDING_MODEL", "")
    memory_semantic_topk: int = int(os.getenv("LHASSA_MEMORY_SEMANTIC_TOPK", "5"))

    # Phase 5 — dedicated embeddings endpoint (separate from the LLM endpoint).
    # Points to an OpenAI-compatible /v1/embeddings endpoint; defaults to a local
    # Ollama instance. Any connection/HTTP failure degrades silently to the offline
    # HashingEmbedder — memory loading never crashes.
    embeddings_url: str = os.getenv("LHASSA_EMBEDDINGS_URL", "http://localhost:11434")
    embeddings_model: str = os.getenv("LHASSA_EMBEDDINGS_MODEL", "nomic-embed-text")
    # Embedder backend gate (memory/embeddings.get_embedder): "auto" probes the
    # local Ollama and uses its NATIVE /api/embed when reachable (else offline
    # hashing); "ollama" forces native (degrades with a warning); "hash" forces
    # the offline embedder. The explicit embeddings_model/url knobs above still
    # win when set — this gate matters when they are cleared.
    embedder_backend: str = os.getenv("LHASSA_EMBEDDER_BACKEND", "auto")
    embedder_model: str = os.getenv("LHASSA_EMBEDDER_MODEL", "nomic-embed-text")
    # Knowledge-graph context layer (LightRAG-lite, Wave 3): OFF by default —
    # behavior is unchanged until the owner opts in (same rollout as layer 4).
    memory_graph_enabled: bool = os.getenv("LHASSA_MEMORY_GRAPH", "0").strip().lower() in ("1", "true", "yes", "on")
    # API key for hosted embedding endpoints (Ollama is keyless; set for cloud).
    # NEVER write this to settings.env — it stays in .env (secret).
    embeddings_api_key: str = os.getenv("LHASSA_EMBEDDINGS_API_KEY", "")

    # ── Document perception — knowledge roots (opt-in, empty by default) ──────
    # Lhassa reads the OWNER'S OWN documents so thinking/recall/the graph are
    # grounded in real material — $0, local, deterministic, no model. NOT "read
    # the whole PC": the owner points Lhassa at a small set of KNOWLEDGE ROOTS
    # (CSV of directories, ``~``-relative allowed); nothing is indexed until
    # this is set. A hard, non-negotiable ignore-list (secrets/noise) always
    # applies regardless of what's inside a root — see
    # ``lhassa.memory.knowledge``. The index itself lives in the grande
    # (``~/.lhassa/brain.db``), never in the repo.
    knowledge_roots: str = os.getenv("LHASSA_KNOWLEDGE_ROOTS", "")
    # Second tier: folders the brain MAY read but only with the owner's explicit
    # per-access approval (each read/list parks an approval — the tap IS the
    # verification). Same CSV shape as knowledge_roots; may overlap it — the
    # protected tier wins.
    protected_roots: str = os.getenv("LHASSA_PROTECTED_ROOTS", "")
    # Files larger than this are skipped outright (KB).
    knowledge_max_file_kb: int = _safe_int(os.getenv("LHASSA_KNOWLEDGE_MAX_FILE_KB", "512"), 512)
    # Chunk size in characters (~500 tokens at the ~3-4 chars/token estimate
    # used elsewhere in memory/load.py).
    knowledge_chunk_chars: int = _safe_int(os.getenv("LHASSA_KNOWLEDGE_CHUNK_CHARS", "2000"), 2000)

    # ── Phase 6 — Telegram notifications (optional, token-gated) ──────────────
    # Polished Telegram channel for alerts/reports. Both must be set to enable it;
    # otherwise the notifier is a no-op (the established credential-gated pattern,
    # like ``github_token``). No token → never touches the network.
    telegram_bot_token: str = os.getenv("LHASSA_TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("LHASSA_TELEGRAM_CHAT_ID", "")
    # Inbound: long-poll Telegram for messages and answer them. Off by default;
    # requires the bot token + chat id above. Only the configured chat is honored.
    telegram_poll_enabled: bool = os.getenv("LHASSA_TELEGRAM_POLL", "0") not in ("0", "false", "")
    # Provider for the Telegram CHAT reply specifically (the tool-less brain think).
    # Empty/"auto" → the router's routine decision (the heartbeat seat), as always.
    # "default" pins the API cost gateway — ~1.5s replies vs ~9s for a cold CLI
    # boot per message — WITHOUT touching what flows / hard work think with. The
    # fallback chain still backs a failing pick, so a bad value degrades, never breaks.
    telegram_provider: str = os.getenv("LHASSA_TELEGRAM_PROVIDER", "")

    # ── Chat memory (rolling per-chat conversation history) ──────────────────
    # How many user+assistant PAIRS of a Telegram chat are kept in the in-memory
    # rolling history and threaded back into the next brain think as ``messages``
    # (the deque itself effectively caps at 2x this many entries). Restarting the
    # poller clears history (it is process-memory only, never persisted to disk).
    telegram_history_turns: int = _safe_int(
        os.getenv("LHASSA_TELEGRAM_HISTORY_TURNS", "8"), 8
    )
    # A history entry older than this many seconds is dropped on read (not
    # necessarily evicted eagerly) — an old conversation never leaks into a new one.
    telegram_history_ttl: float = _safe_float(
        os.getenv("LHASSA_TELEGRAM_HISTORY_TTL", "7200"), 7200.0
    )
    # Every this many user turns, the chat system prompt gets one extra line
    # nudging the model to mark a durable fact it learned about the owner with
    # ``[[remember: ...]]`` (see notify/telegram_inbound.py). Not every turn, so
    # the nudge doesn't dominate the instruction set.
    chat_memory_nudge_turns: int = _safe_int(
        os.getenv("LHASSA_CHAT_MEMORY_NUDGE_TURNS", "6"), 6
    )

    # ── The "$0 intelligence" answer cache (notify/intent.py + memory/answer_cache.py) ──
    # A deterministic intent classifier gates a semantic cache of past chat answers to
    # STABLE questions ("what's the capital of France") so a repeat ask is served free
    # instead of spending another model call. Time-sensitive questions (news/prices/
    # weather/"today") are never cached — see notify/intent.py's live_data bucket.
    answer_cache_enabled: bool = os.getenv("LHASSA_ANSWER_CACHE", "1") not in (
        "0", "false", "",
    )
    # Cosine-similarity floor for a cache hit — high on purpose (0.90) so a
    # merely-similar-but-different question is never served the wrong past answer.
    answer_cache_threshold: float = _safe_float(
        os.getenv("LHASSA_ANSWER_CACHE_THRESHOLD", "0.90"), 0.90
    )
    # A cached row older than this many seconds is ignored on lookup (default 7
    # days) — even a "cacheable" question's answer can go stale over time.
    answer_cache_ttl: float = _safe_float(
        os.getenv("LHASSA_ANSWER_CACHE_TTL", str(7 * 24 * 3600)), 7 * 24 * 3600.0
    )

    # ── Phase 6 — heartbeat via opencode (no local model server needed) ───────
    # The owner has the opencode CLI (no Zen), so the routine/heartbeat thinking can
    # run through opencode instead of a local model server. ``OpenCodeProvider`` shells
    # ``opencode run --format json -m <model> <prompt>`` and uses the owner's existing
    # opencode credentials. Set ``heartbeat_provider=opencode`` (or leave it ``auto``,
    # which prefers opencode when no ``local_model_url`` is set and opencode is on PATH).
    opencode_bin: str = os.getenv("LHASSA_OPENCODE_BIN", "opencode")
    # A cheap/free model is right for a routine beat (e.g. a free flash tier).
    opencode_model: str = os.getenv("LHASSA_OPENCODE_MODEL", "opencode/deepseek-v4-flash-free")
    # Claude Code CLI (``claude`` provider): the model to request. Empty ⇒ the CLI's
    # own default subscription model. The provider ONLY forwards a Claude model, so a
    # foreign model from the router (e.g. a groq name) falls back to this.
    claude_bin: str = os.getenv("LHASSA_CLAUDE_BIN", "claude")
    claude_model: str = os.getenv("LHASSA_CLAUDE_MODEL", "")
    # LaTeX engine used to compile a tailored CV ``.tex`` into a PDF before sending it
    # over Telegram. ``tectonic`` by default (self-contained, non-interactive); resolved
    # via ``shutil.which`` so a machine without it degrades to sending the raw ``.tex``.
    tectonic_bin: str = os.getenv("LHASSA_TECTONIC_BIN", "tectonic")

    # Fallback provider CHAIN for chat/think when the routine provider flakes
    # (opencode boots a runtime per call and is flaky under a long-running poller).
    # Tried IN ORDER after the routine provider; each name resolves through the
    # PROVIDERS registry. Unavailable CLIs (claude/codex not installed) are skipped.
    # "default" = the cloud gateway. Order keeps the owner on their subscriptions
    # (claude/codex) before paying for cloud.
    fallback_providers: str = os.getenv("LHASSA_FALLBACK_PROVIDERS", "claude,codex,default")

    # ── Phase 6 — voice (brain ↔ existing pipecat skeleton) ───────────────────
    # Opt-in bridge so the voice pipeline's turns are answered by the brain (memory
    # + provider + episode) instead of a generic LLM. Off by default; needs the
    # Deepgram/Cartesia keys above to actually run audio.
    voice_enabled: bool = os.getenv("LHASSA_VOICE_ENABLED", "0") not in ("0", "false", "")

    # ── Phase 7 — organizational layer (general + department brains) ──────────
    # Standalone by default (empty role). ``general`` = the coordinator brain (routes
    # typed tasks, never executes department work); ``department`` = an executor brain
    # with its own identity/memory/permissions. Start with TWO brains, never six.
    org_role: str = os.getenv("LHASSA_ORG_ROLE", "").strip().lower()  # ""|general|department
    org_department: str = os.getenv("LHASSA_ORG_DEPARTMENT", "")      # this brain's department name
    # Elastic org: the department set is CONFIG-DRIVEN (comma-separated), not code —
    # scale the org up/down without editing the registry.
    org_departments: str = os.getenv(
        "LHASSA_ORG_DEPARTMENTS", "optimization,documentation,finance"
    )
    # The org CHART/topology lives in this file (JSON): 1 standalone brain up to an
    # arbitrary-depth hierarchy the owner creates/manages (via `lhassa org`). Missing
    # file → derived from org_role/org_departments (a flat general+departments chart).
    org_topology_file: str = os.getenv(
        "LHASSA_ORG_TOPOLOGY_FILE", str(Path.home() / ".lhassa" / "org.json")
    )
    # Lhassa is the PERMANENT root administrator — the default/main brain that always
    # sits at the top of any org and is never removed or demoted ("orbits everything").
    # Reorganizing or adding brains happens beneath it.
    root_admin_id: str = os.getenv("LHASSA_ROOT_ADMIN", "lhassa")
    # Typed org RELATIONSHIPS overlay (reads/notifies/reviews/collaborates) live here —
    # a graph separate from the command tree (who-commands-whom stays in org.json).
    org_relationships_file: str = os.getenv(
        "LHASSA_ORG_RELATIONSHIPS_FILE", str(Path.home() / ".lhassa" / "relationships.json")
    )
    # Work-flows (trigger → task → gate → output graphs) are stored here.
    flows_file: str = os.getenv(
        "LHASSA_FLOWS_FILE", str(Path.home() / ".lhassa" / "flows.json")
    )
    # The stored work-flow id the Telegram → CV bridge runs when the owner sends a CV
    # document (the "Impossible-to-reject CV" work-flow). Looked up via FlowStore; a
    # missing flow degrades to a "not set up yet" reply, never a crash.
    cv_flow_id: str = os.getenv("LHASSA_CV_FLOW_ID", "flow-zvzpry")

    # ── Skills (first-class, SKILL.md format) ─────────────────────────────────
    # Skills live as folders here, each with a SKILL.md (YAML frontmatter
    # name/description/metadata + markdown instructions + optional resources) — the
    # skills.md / Agent-Skills convention — or a home-grown JSON form. Brains
    # reference skills by name in BrainNode.skills.
    skills_dir: str = os.getenv(
        "LHASSA_SKILLS_DIR", str(Path.home() / ".lhassa" / "skills")
    )
    # Curator (Fase 2) lifecycle thresholds: no use for this many days -> "stale";
    # stale AND unused for this many more -> "archived" (moved into
    # ``<skills_dir>/.archive/``, never deleted). See ``skills/curator.py``.
    skill_stale_days: int = _safe_int(os.getenv("LHASSA_SKILL_STALE_DAYS", "30"), 30)
    skill_archive_days: int = _safe_int(os.getenv("LHASSA_SKILL_ARCHIVE_DAYS", "90"), 90)

    # ── Modularity — registries, channels, plugins (elastic & modular) ────────
    # Which notify CHANNELS are active (comma-separated); each is a registered,
    # swappable channel. Empty entries are ignored; unknown names are skipped with
    # a warning. Default: Telegram + desktop notifications.
    notify_channels: str = os.getenv("LHASSA_NOTIFY_CHANNELS", "telegram,desktop")
    # Drop-in plugin directory: modules placed here are imported at startup so they
    # can self-register providers/harnesses/channels/departments/actions WITHOUT
    # editing core. Empty/missing → no external plugins loaded.
    plugins_dir: str = os.getenv(
        "LHASSA_PLUGINS_DIR", str(Path.home() / ".lhassa" / "plugins")
    )

    # ── Monitor watcher (the always-on, $0 "ear") ────────────────────────────
    # A standing, deterministic loop that watches the environment cheaply (no
    # tokens) and only wakes the brain / alerts when something notable happens.
    # OFF by default — opt in via the systemd service (scripts/systemd/
    # lhassa-monitor.service). The module no-ops + exits when this is off.
    monitor_enabled: bool = os.getenv("LHASSA_MONITOR_ENABLED", "0") not in ("0", "false", "")
    # Cadence: while nothing is running it checks every idle interval; while a run
    # IS executing it listens on the short active interval (seconds).
    monitor_idle_interval: float = _safe_float(
        os.getenv("LHASSA_MONITOR_IDLE_INTERVAL", "300"), 300.0
    )
    monitor_active_interval: float = _safe_float(
        os.getenv("LHASSA_MONITOR_ACTIVE_INTERVAL", "15"), 15.0
    )
    # The department the watcher attributes its episodes/alerts to.
    monitor_department: str = os.getenv("LHASSA_MONITOR_DEPARTMENT", "interns")
    # Push a Telegram/desktop alert for important events (failures / health-alert /
    # pending-approvals). On by default; episodes are always logged regardless.
    monitor_notify: bool = os.getenv("LHASSA_MONITOR_NOTIFY", "1") not in ("0", "false", "")
    # Optionally spend a token on a richer live "think" per reaction — the ONLY
    # place the watcher can cost anything. OFF by default; also needs LHASSA_ORG_LIVE.
    monitor_think: bool = os.getenv("LHASSA_MONITOR_THINK", "0") not in ("0", "false", "")
    # When on, react() turns each delegation from the monitor's report (offline or
    # live) into a minted org_run task — findings become work. Uses the offline,
    # deterministic report by default (still $0); de-duped by fingerprint so a
    # standing condition is not re-minted every tick. OFF by default.
    monitor_delegate: bool = os.getenv("LHASSA_MONITOR_DELEGATE", "0") not in ("0", "false", "")
    # A failure only counts while it is FRESH: episodes older than this many hours
    # never enter the failure signal, so an alert's "total N" means "N current",
    # not "N ever seen in the episode window". 0 disables the age cutoff.
    monitor_failure_window_h: float = _safe_float(
        os.getenv("LHASSA_MONITOR_FAILURE_WINDOW_H", "24"), 24.0
    )

    # ── Phase 2 — judgment dispatch (close the think-loop) ────────────────────
    # When on (default), the judgment branch parses the model's JSON reply and
    # writes facts + mints tasks through the security machinery. Set to 0 to
    # disable (today's behavior: log the answer, do nothing else).
    judgment_dispatch_enabled: bool = os.getenv(
        "LHASSA_JUDGMENT_DISPATCH", "1"
    ).lower() in ("1", "true", "yes")

    # ── Phase 4 — watchlist senses ────────────────────────────────────────────
    # Comma-separated absolute paths of git repos to watch for new commits.
    # Empty = no repo watching. Each path is checked via ``git rev-parse HEAD``
    # (subprocess, timeout 5 s, never raises). Missing or non-repo paths are
    # skipped silently.
    watch_repos: str = os.getenv("LHASSA_WATCH_REPOS", "")

    # JSON file of inbound Telegram message rules (loaded by the Telegram poller).
    # Schema: [{"pattern": "<regex>", "accion": "<str>", "params": {}}]
    # An inbound owner message matching a pattern mints the action via dispatch.mint_task
    # BEFORE the normal command/brain fallback. Missing or malformed file → off.
    watch_rules_file: str = os.getenv(
        "LHASSA_WATCH_RULES_FILE",
        str(Path.home() / ".lhassa" / "watch_rules.json"),
    )

    # JSON file of the self-routing "capability -> deterministic handler" table
    # (the self-routing consciousness — see lhassa.notify.capabilities). An
    # approved "mint_route" self-improvement proposal adds an entry here (DATA,
    # not code); lhassa.notify.capabilities.apply_route reads it to serve a
    # recognised message for $0 instead of a think/research. Missing/malformed
    # file -> no routes configured (feature silently off).
    routes_file: str = os.getenv(
        "LHASSA_ROUTES_FILE",
        str(Path.home() / ".lhassa" / "routes.json"),
    )

    # ── Etapa 2 — scheduled operative digest (the "digest" seed) ─────────────
    # Local hours (0-23) the twice-daily "send_digest" task is scheduled for by
    # the ``digest`` seed (``lhassa seed apply digest``). Two knobs rather than one
    # CSV so each is independently overridable without a parsing helper.
    digest_hour_morning: int = _safe_int(os.getenv("LHASSA_DIGEST_HOUR_MORNING", "8"), 8)
    digest_hour_evening: int = _safe_int(os.getenv("LHASSA_DIGEST_HOUR_EVENING", "21"), 21)
    # How the digest reads: "plain" → the deterministic $0 template (already in
    # Lhassa's first-person voice); "persona" → one cheap fast-model pass re-voices
    # it naturally (same facts; ANY failure degrades to plain — it always arrives).
    digest_style: str = os.getenv("LHASSA_DIGEST_STYLE", "plain")
    # Language for the persona pass ("" keeps the template's language; e.g. "es").
    digest_language: str = os.getenv("LHASSA_DIGEST_LANGUAGE", "")
    # Add a short owner-facing personal check-in to the scheduled digest.
    # Deterministic and $0: morning asks about sleep/day shape; evening asks what
    # to remember for tomorrow. Future versions can enrich this from agenda/memory.
    digest_personal_checkin: bool = os.getenv(
        "LHASSA_DIGEST_PERSONAL_CHECKIN", ""
    ).lower() in ("1", "true", "yes")

    # ── Tool guardrails ───────────────────────────────────────────────────────
    # If true, reject obviously destructive shell commands unless explicitly
    # opted out via LHASSA_ALLOW_DESTRUCTIVE_SHELL=1.
    allow_destructive_shell: bool = os.getenv(
        "LHASSA_ALLOW_DESTRUCTIVE_SHELL", ""
    ).lower() in ("1", "true", "yes")

    # Optional filesystem root for write_file. If set, write_file may only
    # create or modify files inside this directory tree.
    file_write_root: str = os.getenv("LHASSA_FILE_WRITE_ROOT", "")

    # ── Self-improvement — repeated-think detector (§2.8, visible self-improvement) ──
    # How many times a (despertar_por, accion) pattern must repeat within the
    # detection window before agents.repetition.detect_repetition proposes minting
    # a deterministic action to replace it.
    repetition_threshold: int = _safe_int(
        os.getenv("LHASSA_REPETITION_THRESHOLD", "5"), 5
    )

    def validate(self):
        """
        Check all active configuration at startup. Raises RuntimeError with a
        clear, actionable message on the first problem found.
        Logs the resolved agent backend so users always know what's active.
        """
        from loguru import logger

        # ── Required keys ─────────────────────────────────────────────────────
        if not self.deepgram_api_key:
            raise RuntimeError(
                "Missing required environment variable: DEEPGRAM_API_KEY\n"
                "Sign up at console.deepgram.com and add the key to your .env file."
            )
        if not self.cartesia_api_key:
            raise RuntimeError(
                "Missing required environment variable: CARTESIA_API_KEY\n"
                "Sign up at console.cartesia.ai and add the key to your .env file."
            )

        # ── Port ───────────────────────────────────────────────────────────────
        port_raw = os.getenv("LHASSA_API_PORT", "7766")
        try:
            int(port_raw)
        except ValueError:
            raise RuntimeError(
                f"LHASSA_API_PORT={port_raw!r} is not a valid port number.\n"
                "Set it to an integer between 1024 and 65535."
            )

        # ── LLM provider ──────────────────────────────────────────────────────
        provider = self.llm_provider
        auto_provider = not os.getenv("LHASSA_LLM_PROVIDER")
        if auto_provider:
            logger.info(f"LLM provider: {provider} (auto-detected from API key)  model={self.model}")
        else:
            logger.info(f"LLM provider: {provider}  model={self.model}")

        if provider not in _PROVIDERS:
            raise RuntimeError(
                f"Unknown LLM provider: {provider!r}. "
                f"Valid options: {', '.join(sorted(_PROVIDERS))}"
            )

        key_map = {
            "groq":       ("groq_api_key",       "GROQ_API_KEY"),
            "anthropic":  ("anthropic_api_key",  "ANTHROPIC_API_KEY"),
            "openai":     ("openai_api_key",     "OPENAI_API_KEY"),
            "openrouter": ("openrouter_api_key", "OPENROUTER_API_KEY"),
            "cerebras":   ("cerebras_api_key",   "CEREBRAS_API_KEY"),
            "deepseek":   ("deepseek_api_key",   "DEEPSEEK_API_KEY"),
        }
        if provider in key_map:
            attr, env = key_map[provider]
            if not getattr(self, attr):
                raise RuntimeError(
                    f"LHASSA_LLM_PROVIDER={provider!r} but {env} is not set.\n"
                    f"Add it to your .env file, or change LHASSA_LLM_PROVIDER."
                )

        # ── Agent backend ──────────────────────────────────────────────────────
        backend = self.agent_backend

        if backend not in ("claude_code", "builtin"):
            raise RuntimeError(
                f"Unknown LHASSA_AGENT_BACKEND={backend!r}. "
                f"Valid options: claude_code, builtin"
            )

        if backend == "claude_code":
            if not _CLAUDE_AVAILABLE:
                raise RuntimeError(
                    "LHASSA_AGENT_BACKEND=claude_code but the 'claude' CLI was not found in PATH.\n"
                    "Install Claude Code: https://claude.ai/code\n"
                    "Or set LHASSA_AGENT_BACKEND=builtin to use the built-in LLM tool loop."
                )
            cwd = os.getcwd()
            if not self.agent_dir:
                logger.warning(
                    f"LHASSA_AGENT_DIR is not set — agents will run in {cwd}. "
                    "Add LHASSA_AGENT_DIR=/path/to/your/project to .env."
                )
            detected = " (auto-detected)" if not os.getenv("LHASSA_AGENT_BACKEND") else ""
            dir_info = self.agent_dir or cwd
            logger.info(f"Agent backend: claude_code{detected}  dir={dir_info}")
        else:
            detected = " (auto-detected: claude CLI not found)" if not os.getenv("LHASSA_AGENT_BACKEND") else ""
            logger.info(f"Agent backend: builtin{detected}  provider={provider}  model={self.model}")

        # ── Local AI coding CLI (run_ai_cli tool) ──────────────────────────────
        ai_cli = self.ai_cli
        if ai_cli not in _AI_CLI_BINARIES:
            raise RuntimeError(
                f"Unknown LHASSA_AI_CLI={ai_cli!r}. "
                f"Valid options: {', '.join(_AI_CLI_BINARIES)}"
            )
        ai_detected = " (auto-detected)" if not os.getenv("LHASSA_AI_CLI") else ""
        if shutil.which(ai_cli) is not None:
            logger.info(f"AI CLI: {ai_cli}{ai_detected}  (run_ai_cli tool)")
        else:
            logger.warning(
                f"AI CLI: {ai_cli}{ai_detected} is not installed — the run_ai_cli "
                f"tool will report it as unavailable until it is on PATH."
            )

        # ── Vision provider ────────────────────────────────────────────────────
        _VISION_CAPABLE = {"anthropic", "openai", "openrouter"}
        _vision_key_map = {
            "anthropic":  self.anthropic_api_key,
            "openai":     self.openai_api_key,
            "openrouter": self.openrouter_api_key,
        }

        vp = self.vision_provider
        if vp not in (*_VISION_CAPABLE, "auto"):
            raise RuntimeError(
                f"Unknown LHASSA_VISION_PROVIDER={vp!r}. "
                f"Valid options: auto, anthropic, openai, openrouter"
            )

        if vp != "auto":
            if not _vision_key_map.get(vp):
                raise RuntimeError(
                    f"LHASSA_VISION_PROVIDER={vp!r} but the matching API key is not set.\n"
                    f"Add {vp.upper()}_API_KEY to .env."
                )
            resolved_vp = vp
        elif provider in _VISION_CAPABLE and _vision_key_map.get(provider):
            resolved_vp = provider  # active LLM provider supports vision
        elif self.anthropic_api_key:
            resolved_vp = "anthropic"
        elif self.openai_api_key:
            resolved_vp = "openai"
        else:
            resolved_vp = None  # vision tools will fail at call time with a clear message

        if resolved_vp:
            vm = self.vision_model or {"anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini", "openrouter": "anthropic/claude-haiku-4-5-20251001"}.get(resolved_vp, "")
            auto_note = " (auto-detected)" if vp == "auto" else ""
            logger.info(f"Vision provider: {resolved_vp}{auto_note}  model={vm}")
        else:
            logger.warning(
                "Vision tools disabled — no vision-capable API key found. "
                "Set ANTHROPIC_API_KEY or OPENAI_API_KEY to enable analyze_screen / analyze_image."
            )
