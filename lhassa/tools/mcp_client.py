# SPDX-License-Identifier: AGPL-3.0-or-later
"""
MCP client; "plug in, don't program" (§2.11, Phase 4 DoD).

Loads server definitions from ``~/.config/lhassa/mcp.json`` (overridable via
``Config.mcp_config_path``), connects to each configured server, discovers its
tools, and adapts them into the brain's tool ecosystem as **namespaced** tools
``mcp__<server>__<tool>``. Registering those onto the LLM service is all it takes
to make an integration invocable; no per-integration code.

Config shape::

    {
      "servers": {
        "files":  {"type": "stdio", "command": ["mcp-server-filesystem", "/data"]},
        "sheets": {"type": "url",   "url": "https://example.com/mcp"}
      }
    }

Defensive by construction: if the ``mcp`` SDK is absent, the config file is
missing/garbage, or a server is unreachable, the loader logs and skips it; it
never raises and returns whatever loaded. The brain runs fine with zero servers.

The SDK is funnelled through three small seams; ``_sdk()``, ``_list_server_tools``
and ``_call_server_tool``: so tests monkeypatch connect/list_tools/call_tool and
stay fully offline (no real MCP process or network).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from lhassa.config import Config

# Namespacing: a discovered tool ``<tool>`` on server ``<server>`` is exposed as
# ``mcp__<server>__<tool>``. This is what makes it invocable without name clashes.
NAMESPACE_SEP = "__"


def namespaced(server: str, tool: str) -> str:
    """Build the namespaced tool name ``mcp__<server>__<tool>``."""
    return f"mcp{NAMESPACE_SEP}{server}{NAMESPACE_SEP}{tool}"


# ── config loading (defensive) ───────────────────────────────────────────────────

def load_config(path: Optional[str] = None) -> dict:
    """Read the MCP config file into ``{"servers": {...}}``.

    Missing file or unparseable JSON → ``{"servers": {}}`` (never raises). The
    path defaults to ``Config.mcp_config_path``.
    """
    cfg_path = Path(path or Config.mcp_config_path).expanduser()
    try:
        raw = cfg_path.read_text()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError):
        return {"servers": {}}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning(f"[mcp] {cfg_path} is not valid JSON; ignoring")
        return {"servers": {}}
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {"servers": {}}
    return {"servers": servers}


# ── SDK seam (lazy, optional) ────────────────────────────────────────────────────

def _sdk():
    """Return the ``mcp`` SDK pieces, or ``None`` if the SDK is not installed.

    Imported lazily so the rest of lhassa works with no ``mcp`` package present.
    """
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        return ClientSession, StdioServerParameters, stdio_client
    except Exception:  # ImportError or any partial-install breakage
        return None


def sdk_available() -> bool:
    """True if the official ``mcp`` Python SDK is importable."""
    return _sdk() is not None


def _stdio_params(server_def: dict):
    """Build ``StdioServerParameters`` from a ``{"command": [...]}`` server def.

    ``command`` may be a list (argv) or a bare string. Returns ``None`` if the
    SDK is missing or the command is empty.
    """
    parts = _sdk()
    if parts is None:
        return None
    _, StdioServerParameters, _ = parts
    cmd = server_def.get("command")
    if isinstance(cmd, str):
        argv = cmd.split()
    elif isinstance(cmd, (list, tuple)):
        argv = [str(c) for c in cmd]
    else:
        argv = []
    if not argv:
        return None
    return StdioServerParameters(command=argv[0], args=list(argv[1:]))


# ── connect / list / call (the monkeypatch seams) ────────────────────────────────

async def _list_server_tools(name: str, server_def: dict) -> list[Any]:
    """Connect to one server and return its tool objects (``.name``, ``.description``,
    ``.inputSchema``). Returns ``[]`` on any failure (SDK missing, unreachable,
    unsupported type). Stdio is supported first-class; ``url`` is best-effort.
    """
    parts = _sdk()
    if parts is None:
        return []
    stype = str(server_def.get("type") or "stdio").lower()
    if stype != "stdio":
        # url/http transport is best-effort and never fatal; stdio is the
        # first-class path per the Phase 4 brief.
        return await _list_server_tools_url(name, server_def)

    ClientSession, _, stdio_client = parts
    params = _stdio_params(server_def)
    if params is None:
        return []
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return list(getattr(result, "tools", []) or [])
    except Exception as exc:
        logger.warning(f"[mcp] server {name!r} unreachable (list_tools): {exc}")
        return []


async def _list_server_tools_url(name: str, server_def: dict) -> list[Any]:
    """Best-effort tool listing over the streamable-HTTP transport."""
    url = server_def.get("url")
    if not url:
        return []
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except Exception:
        return []
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return list(getattr(result, "tools", []) or [])
    except Exception as exc:
        logger.warning(f"[mcp] server {name!r} unreachable (url list_tools): {exc}")
        return []


async def _call_server_tool(server_def: dict, tool_name: str, arguments: dict) -> str:
    """Call ``tool_name`` on a server and return its text result.

    Opens a fresh session per call (simple and stateless). Raises on failure; the
    handler that wraps it converts any error into a result-callback string so the
    LLM never sees an exception.
    """
    parts = _sdk()
    if parts is None:
        raise RuntimeError("mcp SDK not installed")
    stype = str(server_def.get("type") or "stdio").lower()

    if stype == "stdio":
        ClientSession, _, stdio_client = parts
        params = _stdio_params(server_def)
        if params is None:
            raise RuntimeError("invalid stdio command")
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments or {})
                return _result_to_text(result)

    # url transport
    url = server_def.get("url")
    if not url:
        raise RuntimeError("server has no url")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments or {})
            return _result_to_text(result)


def _result_to_text(result: Any) -> str:
    """Flatten an MCP ``CallToolResult`` into a plain-text string.

    Concatenates the text of every text content block; falls back to ``str``.
    """
    content = getattr(result, "content", None)
    if content is None:
        return str(result)
    chunks: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            chunks.append(str(text))
        else:
            chunks.append(str(block))
    return "\n".join(chunks).strip()


# ── adapters: discovered tool → registry-shaped tool ─────────────────────────────

def _build_schema(server: str, tool: Any):
    """Build a pipecat ``FunctionSchema`` for a discovered MCP tool, or ``None``.

    Imported lazily so this module is usable (e.g. ``mcp list``) without pipecat.
    """
    try:
        from pipecat.adapters.schemas.function_schema import FunctionSchema
    except Exception:
        return None
    input_schema = getattr(tool, "inputSchema", None) or {}
    properties = input_schema.get("properties") if isinstance(input_schema, dict) else None
    required = input_schema.get("required") if isinstance(input_schema, dict) else None
    return FunctionSchema(
        name=namespaced(server, getattr(tool, "name", "tool")),
        description=getattr(tool, "description", None) or f"MCP tool {tool} on {server}",
        properties=properties or {},
        required=list(required or []),
    )


def _make_handler(full_name: str, server_def: dict, tool_name: str):
    """Create an async handler (registry shape) that invokes the MCP tool.

    Catches everything and reports via ``result_callback``: it never raises, the
    convention every lhassa tool handler follows.
    """
    async def handler(params):
        args = getattr(params, "arguments", None) or {}
        _bump_tool()
        try:
            text = await _call_server_tool(server_def, tool_name, args)
            await params.result_callback(text or "(no output)")
        except Exception as exc:
            logger.error(f"[mcp] {full_name} call failed: {exc}")
            await params.result_callback(f"MCP error ({full_name}): {exc}")

    return handler


def _bump_tool() -> None:
    """Best-effort tool-call metric bump (lazy import; never fatal)."""
    try:
        from lhassa.state import state
        state.metrics["tool_calls"] += 1
    except Exception:
        pass


async def load_mcp_tools(
    config: Optional[dict] = None,
    *,
    path: Optional[str] = None,
) -> list[dict]:
    """Discover every configured MCP server's tools and adapt them.

    Returns a list of tool descriptors, one per discovered tool::

        {"name": "mcp__<server>__<tool>", "server": ..., "tool": ...,
         "description": ..., "schema": FunctionSchema | None, "handler": async fn}

    Defensive: a missing SDK, missing config, or an unreachable server yields an
    empty (or partial) list; never an exception.
    """
    if config is None:
        config = load_config(path)
    servers = (config or {}).get("servers") or {}
    if not sdk_available():
        if servers:
            logger.warning("[mcp] config has servers but the mcp SDK is not installed; skipping")
        return []

    tools: list[dict] = []
    for server_name, server_def in servers.items():
        if not isinstance(server_def, dict):
            continue
        discovered = await _list_server_tools(server_name, server_def)
        for tool in discovered:
            tool_name = getattr(tool, "name", None)
            if not tool_name:
                continue
            full_name = namespaced(server_name, tool_name)
            tools.append({
                "name": full_name,
                "server": server_name,
                "tool": tool_name,
                "description": getattr(tool, "description", "") or "",
                "schema": _build_schema(server_name, tool),
                "handler": _make_handler(full_name, server_def, tool_name),
            })
        if discovered:
            logger.info(
                f"[mcp] server {server_name!r}: {len(discovered)} tool(s) discovered"
            )
    return tools


async def register_mcp_tools(llm, tools: Optional[list[dict]] = None) -> list[str]:
    """Register discovered MCP tools onto the LLM service (same shape as
    ``tools.registry.register_tools``). Returns the registered namespaced names.

    Additive: this only adds new ``mcp__*`` functions; it never touches existing
    tools or routes. If ``tools`` is ``None`` they are discovered first.
    """
    if tools is None:
        tools = await load_mcp_tools()
    registered: list[str] = []
    for t in tools:
        try:
            llm.register_function(t["name"], t["handler"])
            registered.append(t["name"])
        except Exception as exc:
            logger.warning(f"[mcp] could not register {t['name']}: {exc}")
    if registered:
        logger.info(f"[mcp] registered {len(registered)} tool(s): {', '.join(registered)}")
    return registered


# ── the tracking_postulaciones MCP-backed pull (guarded) ─────────────────────────

async def pull_postulaciones(tool_name: str, args: Optional[dict] = None) -> list[dict]:
    """Pull application updates from a configured MCP tool, as a list of dicts.

    ``tool_name`` is a namespaced ``mcp__<server>__<tool>``. Resolves the server
    from config, calls the tool, and parses its JSON-ish text result into a list
    of ``{empresa, rol, estado, fuente, link, notas}`` dicts. Returns ``[]`` on
    any problem; this is the optional, guarded source for ``tracking_postulaciones``.
    """
    try:
        if not tool_name or not tool_name.startswith(f"mcp{NAMESPACE_SEP}"):
            return []
        rest = tool_name[len(f"mcp{NAMESPACE_SEP}"):]
        server_name, _, bare_tool = rest.partition(NAMESPACE_SEP)
        if not server_name or not bare_tool:
            return []
        servers = load_config().get("servers") or {}
        server_def = servers.get(server_name)
        if not isinstance(server_def, dict):
            return []
        text = await _call_server_tool(server_def, bare_tool, args or {})
        return _parse_postulaciones(text)
    except Exception as exc:
        logger.warning(f"[mcp] pull_postulaciones({tool_name!r}) failed: {exc}")
        return []


def _parse_postulaciones(text: str) -> list[dict]:
    """Parse an MCP tool's text result into a list of application dicts.

    Accepts a JSON array, or a JSON object with an ``items``/``postulaciones`` key.
    Anything else → ``[]``.
    """
    if not text:
        return []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if isinstance(data, dict):
        data = data.get("items") or data.get("postulaciones") or []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


# ── CLI helper: show configured servers + discovered tools ───────────────────────

async def mcp_list_lines(path: Optional[str] = None) -> list[str]:
    """Build a human-readable listing of configured servers and their tools."""
    config = load_config(path)
    servers = config.get("servers") or {}
    lines: list[str] = []
    if not sdk_available():
        lines.append("mcp SDK: NOT installed (pip/uv add mcp); tools cannot be discovered")
    else:
        lines.append("mcp SDK: installed")
    if not servers:
        lines.append(f"No MCP servers configured ({Config.mcp_config_path}).")
        return lines
    lines.append(f"Configured servers ({len(servers)}):")
    tools = await load_mcp_tools(config)
    by_server: dict[str, list[str]] = {}
    for t in tools:
        by_server.setdefault(t["server"], []).append(t["tool"])
    for name, sdef in servers.items():
        stype = sdef.get("type", "stdio") if isinstance(sdef, dict) else "?"
        found = by_server.get(name, [])
        lines.append(f"  - {name} [{stype}]: {len(found)} tool(s)")
        for tool in found:
            lines.append(f"      • {namespaced(name, tool)}")
    return lines


def print_mcp_list(path: Optional[str] = None) -> None:
    """Print the configured servers + discovered tools (the ``mcp list`` command)."""
    import asyncio
    for line in asyncio.run(mcp_list_lines(path)):
        print(line)
