"""
Herramientas de Potts — Phase 2b
Registra funciones en el LLM para: búsqueda web, shell, archivos.
"""

import asyncio
import subprocess
from pathlib import Path

from duckduckgo_search import DDGS
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams


# ── Schemas (lo que ve el LLM) ────────────────────────────────────────────────

TOOLS = ToolsSchema(
    standard_tools=[
        FunctionSchema(
            name="web_search",
            description="Busca información actualizada en internet. Úsala cuando necesites datos recientes, noticias, o información que no tienes.",
            properties={
                "query": {"type": "string", "description": "La consulta de búsqueda"},
                "max_results": {"type": "integer", "description": "Número de resultados (default 4)", "default": 4},
            },
            required=["query"],
        ),
        FunctionSchema(
            name="shell",
            description="Ejecuta un comando de shell en el sistema del usuario. Úsala para operaciones del sistema, listar archivos, obtener información del sistema, etc.",
            properties={
                "command": {"type": "string", "description": "El comando a ejecutar"},
            },
            required=["command"],
        ),
        FunctionSchema(
            name="read_file",
            description="Lee el contenido de un archivo en el sistema.",
            properties={
                "path": {"type": "string", "description": "Ruta absoluta o relativa al archivo"},
            },
            required=["path"],
        ),
        FunctionSchema(
            name="write_file",
            description="Escribe o sobreescribe un archivo en el sistema.",
            properties={
                "path": {"type": "string", "description": "Ruta del archivo a escribir"},
                "content": {"type": "string", "description": "Contenido a escribir"},
            },
            required=["path", "content"],
        ),
    ]
)


# ── Handlers (la implementación real) ─────────────────────────────────────────

async def handle_web_search(params: FunctionCallParams):
    query = params.arguments.get("query", "")
    max_results = params.arguments.get("max_results", 4)
    logger.info(f"[tool] web_search: {query!r}")
    print(f"\033[2m[🔍 buscando: {query}]\033[0m")

    try:
        results = await asyncio.to_thread(
            lambda: list(DDGS().text(query, max_results=max_results))
        )
        if not results:
            await params.result_callback("No encontré resultados para esa búsqueda.")
            return

        formatted = "\n\n".join(
            f"**{r['title']}**\n{r['body']}" for r in results
        )
        await params.result_callback(formatted)
    except Exception as e:
        logger.error(f"web_search error: {e}")
        await params.result_callback(f"Error al buscar: {e}")


async def handle_shell(params: FunctionCallParams):
    command = params.arguments.get("command", "")
    logger.info(f"[tool] shell: {command!r}")
    print(f"\033[2m[⚡ ejecutando: {command}]\033[0m")

    try:
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        )
        output = result.stdout.strip() or result.stderr.strip() or "(sin output)"
        # Truncar si es muy largo para no explotar el contexto
        if len(output) > 2000:
            output = output[:2000] + "\n... (truncado)"
        await params.result_callback(output)
    except subprocess.TimeoutExpired:
        await params.result_callback("El comando tardó más de 30 segundos y fue cancelado.")
    except Exception as e:
        logger.error(f"shell error: {e}")
        await params.result_callback(f"Error al ejecutar: {e}")


async def handle_read_file(params: FunctionCallParams):
    path = params.arguments.get("path", "")
    logger.info(f"[tool] read_file: {path!r}")
    print(f"\033[2m[📄 leyendo: {path}]\033[0m")

    try:
        content = Path(path).expanduser().read_text()
        if len(content) > 4000:
            content = content[:4000] + "\n... (truncado)"
        await params.result_callback(content)
    except FileNotFoundError:
        await params.result_callback(f"Archivo no encontrado: {path}")
    except Exception as e:
        await params.result_callback(f"Error al leer: {e}")


async def handle_write_file(params: FunctionCallParams):
    path = params.arguments.get("path", "")
    content = params.arguments.get("content", "")
    logger.info(f"[tool] write_file: {path!r}")
    print(f"\033[2m[✏️  escribiendo: {path}]\033[0m")

    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        await params.result_callback(f"Archivo escrito correctamente: {path}")
    except Exception as e:
        await params.result_callback(f"Error al escribir: {e}")


def register_tools(llm):
    """Registra todos los handlers de herramientas en el servicio LLM."""
    llm.register_function("web_search", handle_web_search)
    llm.register_function("shell", handle_shell)
    llm.register_function("read_file", handle_read_file)
    llm.register_function("write_file", handle_write_file)
    logger.info("Tools registradas: web_search, shell, read_file, write_file")
