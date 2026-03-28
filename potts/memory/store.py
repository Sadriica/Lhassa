"""
Memoria persistente de Potts — Opción C (híbrida)

Tablas:
  facts    — hechos sobre el usuario extraídos automáticamente
  sessions — resumen de cada conversación

Flujo:
  Al iniciar → load_context() inyecta hechos + últimos resúmenes en el system prompt
  Al terminar → save_session() pide al LLM que extraiga hechos y resuma la conversación
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

DB_PATH = Path.home() / ".potts" / "memory.db"


async def init_db():
    """Crea las tablas si no existen."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                key       TEXT NOT NULL,
                value     TEXT NOT NULL,
                updated   TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS facts_key ON facts(key)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                date      TEXT NOT NULL,
                summary   TEXT NOT NULL
            )
        """)
        await db.commit()


async def upsert_fact(key: str, value: str):
    """Inserta o actualiza un hecho."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO facts(key, value, updated) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated
        """, (key, value, now))
        await db.commit()


async def load_facts() -> list[dict]:
    """Retorna todos los hechos almacenados."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT key, value, updated FROM facts ORDER BY updated DESC") as cur:
            return [dict(row) for row in await cur.fetchall()]


async def save_session(summary: str):
    """Guarda el resumen de una sesión."""
    date = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO sessions(date, summary) VALUES(?, ?)", (date, summary))
        await db.commit()


async def load_recent_sessions(n: int = 5) -> list[dict]:
    """Retorna los últimos N resúmenes de sesión."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT date, summary FROM sessions ORDER BY id DESC LIMIT ?", (n,)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def build_memory_context() -> Optional[str]:
    """
    Construye el bloque de contexto que se inyecta en el system prompt.
    Retorna None si no hay memoria guardada.
    """
    facts = await load_facts()
    sessions = await load_recent_sessions(5)

    if not facts and not sessions:
        return None

    parts = []

    if facts:
        facts_text = "\n".join(f"- {f['key']}: {f['value']}" for f in facts)
        parts.append(f"## Lo que sabes sobre el usuario\n{facts_text}")

    if sessions:
        sessions_text = "\n".join(
            f"- [{s['date'][:10]}]: {s['summary']}" for s in reversed(sessions)
        )
        parts.append(f"## Conversaciones recientes\n{sessions_text}")

    return "\n\n".join(parts)
