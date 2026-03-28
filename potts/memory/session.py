"""
Gestión del ciclo de vida de la sesión.
Al finalizar, llama al LLM para extraer hechos y generar un resumen.
"""

import json
from loguru import logger
from groq import AsyncGroq

from potts.memory.store import save_session, upsert_fact


EXTRACT_PROMPT = """Analiza esta conversación y responde en JSON con este formato exacto:
{
  "facts": [{"key": "nombre_clave", "value": "valor"}],
  "summary": "resumen de una oración de qué se habló"
}

Reglas para facts:
- Solo incluye hechos NUEVOS o ACTUALIZADOS sobre el usuario (nombre, trabajo, proyectos, preferencias, etc.)
- Usa claves en snake_case descriptivas: nombre, trabajo, sistema_operativo, proyecto_actual, etc.
- Si no hay hechos nuevos, usa facts: []
- El summary SIEMPRE debe existir, una oración concisa

Conversación:
{conversation}"""


async def close_session(api_key: str, model: str, messages: list):
    """
    Llamado al cerrar Potts. Extrae hechos y guarda resumen de la sesión.
    Solo procesa si hubo al menos un intercambio real (>2 mensajes).
    """
    # Filtra solo user/assistant, ignora el system prompt
    exchange = [m for m in messages if m.get("role") in ("user", "assistant")]
    if len(exchange) < 2:
        logger.info("Sesión muy corta, no se guarda en memoria.")
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
        raw = response.choices[0].message.content
        data = json.loads(raw)

        facts = data.get("facts", [])
        summary = data.get("summary", "")

        for fact in facts:
            key = fact.get("key", "").strip()
            value = fact.get("value", "").strip()
            if key and value:
                await upsert_fact(key, value)
                logger.info(f"[memoria] hecho guardado: {key} = {value}")

        if summary:
            await save_session(summary)
            logger.info(f"[memoria] sesión guardada: {summary}")

    except Exception as e:
        logger.error(f"Error al guardar memoria de sesión: {e}")
