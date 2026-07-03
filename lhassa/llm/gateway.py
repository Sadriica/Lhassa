"""
LedgerGateway — the single chokepoint every cloud-LLM call routes through.

Three responsibilities, all backed by one SQLite file:

  1. Cache   — identical prompts return a stored answer without hitting the cloud.
  2. Budget  — a per-UTC-day output-token ceiling refuses calls once spent.
  3. Ledger  — every call is costed and recorded (tokens + estimated USD).

Live spend is mirrored into ``state.metrics`` so the dashboard reflects it in
real time. The provider call itself goes through ``get_agent_client`` and
supports both the Anthropic client and the OpenAI-compatible client.

This is Phase-1 "minimize AI usage" infrastructure: nothing calls a model
directly — they go through here so cost is always visible and bounded.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from lhassa._vendor import aiosqlite
from loguru import logger

from lhassa.state import state

if TYPE_CHECKING:
    from lhassa.config import Config

# Default ledger location — mirrors lhassa.memory.store's ~/.lhassa layout.
DEFAULT_DB_PATH = Path.home() / ".lhassa" / "llm_gateway.db"

# Runtime cost limits that must survive a restart (the daily token budget set
# from the dashboard). A tiny JSON next to the ledger DB. The kill-switch is
# deliberately NOT persisted — it defaults from config (LHASSA_KILL_SWITCH) on
# every boot so a hard stop is always an explicit, in-session decision.
DEFAULT_LIMITS_PATH = Path.home() / ".lhassa" / "llm_limits.json"


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env var (1/true/yes/on), returning default when unset."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")

# Pricing in USD per 1M tokens, as (input_rate, output_rate). Matched by
# substring against the model id, so dated suffixes (…-20251101) still resolve.
# Metered APIs (Anthropic, Groq) carry real list prices. Subscription CLIs
# (claude / codex / opencode) and local/ollama stay at 0.0 BY DESIGN — they have
# no marginal cash cost per call. Editable: add a row, or LHASSA_PRICING_JSON to
# override/extend without a code edit. Anything unlisted costs 0.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
    # Groq list prices (metered API).
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
}


def _parse_pricing_json(raw: str) -> dict[str, tuple[float, float]]:
    """Parse LHASSA_PRICING_JSON into {model_id.lower(): (in_rate, out_rate)}.

    Shape: a JSON object ``{"model-name": [input_per_1M, output_per_1M]}``. Any
    malformed JSON, non-object payload, or bad row is logged and skipped — this
    never raises, so a typo can never crash the gateway.
    """
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception as exc:
        logger.warning(f"LHASSA_PRICING_JSON: ignoring malformed JSON ({exc})")
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "LHASSA_PRICING_JSON: expected a JSON object {model: [in, out]}; ignoring"
        )
        return {}
    out: dict[str, tuple[float, float]] = {}
    for model, rates in data.items():
        if not isinstance(model, str) or not model.strip():
            continue
        if isinstance(rates, (list, tuple)) and len(rates) == 2:
            try:
                out[model.strip().lower()] = (float(rates[0]), float(rates[1]))
            except (TypeError, ValueError):
                logger.warning(f"LHASSA_PRICING_JSON: bad rates for {model!r}; skipping")
    return out


def _merged_pricing(raw: str) -> dict[str, tuple[float, float]]:
    """Built-in PRICING with the LHASSA_PRICING_JSON overrides merged OVER it."""
    merged = dict(PRICING)
    merged.update(_parse_pricing_json(raw))
    return merged

# A completion backend: given the resolved call, return (text, input, output).
# The default implementation talks to the real provider; tests inject a fake.
CompletionFn = Callable[..., Awaitable[tuple[str, int, int]]]

# A tools-aware completion backend: given the resolved call (incl. tool schemas),
# return (text, tool_calls, input, output, stop_reason). Tests inject a fake to
# run the agent/CLI loop offline. tool_calls is a list of normalized dicts
# ``{"id", "name", "input"}`` where input is the parsed argument object.
ToolCompletionFn = Callable[
    ..., Awaitable[tuple[str, list[dict], int, int, str]]
]


class BudgetExceeded(Exception):
    """Raised when today's output-token spend has reached the daily budget."""


class KillSwitchActive(BudgetExceeded):
    """Raised when the manual kill-switch is engaged — a hard stop, independent
    of the daily token budget. Subclasses ``BudgetExceeded`` so existing degrade
    paths that already catch a spent budget also catch a kill-switch halt.
    """


@dataclass
class CompletionResult:
    """Outcome of a gateway call — text plus the costing metadata that was logged."""

    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_hit: bool


@dataclass
class ToolTurnResult:
    """Outcome of one tool-aware round-trip — text, any tool calls, and costing.

    ``tool_calls`` are provider-normalized: each is ``{"id", "name", "input"}``
    where ``input`` is the parsed argument object. An empty list means the model
    produced a final answer (a deterministic turn that may be cached).
    """

    text: str
    tool_calls: list[dict]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_hit: bool
    stop_reason: str


def _resolve_provider(config: "Config") -> str:
    """Normalise the configured provider to the two call shapes we support.

    Anthropic uses its own client; everything else speaks the OpenAI protocol.
    """
    return "anthropic" if config.llm_provider == "anthropic" else "openai"


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD for a call, from the per-1M PRICING table (0.0 if unknown)."""
    in_rate, out_rate = price_for(model)
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def price_for(model: str) -> tuple[float, float]:
    """Return (input_rate, output_rate) per 1M tokens for a model id, or (0, 0)."""
    model_id = (model or "").lower()
    for needle, rates in PRICING.items():
        if needle in model_id:
            return rates
    return (0.0, 0.0)


def _now() -> datetime:
    """Current UTC instant. A seam so cache TTL tests can freeze time."""
    return datetime.now(timezone.utc)


def _today() -> str:
    """Current UTC calendar day as YYYY-MM-DD — the budget bucket key."""
    return _now().strftime("%Y-%m-%d")


def _cache_key(
    provider: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: Optional[float],
    system: Optional[str],
) -> str:
    """Stable SHA-256 over everything that can change the model's answer."""
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _tools_cache_key(
    provider: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    system: Optional[str],
) -> str:
    """Stable SHA-256 for a tool-aware turn — includes the tool schemas.

    Distinct from ``_cache_key`` (the ``kind`` discriminator prevents a plain
    completion and a tool turn with the same messages from colliding). ``default=str``
    keeps it robust if a message carries a non-JSON-native value.
    """
    payload = json.dumps(
        {
            "kind": "tools",
            "provider": provider,
            "model": model,
            "system": system,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LedgerGateway:
    """Cache + budget + cost ledger wrapper around a chat completion."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        limits_path: str | Path | None = None,
        kill_switch: bool | None = None,
        pricing_json: str | None = None,
    ):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.limits_path = Path(limits_path) if limits_path else DEFAULT_LIMITS_PATH
        self._initialized = False
        # Effective pricing table: built-in PRICING with the LHASSA_PRICING_JSON
        # overrides merged over it at init (explicit arg wins over the env var).
        raw = os.getenv("LHASSA_PRICING_JSON", "") if pricing_json is None else pricing_json
        self._pricing = _merged_pricing(raw)
        # Last UTC day the expired-cache purge ran (opportunistic, once per day).
        self._last_purge_day: str | None = None
        # Runtime daily-budget override (None → defer to config.llm_daily_token_budget).
        self._budget_override: int | None = None
        # Kill-switch: explicit arg wins, else the LHASSA_KILL_SWITCH boot default.
        self._kill_switch: bool = (
            _env_flag("LHASSA_KILL_SWITCH", False) if kill_switch is None else bool(kill_switch)
        )
        self._load_limits()

    # ── Kill-switch (hard manual stop) ─────────────────────────────────────────

    @property
    def kill_switch(self) -> bool:
        """Whether the manual kill-switch is currently engaged."""
        return self._kill_switch

    def set_kill_switch(self, value: bool) -> None:
        """Engage or release the kill-switch for the live process."""
        self._kill_switch = bool(value)
        logger.warning(
            f"LLM gateway: kill-switch {'ENGAGED — all cloud-LLM calls halted' if value else 'released'}"
        )

    def _assert_live(self) -> None:
        """Refuse the call before any provider work when the kill-switch is on."""
        if self._kill_switch:
            logger.warning("LLM gateway: call refused — kill-switch is engaged")
            raise KillSwitchActive(
                "LLM kill-switch is engaged; all cloud-LLM calls are halted. "
                "Release it from the dashboard to resume."
            )

    # ── Cost limits (runtime budget, persisted) ────────────────────────────────

    def _load_limits(self) -> None:
        """Load the persisted daily-budget override, if any. Never raises."""
        try:
            if self.limits_path.exists():
                data = json.loads(self.limits_path.read_text())
                if isinstance(data, dict):
                    budget = data.get("daily_token_budget")
                    if isinstance(budget, int) and not isinstance(budget, bool) and budget >= 0:
                        self._budget_override = budget
        except Exception:
            pass

    def _save_limits(self) -> None:
        """Persist the daily-budget override next to the ledger DB. Never raises."""
        try:
            self.limits_path.parent.mkdir(parents=True, exist_ok=True)
            self.limits_path.write_text(
                json.dumps({"daily_token_budget": self._budget_override})
            )
        except Exception:
            pass

    def set_daily_token_budget(self, budget: int) -> None:
        """Set (and persist) the runtime daily output-token budget. 0 = unlimited."""
        self._budget_override = max(0, int(budget))
        self._save_limits()
        logger.info(f"LLM gateway: daily token budget set to {self._budget_override}")

    def effective_daily_budget(self, config: "Config") -> int:
        """The budget in force: the runtime override if set, else the config value."""
        if self._budget_override is not None:
            return self._budget_override
        return config.llm_daily_token_budget

    # ── Pricing (built-in table + LHASSA_PRICING_JSON overrides) ────────────────

    def price_for(self, model: str) -> tuple[float, float]:
        """(input_rate, output_rate) per 1M tokens for a model id, or (0, 0).

        Uses this instance's effective table (built-in + LHASSA_PRICING_JSON), so
        an owner-supplied price applies without any code change.
        """
        model_id = (model or "").lower()
        for needle, rates in self._pricing.items():
            if needle in model_id:
                return rates
        return (0.0, 0.0)

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimated USD for a call from this instance's effective pricing table."""
        in_rate, out_rate = self.price_for(model)
        return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000

    # ── Schema ────────────────────────────────────────────────────────────────

    async def _ensure_db(self) -> None:
        """Create tables if absent. Safe to call repeatedly; runs once per instance."""
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key           TEXT PRIMARY KEY,
                    provider      TEXT NOT NULL,
                    model         TEXT NOT NULL,
                    response_text TEXT NOT NULL,
                    created_at    TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS daily_spend (
                    day           TEXT PRIMARY KEY,
                    output_tokens INTEGER NOT NULL DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ledger (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts            TEXT NOT NULL,
                    provider      TEXT NOT NULL,
                    model         TEXT NOT NULL,
                    input_tokens  INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cost_usd      REAL NOT NULL,
                    cache_hit     INTEGER NOT NULL
                )
            """)
            await db.commit()
        self._initialized = True

    # ── Budget bookkeeping ────────────────────────────────────────────────────

    async def daily_output_tokens(self, day: str | None = None) -> int:
        """Output tokens already spent on the given UTC day (default today)."""
        await self._ensure_db()
        day = day or _today()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT output_tokens FROM daily_spend WHERE day=?", (day,)
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def _cost_today(self, day: str) -> float:
        """Sum of ``ledger.cost_usd`` for calls timestamped on ``day`` (UTC)."""
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM ledger WHERE ts LIKE ?",
                (f"{day}%",),
            ) as cur:
                row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def spend_summary(self, config: "Config | None" = None) -> dict:
        """Give the brain visibility into its own spend — "spend as a signal".

        Returns ``{"day", "output_tokens", "budget", "pct_used", "cost_usd"}`` for
        today (UTC): today's output-token spend, the effective daily budget
        (runtime override if set, else ``config.llm_daily_token_budget``; 0 =
        unlimited), the fraction of that budget already used (``0.0`` when
        unlimited), and today's real cost in USD from the ledger. Never raises —
        a query error degrades to a zeroed summary so a caller (e.g. the
        ``check_spend`` action) can always report *something*.
        """
        if config is None:
            from lhassa.config import Config

            config = Config()
        try:
            day = _today()
            spent = await self.daily_output_tokens(day)
            budget = self.effective_daily_budget(config)
            pct_used = (spent / budget) if budget > 0 else 0.0
            cost_today = await self._cost_today(day)
            return {
                "day": day,
                "output_tokens": spent,
                "budget": budget,
                "pct_used": pct_used,
                "cost_usd": cost_today,
            }
        except Exception as exc:
            logger.warning(f"LLM gateway: spend_summary failed: {exc}")
            return {
                "day": _today(),
                "output_tokens": 0,
                "budget": 0,
                "pct_used": 0.0,
                "cost_usd": 0.0,
            }

    async def _add_daily_spend(self, day: str, output_tokens: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO daily_spend(day, output_tokens) VALUES(?, ?)
                ON CONFLICT(day) DO UPDATE
                    SET output_tokens = output_tokens + excluded.output_tokens
                """,
                (day, output_tokens),
            )
            await db.commit()

    # ── Cache ─────────────────────────────────────────────────────────────────

    async def _cache_get(self, key: str, ttl_days: int = 0) -> Optional[str]:
        """Return a cached answer, or None on a miss OR an entry past ``ttl_days``.

        ``ttl_days<=0`` disables expiry (the entry never goes stale). A stored
        ``created_at`` that cannot be parsed is treated as fresh, so a bad row
        never silently drops a valid cache entry.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT response_text, created_at FROM cache WHERE key=?", (key,)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        if ttl_days and ttl_days > 0:
            try:
                created = datetime.fromisoformat(row[1])
                if _now() - created > timedelta(days=ttl_days):
                    return None
            except (TypeError, ValueError):
                pass
        return row[0]

    async def _cache_put(self, key: str, provider: str, model: str, text: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO cache(key, provider, model, response_text, created_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    response_text = excluded.response_text,
                    created_at = excluded.created_at
                """,
                (key, provider, model, text, _now().isoformat()),
            )
            await db.commit()

    async def _maybe_purge_cache(self, ttl_days: int) -> None:
        """Once per UTC day, delete cache rows older than ``ttl_days``. Never raises.

        A cheap opportunistic sweep so an expired-but-never-re-requested prompt does
        not accumulate forever. No-op when expiry is disabled (``ttl_days<=0``).
        """
        if not ttl_days or ttl_days <= 0:
            return
        today = _today()
        if self._last_purge_day == today:
            return
        self._last_purge_day = today
        cutoff = (_now() - timedelta(days=ttl_days)).isoformat()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM cache WHERE created_at < ?", (cutoff,))
                await db.commit()
        except Exception:
            pass

    # ── Ledger ────────────────────────────────────────────────────────────────

    async def _record(self, result: CompletionResult) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO ledger(
                    ts, provider, model, input_tokens, output_tokens, cost_usd, cache_hit
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    result.provider,
                    result.model,
                    result.input_tokens,
                    result.output_tokens,
                    result.cost_usd,
                    1 if result.cache_hit else 0,
                ),
            )
            await db.commit()

    async def ledger_rows(self) -> list[dict]:
        """Return every recorded call, oldest first — for inspection and tests."""
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM ledger ORDER BY id") as cur:
                return [dict(row) for row in await cur.fetchall()]

    # ── Provider call ─────────────────────────────────────────────────────────

    async def _default_completion(
        self,
        config: "Config",
        provider: str,
        model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: Optional[float],
        system: Optional[str],
    ) -> tuple[str, int, int]:
        """Live call to the real provider. Returns (text, input_tokens, output_tokens)."""
        from lhassa.llm.provider import get_agent_client

        client, provider_name = get_agent_client(config)

        if provider_name == "anthropic":
            kwargs: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
            if system:
                kwargs["system"] = system
            if temperature is not None:
                kwargs["temperature"] = temperature
            resp = await client.messages.create(**kwargs)
            text = "".join(
                block.text
                for block in resp.content
                if getattr(block, "type", None) == "text"
            )
            return text, resp.usage.input_tokens, resp.usage.output_tokens

        # OpenAI-compatible (groq, openai, openrouter, ollama, cerebras)
        chat_messages = messages
        if system:
            chat_messages = [{"role": "system", "content": system}, *messages]
        kwargs = {"model": model, "messages": chat_messages, "max_tokens": max_tokens}
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = await client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        return text, resp.usage.prompt_tokens, resp.usage.completion_tokens

    async def _default_tools_completion(
        self,
        config: "Config",
        provider: str,
        model: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        system: Optional[str],
    ) -> tuple[str, list[dict], int, int, str]:
        """Live tool-aware call. Returns (text, tool_calls, in, out, stop_reason).

        ``tools`` is passed through in whatever shape the caller built it (Anthropic
        ``input_schema`` form for the Anthropic client, OpenAI ``function`` form for
        the OpenAI-compatible client). The returned ``tool_calls`` are normalized to
        ``{"id", "name", "input"}`` regardless of provider.
        """
        from lhassa.llm.provider import get_agent_client

        client, provider_name = get_agent_client(config)

        if provider_name == "anthropic":
            kwargs: dict = {
                "model": model,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
            }
            if system:
                kwargs["system"] = system
            resp = await client.messages.create(**kwargs)
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in resp.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text_parts.append(block.text)
                elif btype == "tool_use":
                    tool_calls.append(
                        {"id": block.id, "name": block.name, "input": dict(block.input)}
                    )
            return (
                "".join(text_parts),
                tool_calls,
                resp.usage.input_tokens,
                resp.usage.output_tokens,
                resp.stop_reason or "",
            )

        # OpenAI-compatible (groq, openai, openrouter, ollama, cerebras)
        chat_messages = messages
        if system:
            chat_messages = [{"role": "system", "content": system}, *messages]
        resp = await client.chat.completions.create(
            model=model,
            messages=chat_messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or ""
        tool_calls = []
        for tc in getattr(msg, "tool_calls", None) or []:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "input": args})
        return (
            text,
            tool_calls,
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
            choice.finish_reason or "",
        )

    # ── Public entry point ────────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        *,
        config: "Config | None" = None,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        system: str | None = None,
        completion_fn: CompletionFn | None = None,
    ) -> CompletionResult:
        """Run a chat completion through cache → budget → live call → ledger.

        Args:
            messages: chat messages (provider-native role/content dicts).
            config: Config to use; a fresh Config() is created if omitted.
            model: model id override; defaults to ``config.model``.
            max_tokens: max output tokens (also part of the cache key).
            temperature: sampling temperature (part of the cache key).
            system: optional system prompt (part of the cache key).
            completion_fn: async ``(provider, model, messages, max_tokens,
                temperature, system) -> (text, input_tokens, output_tokens)``.
                Injected by tests to run offline; defaults to the live provider.

        Returns:
            CompletionResult with the text and the costing metadata that was logged.

        Raises:
            BudgetExceeded: if today's output-token spend already meets the budget.
        """
        if config is None:
            from lhassa.config import Config

            config = Config()

        # ── Kill-switch: a hard manual stop, before cache, budget, or provider ──
        self._assert_live()

        await self._ensure_db()

        model = model or config.model
        provider = _resolve_provider(config)
        key = _cache_key(provider, model, messages, max_tokens, temperature, system)

        # ── Cache (TTL-bounded; expired entries are misses + purged daily) ──────
        if config.llm_cache_enabled:
            ttl_days = getattr(config, "llm_cache_ttl_days", 0) or 0
            await self._maybe_purge_cache(ttl_days)
            cached = await self._cache_get(key, ttl_days)
            if cached is not None:
                state.metrics["llm_cache_hits"] += 1
                result = CompletionResult(
                    text=cached,
                    provider=provider,
                    model=model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    cache_hit=True,
                )
                await self._record(result)
                logger.debug(f"LLM gateway: cache hit ({provider}/{model})")
                return result

        # ── Budget ─────────────────────────────────────────────────────────────
        budget = self.effective_daily_budget(config)
        if budget > 0:
            day = _today()
            spent = await self.daily_output_tokens(day)
            if spent >= budget:
                raise BudgetExceeded(
                    f"Daily LLM output-token budget reached: {spent}/{budget} "
                    f"tokens spent today ({day} UTC). Calls resume tomorrow."
                )

        # ── Live call ──────────────────────────────────────────────────────────
        fn = completion_fn
        if fn is None:
            text, input_tokens, output_tokens = await self._default_completion(
                config, provider, model, messages, max_tokens, temperature, system
            )
        else:
            text, input_tokens, output_tokens = await fn(
                provider, model, messages, max_tokens, temperature, system
            )

        cost = self._estimate_cost(model, input_tokens, output_tokens)

        # ── Ledger: mirror into live metrics, persist a row ───────────────────
        state.metrics["llm_calls"] += 1
        state.metrics["llm_input_tokens"] += input_tokens
        state.metrics["llm_output_tokens"] += output_tokens
        state.metrics["llm_cost_usd"] += cost

        await self._add_daily_spend(_today(), output_tokens)

        if config.llm_cache_enabled:
            await self._cache_put(key, provider, model, text)

        result = CompletionResult(
            text=text,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            cache_hit=False,
        )
        await self._record(result)
        logger.debug(
            f"LLM gateway: live call {provider}/{model} "
            f"in={input_tokens} out={output_tokens} cost=${cost:.6f}"
        )
        return result

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        config: "Config | None" = None,
        model: str | None = None,
        max_tokens: int = 4096,
        system: str | None = None,
        completion_fn: ToolCompletionFn | None = None,
    ) -> ToolTurnResult:
        """Run one tool-aware round-trip through cache → budget → live call → ledger.

        This is the choke point for the agent and CLI tool loops: callers invoke it
        once per loop iteration instead of hitting the raw provider client, then
        execute any returned tool calls and feed results back on the next call.

        Caching is deliberately limited to *deterministic* turns — only a turn that
        returns no tool calls is stored (tool outputs are non-deterministic, so a
        tool-calling turn must never be served from cache). The cache key includes
        the tool schemas, so changing the available tools invalidates it.

        Args:
            messages: chat messages (provider-native role/content dicts, no system).
            tools: tool schemas in the shape the resolved provider expects — Anthropic
                ``input_schema`` form for Anthropic, OpenAI ``function`` form otherwise.
            config: Config to use; a fresh Config() is created if omitted.
            model: model id override; defaults to ``config.model``.
            max_tokens: max output tokens (also part of the cache key).
            system: optional system prompt (part of the cache key).
            completion_fn: async ``(provider, model, messages, tools, system,
                max_tokens) -> (text, tool_calls, input_tokens, output_tokens,
                stop_reason)``. Injected by tests to run offline; defaults to the
                live provider.

        Returns:
            ToolTurnResult with text, normalized tool_calls, usage, cost, cache_hit,
            and stop_reason.

        Raises:
            BudgetExceeded: if today's output-token spend already meets the budget.
        """
        if config is None:
            from lhassa.config import Config

            config = Config()

        # ── Kill-switch: a hard manual stop, before cache, budget, or provider ──
        self._assert_live()

        await self._ensure_db()

        model = model or config.model
        provider = _resolve_provider(config)
        key = _tools_cache_key(provider, model, messages, tools, max_tokens, system)

        # ── Cache (only no-tool turns are ever stored → a hit is always tool-free) ─
        if config.llm_cache_enabled:
            ttl_days = getattr(config, "llm_cache_ttl_days", 0) or 0
            await self._maybe_purge_cache(ttl_days)
            cached = await self._cache_get(key, ttl_days)
            if cached is not None:
                state.metrics["llm_cache_hits"] += 1
                try:
                    payload = json.loads(cached)
                    text = payload.get("text", "")
                    stop_reason = payload.get("stop_reason", "")
                except (json.JSONDecodeError, TypeError):
                    text, stop_reason = cached, ""
                await self._record(
                    CompletionResult(
                        text=text,
                        provider=provider,
                        model=model,
                        input_tokens=0,
                        output_tokens=0,
                        cost_usd=0.0,
                        cache_hit=True,
                    )
                )
                logger.debug(f"LLM gateway: tool cache hit ({provider}/{model})")
                return ToolTurnResult(
                    text=text,
                    tool_calls=[],
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    cache_hit=True,
                    stop_reason=stop_reason,
                )

        # ── Budget ─────────────────────────────────────────────────────────────
        budget = self.effective_daily_budget(config)
        if budget > 0:
            day = _today()
            spent = await self.daily_output_tokens(day)
            if spent >= budget:
                raise BudgetExceeded(
                    f"Daily LLM output-token budget reached: {spent}/{budget} "
                    f"tokens spent today ({day} UTC). Calls resume tomorrow."
                )

        # ── Live call ──────────────────────────────────────────────────────────
        if completion_fn is None:
            text, tool_calls, input_tokens, output_tokens, stop_reason = (
                await self._default_tools_completion(
                    config, provider, model, messages, tools, max_tokens, system
                )
            )
        else:
            text, tool_calls, input_tokens, output_tokens, stop_reason = (
                await completion_fn(provider, model, messages, tools, system, max_tokens)
            )

        tool_calls = tool_calls or []
        cost = self._estimate_cost(model, input_tokens, output_tokens)

        # ── Ledger: mirror into live metrics, persist a row ───────────────────
        state.metrics["llm_calls"] += 1
        state.metrics["llm_input_tokens"] += input_tokens
        state.metrics["llm_output_tokens"] += output_tokens
        state.metrics["llm_cost_usd"] += cost

        await self._add_daily_spend(_today(), output_tokens)

        # Cache only deterministic (no tool) turns.
        if config.llm_cache_enabled and not tool_calls:
            await self._cache_put(
                key,
                provider,
                model,
                json.dumps({"text": text, "stop_reason": stop_reason}),
            )

        await self._record(
            CompletionResult(
                text=text,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                cache_hit=False,
            )
        )
        logger.debug(
            f"LLM gateway: live tool call {provider}/{model} "
            f"in={input_tokens} out={output_tokens} tools={len(tool_calls)} "
            f"cost=${cost:.6f}"
        )
        return ToolTurnResult(
            text=text,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            cache_hit=False,
            stop_reason=stop_reason,
        )


# Module-level default instance — import from anywhere via
# `from lhassa.llm.gateway import gateway`.
gateway = LedgerGateway()


async def spend_summary(config: "Config | None" = None) -> dict:
    """Module-level convenience wrapping the default :data:`gateway` instance.

    See :meth:`LedgerGateway.spend_summary`. This is the seam the ``check_spend``
    actionpack action (and any other caller that just wants "today's spend")
    uses without needing the ``LedgerGateway`` instance.
    """
    return await gateway.spend_summary(config)
