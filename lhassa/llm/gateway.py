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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import aiosqlite
from loguru import logger

from lhassa.state import state

if TYPE_CHECKING:
    from lhassa.config import Config

# Default ledger location — mirrors lhassa.memory.store's ~/.lhassa layout.
DEFAULT_DB_PATH = Path.home() / ".lhassa" / "llm_gateway.db"

# Pricing in USD per 1M tokens, as (input_rate, output_rate). Matched by
# substring against the model id, so dated suffixes (…-20251101) still resolve.
# Anything not listed — local / groq / cerebras / openrouter / ollama / mini —
# costs 0 by default. Editable: add a row to price a new model.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}

# A completion backend: given the resolved call, return (text, input, output).
# The default implementation talks to the real provider; tests inject a fake.
CompletionFn = Callable[..., Awaitable[tuple[str, int, int]]]


class BudgetExceeded(Exception):
    """Raised when today's output-token spend has reached the daily budget."""


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


def _today() -> str:
    """Current UTC calendar day as YYYY-MM-DD — the budget bucket key."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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


class LedgerGateway:
    """Cache + budget + cost ledger wrapper around a chat completion."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._initialized = False

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

    async def _cache_get(self, key: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT response_text FROM cache WHERE key=?", (key,)
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else None

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
                (key, provider, model, text, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

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

        await self._ensure_db()

        model = model or config.model
        provider = _resolve_provider(config)
        key = _cache_key(provider, model, messages, max_tokens, temperature, system)

        # ── Cache ──────────────────────────────────────────────────────────────
        if config.llm_cache_enabled:
            cached = await self._cache_get(key)
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
        budget = config.llm_daily_token_budget
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

        cost = estimate_cost(model, input_tokens, output_tokens)

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


# Module-level default instance — import from anywhere via
# `from lhassa.llm.gateway import gateway`.
gateway = LedgerGateway()
