"""
Session lifecycle — called on shutdown to extract facts and save a summary.
Uses Groq to analyse the conversation and produce structured JSON output.
"""

import json
from loguru import logger
from groq import AsyncGroq

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


async def close_session(api_key: str, model: str, messages: list):
    """
    Extract facts and save a session summary when Potts shuts down.
    Skips sessions with fewer than 2 real exchanges (user + assistant).
    """
    exchange = [m for m in messages if m.get("role") in ("user", "assistant")]
    if len(exchange) < 2:
        logger.info("Session too short — skipping memory save.")
        return

    conversation_text = "\n".join(
        f"{m['role'].upper()}: {m.get('content', '')}"
        for m in exchange
        if isinstance(m.get("content"), str)
    )

    prompt = EXTRACT_PROMPT.format(conversation=conversation_text)

    try:
        client = AsyncGroq(api_key=api_key)
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        state.metrics["groq_calls"] += 1

        raw = response.choices[0].message.content
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
