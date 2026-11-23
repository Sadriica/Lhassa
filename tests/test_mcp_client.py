"""Tests for the MCP client; fully offline (no real MCP process or network).

The SDK seams (``_sdk`` / ``_list_server_tools`` / ``_call_server_tool``) are
monkeypatched, so connect + list_tools + call_tool are simulated in-process.
Covers: config loading, namespaced discovery, registration onto a fake LLM, an
invocable tool returning the stubbed result, and the defensive graceful-empty
paths (missing SDK / missing config / unreachable server).
"""

import json

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.tools import mcp_client as mc


def _pipecat_available() -> bool:
    """Whether ``FunctionSchema`` can be imported.

    ``mcp_client._build_schema`` imports pipecat LAZILY and returns ``None``
    when it is absent; the module is deliberately usable without the voice
    extra (``lhassa mcp list`` must work on a headless install). A test that
    asserts a schema unconditionally therefore encodes a dependency the code
    explicitly does not have, and fails on any base-deps environment while
    passing on a dev machine that happens to have pipecat installed.
    """
    try:
        from pipecat.adapters.schemas.function_schema import FunctionSchema  # noqa: F401
    except Exception:
        return False
    return True


# ── fakes ───────────────────────────────────────────────────────────────────────

class FakeTool:
    """Stand-in for an MCP ``Tool`` object."""

    def __init__(self, name, description="", input_schema=None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        }


class FakeLLM:
    """Stand-in for the pipecat LLM service; records registered functions."""

    def __init__(self):
        self.functions = {}

    def register_function(self, name, handler):
        self.functions[name] = handler


class FakeParams:
    """Stand-in for ``FunctionCallParams``: captures the callback result."""

    def __init__(self, arguments):
        self.arguments = arguments
        self.result = None

    async def result_callback(self, value):
        self.result = value


def _write_config(tmp_path, servers):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": servers}))
    return str(path)


SAMPLE_SERVERS = {
    "files": {"type": "stdio", "command": ["mcp-server-filesystem", "/data"]},
}


# ── config loading ──────────────────────────────────────────────────────────────

def test_load_config_reads_sample(tmp_path):
    path = _write_config(tmp_path, SAMPLE_SERVERS)
    cfg = mc.load_config(path)
    assert "files" in cfg["servers"]
    assert cfg["servers"]["files"]["command"][0] == "mcp-server-filesystem"


def test_load_config_missing_file_is_empty():
    assert mc.load_config("/no/such/path/mcp.json") == {"servers": {}}


def test_load_config_garbage_is_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    assert mc.load_config(str(p)) == {"servers": {}}


def test_namespacing():
    assert mc.namespaced("files", "read") == "mcp__files__read"


# ── discovery → namespaced tools ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_mcp_tools_namespaces(tmp_path, monkeypatch):
    path = _write_config(tmp_path, SAMPLE_SERVERS)

    async def fake_list(name, server_def):
        return [FakeTool("read"), FakeTool("write")]

    monkeypatch.setattr(mc, "sdk_available", lambda: True)
    monkeypatch.setattr(mc, "_list_server_tools", fake_list)

    tools = await mc.load_mcp_tools(path=path)
    names = {t["name"] for t in tools}
    assert names == {"mcp__files__read", "mcp__files__write"}
    read = next(t for t in tools if t["name"] == "mcp__files__read")
    # Namespacing is this test's actual subject; always asserted.
    assert read["server"] == "files" and read["tool"] == "read"
    # The pipecat schema is OPTIONAL by design (see _pipecat_available).
    if _pipecat_available():
        assert read["schema"] is not None
        assert read["schema"].name == "mcp__files__read"
    else:
        assert read["schema"] is None


@pytest.mark.asyncio
async def test_register_and_invoke(tmp_path, monkeypatch):
    path = _write_config(tmp_path, SAMPLE_SERVERS)

    async def fake_list(name, server_def):
        return [FakeTool("read")]

    async def fake_call(server_def, tool_name, arguments):
        return f"stubbed:{tool_name}:{arguments.get('q')}"

    monkeypatch.setattr(mc, "sdk_available", lambda: True)
    monkeypatch.setattr(mc, "_list_server_tools", fake_list)
    monkeypatch.setattr(mc, "_call_server_tool", fake_call)

    tools = await mc.load_mcp_tools(path=path)
    llm = FakeLLM()
    registered = await mc.register_mcp_tools(llm, tools)
    assert registered == ["mcp__files__read"]
    assert "mcp__files__read" in llm.functions

    # the registered handler is invocable and returns the stubbed result
    params = FakeParams({"q": "hello"})
    await llm.functions["mcp__files__read"](params)
    assert params.result == "stubbed:read:hello"


@pytest.mark.asyncio
async def test_handler_never_raises_on_call_error(tmp_path, monkeypatch):
    path = _write_config(tmp_path, SAMPLE_SERVERS)

    async def fake_list(name, server_def):
        return [FakeTool("read")]

    async def boom(server_def, tool_name, arguments):
        raise RuntimeError("server died mid-call")

    monkeypatch.setattr(mc, "sdk_available", lambda: True)
    monkeypatch.setattr(mc, "_list_server_tools", fake_list)
    monkeypatch.setattr(mc, "_call_server_tool", boom)

    tools = await mc.load_mcp_tools(path=path)
    params = FakeParams({"q": "x"})
    await tools[0]["handler"](params)  # must not raise
    assert "MCP error" in params.result


# ── defensive graceful-empty paths ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_sdk_returns_empty(tmp_path, monkeypatch):
    path = _write_config(tmp_path, SAMPLE_SERVERS)
    monkeypatch.setattr(mc, "_sdk", lambda: None)  # SDK absent
    assert mc.sdk_available() is False
    tools = await mc.load_mcp_tools(path=path)
    assert tools == []


@pytest.mark.asyncio
async def test_missing_config_returns_empty(monkeypatch):
    monkeypatch.setattr(mc, "sdk_available", lambda: True)
    tools = await mc.load_mcp_tools(path="/no/such/mcp.json")
    assert tools == []


@pytest.mark.asyncio
async def test_unreachable_server_returns_empty(tmp_path, monkeypatch):
    path = _write_config(tmp_path, SAMPLE_SERVERS)

    async def unreachable(name, server_def):
        # mirrors the real _list_server_tools swallow-and-return-[] on failure
        return []

    monkeypatch.setattr(mc, "sdk_available", lambda: True)
    monkeypatch.setattr(mc, "_list_server_tools", unreachable)
    tools = await mc.load_mcp_tools(path=path)
    assert tools == []  # no raise, just nothing discovered


@pytest.mark.asyncio
async def test_real_list_server_tools_swallows_failure(tmp_path):
    # No monkeypatch of the seam: a bogus stdio command must fail gracefully.
    server_def = {"type": "stdio", "command": ["definitely-not-a-real-binary-xyz"]}
    out = await mc._list_server_tools("files", server_def)
    assert out == []


# ── pull_postulaciones (MCP-backed source for the typed task) ─────────────────────

@pytest.mark.asyncio
async def test_pull_postulaciones_parses_json(tmp_path, monkeypatch):
    path = _write_config(tmp_path, SAMPLE_SERVERS)
    monkeypatch.setattr(mc.Config, "mcp_config_path", path)

    async def fake_call(server_def, tool_name, arguments):
        return json.dumps([
            {"empresa": "Acme", "rol": "Intern", "estado": "enviada"},
            {"empresa": "Beta"},
        ])

    monkeypatch.setattr(mc, "_call_server_tool", fake_call)
    items = await mc.pull_postulaciones("mcp__files__list_apps")
    assert len(items) == 2
    assert items[0]["empresa"] == "Acme"


@pytest.mark.asyncio
async def test_pull_postulaciones_bad_name_empty():
    assert await mc.pull_postulaciones("not_namespaced") == []


def test_parse_postulaciones_object_with_items():
    text = json.dumps({"items": [{"empresa": "X"}], "meta": 1})
    assert mc._parse_postulaciones(text) == [{"empresa": "X"}]


def test_parse_postulaciones_garbage_empty():
    assert mc._parse_postulaciones("not json") == []


# ── CLI listing helper ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_list_lines(tmp_path, monkeypatch):
    path = _write_config(tmp_path, SAMPLE_SERVERS)

    async def fake_list(name, server_def):
        return [FakeTool("read")]

    monkeypatch.setattr(mc, "sdk_available", lambda: True)
    monkeypatch.setattr(mc, "_list_server_tools", fake_list)

    lines = await mc.mcp_list_lines(path)
    text = "\n".join(lines)
    assert "files" in text
    assert "mcp__files__read" in text


@pytest.mark.asyncio
async def test_mcp_list_lines_no_servers(tmp_path):
    path = _write_config(tmp_path, {})
    lines = await mc.mcp_list_lines(path)
    assert any("No MCP servers configured" in line for line in lines)
