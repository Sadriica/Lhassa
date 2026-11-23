# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Session lifecycle; called on shutdown to extract facts and save a summary.
The fact-extraction LLM call is routed through the cost gateway
(``lhassa.llm.gateway``) so the shared cache, daily token budget, and cost
ledger all apply. The gateway is provider-agnostic, so the request for JSON
output lives in the prompt/system text rather than a provider-specific
``response_format`` flag.
"""

import asyncio
import json

from loguru import logger

from lhassa.llm.gateway import BudgetExceeded, gateway
from lhassa.memory.store import save_session, upsert_fact
from lhassa.state import state


EXTRACT_PROMPT = """Analyse this conversation and respond with JSON in exactly this format:
{
  "facts": [{"key": "snake_case_key", "value": "value"}],
  "summary": "one-sentence summary of what was discussed"
}

Rules for facts:
- Only include NEW or UPDATED facts about the user (name, job, projects, preferences, OS, etc.)
- Keys must be descriptive snake_case: name, job, current_project, operating_system, etc.
- If there are no new facts, use facts: []
- The summary field is always required; one concise sentence

Conversation:
{conversation}"""

# The gateway is provider-agnostic and does not pass an OpenAI-style
# ``response_format=json_object``, so the JSON-only contract is stated here in
# the system prompt; it covers both the Anthropic and OpenAI-compatible paths.
EXTRACT_SYSTEM = (
    "You are a JSON extraction assistant. Reply with valid JSON only; "
    "no prose, no markdown."
)

# Upper bound on how long fact extraction may block shutdown.
_EXTRACT_TIMEOUT = 45.0


async def close_session(messages: list):
    """
    Extract facts and save a session summary when Lhassa shuts down.
    Skips sessions with fewer than 2 real exchanges (user + assistant).
    Routes the extraction through the cost gateway; falls back gracefully on
    error and never crashes shutdown.
    """
    exchange = [m for m in messages if m.get("role") in ("user", "assistant")]
    if len(exchange) < 2:
        logger.info("Session too short; skipping memory save.")
        return

    # Keep only the last 40 turns to avoid exceeding model context limits
    if len(exchange) > 40:
        logger.debug(f"[memory] conversation has {len(exchange)} messages; truncating to last 40 for extraction")
        exchange = exchange[-40:]

    conversation_text = "\n".join(
        f"{m['role'].upper()}: {m.get('content', '')}"
        for m in exchange
        if isinstance(m.get("content"), str)
    )

    # .replace (not .format); EXTRACT_PROMPT contains literal JSON braces.
    prompt = EXTRACT_PROMPT.replace("{conversation}", conversation_text)

    try:
        raw = await _extract_session_payload(prompt)
    except BudgetExceeded as e:
        logger.warning(f"[memory] Daily LLM budget reached; skipping memory extraction: {e}")
        return
    except Exception as e:
        logger.error(f"Error extracting session memory: {e}")
        return

    if not raw:
        return

    try:
        data = json.loads(raw)
        facts = data.get("facts", [])
        summary = data.get("summary", "")

        for fact in facts:
            key = fact.get("key", "").strip()
            value = fact.get("value", "").strip()
            if key and value:
                await upsert_fact(key, value)
                logger.info(f"[memory] fact saved: {key} = {value}")

        if summary:
            await save_session(summary)
            state.metrics["sessions"] += 1
            logger.info(f"[memory] session saved: {summary}")

    except json.JSONDecodeError as e:
        logger.error(f"Could not parse memory extraction response: {e}")
    except Exception as e:
        logger.error(f"Error saving session memory: {e}")


async def _extract_session_payload(prompt: str) -> str:
    """Run fact extraction through the cost gateway and return the raw JSON text.

    The gateway applies the shared cache, daily token budget (raising
    ``BudgetExceeded`` when spent), and cost ledger. Wrapped in a timeout so a
    stalled provider can never hang shutdown.
    """
    result = await asyncio.wait_for(
        gateway.complete(
            [{"role": "user", "content": prompt}],
            system=EXTRACT_SYSTEM,
            max_tokens=1024,
            temperature=0,
        ),
        timeout=_EXTRACT_TIMEOUT,
    )
    return _strip_json_fence(result.text)


def _strip_json_fence(text: str) -> str:
    """Strip a ```json … ``` markdown fence if a provider wrapped the JSON in one."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else ""
        if cleaned.endswith("```"):
            cleaned = cleaned[: -len("```")]
    return cleaned.strip() or "{}"
