"""
Session lifecycle — called on shutdown to extract facts and save a summary.
Uses the configured LLM provider (same as the voice pipeline). Falls back to
Groq if the active provider doesn't support JSON response_format.
"""

import asyncio
import json
import os
from loguru import logger

from potts.memory.store import save_session, upsert_fact
from potts.state import state


EXTRACT_PROMPT = """Analyse this conversation and respond with JSON in exactly this format:
{
  "facts": [{"key": "snake_case_key", "value": "value"}],
  "summary": "one-sentence summary of what was discussed"
}

Rules for facts:
- Only include NEW or UPDATED facts about the user (name, job, projects, preferences, OS, etc.)
- Keys must be descriptive snake_case: name, job, current_project, operating_system, etc.
- If there are no new facts, use facts: []
- The summary field is always required — one concise sentence

Conversation:
{conversation}"""

_GROQ_FALLBACK_MODEL = os.getenv(
    "POTTS_MEMORY_FALLBACK_MODEL",
    "llama-3.3-70b-versatile",
)


async def close_session(messages: list):
    """
    Extract facts and save a session summary when Potts shuts down.
    Skips sessions with fewer than 2 real exchanges (user + assistant).
    Uses the configured LLM provider; falls back gracefully on error.
    """
    exchange = [m for m in messages if m.get("role") in ("user", "assistant")]
    if len(exchange) < 2:
        logger.info("Session too short — skipping memory save.")
        return

    # Keep only the last 40 turns to avoid exceeding model context limits
    if len(exchange) > 40:
        logger.debug(f"[memory] conversation has {len(exchange)} messages — truncating to last 40 for extraction")
        exchange = exchange[-40:]

    conversation_text = "\n".join(
        f"{m['role'].upper()}: {m.get('content', '')}"
        for m in exchange
        if isinstance(m.get("content"), str)
    )

    prompt = EXTRACT_PROMPT.format(conversation=conversation_text)

    try:
        from potts.config import Config
        from potts.llm.provider import get_agent_client
        config = Config()
        client, provider = get_agent_client(config)
        raw = await _extract_session_payload(config, client, provider, prompt)

        state.metrics["llm_calls"] += 1

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


async def _extract_session_payload(config, client, provider: str, prompt: str) -> str:
    try:
        if provider == "anthropic":
            return await _extract_anthropic(client, config.model, prompt)
        return await _extract_openai_compat(client, config.model, prompt)
    except Exception as primary_error:
        if provider in ("anthropic", "groq") or not getattr(config, "groq_api_key", ""):
            raise

        logger.warning(
            f"[memory] Primary extraction via {provider} failed, retrying with Groq: {primary_error}"
        )
        return await _extract_with_groq_fallback(prompt, config.groq_api_key)


async def _extract_with_groq_fallback(prompt: str, api_key: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    try:
        return await _extract_openai_compat(client, _GROQ_FALLBACK_MODEL, prompt)
    finally:
        await client.aclose()


async def _extract_openai_compat(client, model: str, prompt: str) -> str:
    """Call an OpenAI-compatible client and request JSON output."""
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
            timeout=30,
        ),
        timeout=45.0,
    )
    if not response.choices or not response.choices[0].message.content:
        return "{}"
    return response.choices[0].message.content


async def _extract_anthropic(client, model: str, prompt: str) -> str:
    """Call the Anthropic client. Instructs the model to reply with JSON only."""
    response = await asyncio.wait_for(
        client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            system="You are a JSON extraction assistant. Reply with valid JSON only — no prose, no markdown.",
        ),
        timeout=45.0,
    )
    # Extract text from the first content block
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return "{}"
