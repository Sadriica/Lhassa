"""
BaseAgent — autonomous background agent with tool access.

Uses the active LLM provider (same as the voice pipeline) and runs a ReAct-style
tool loop: call LLM → execute any tool calls → feed results back → repeat until done.

Available agent tools: web_search, shell, read_file, write_file.
"""

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

from loguru import logger

from lhassa.state import AgentRun, state
from lhassa.tools.guardrails import validate_shell_command, validate_write_path

MAX_ITERATIONS = int(os.getenv("LHASSA_MAX_AGENT_ITERATIONS", "10"))  # Safety cap on tool-calling loops
_SHELL_OUTPUT_MAX = int(os.getenv("LHASSA_SHELL_OUTPUT_MAX", "3000"))  # Max chars returned from shell to agent
_FILE_CONTENT_MAX = int(os.getenv("LHASSA_FILE_CONTENT_MAX", "4000"))  # Max chars returned from read_file to agent

# ── Tool schemas (OpenAI function-calling format) ──────────────────────────────

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the internet for up-to-date information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 4},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run a shell command on the user's machine.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the filesystem.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
]

# Anthropic tool format (converted from OpenAI format at call time)
def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in tools
    ]


# ── Tool execution ─────────────────────────────────────────────────────────────

async def _execute_tool(name: str, args: dict) -> str:
    try:
        if name == "web_search":
            from lhassa.tools.search import web_search
            results = await web_search(args.get("query", ""), args.get("max_results", 4))
            if not results:
                return "No results found."
            return "\n\n".join(f"{r['title']}\n{r['body']}\n{r['url']}" for r in results)

        elif name == "shell":
            cmd = args.get("command", "")
            validation_error = validate_shell_command(cmd)
            if validation_error:
                await state.record_audit_event(
                    action="shell",
                    target=cmd[:200],
                    source="agent",
                    status="blocked",
                    detail=validation_error,
                )
                return validation_error
            result = await asyncio.to_thread(
                lambda: subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no output)"
            await state.record_audit_event(
                action="shell",
                target=cmd[:200],
                source="agent",
                status="ok" if result.returncode == 0 else "error",
                detail=f"exit_code={result.returncode}",
            )
            return output[:_SHELL_OUTPUT_MAX] + "\n...(truncated)" if len(output) > _SHELL_OUTPUT_MAX else output

        elif name == "read_file":
            path_str = args.get("path", "").strip()
            if not path_str:
                return "Error: path is required for read_file"
            p = Path(path_str).expanduser()
            size = p.stat().st_size
            if size > 512 * 1024:
                return (
                    f"File is too large to read ({size // 1024} KB). "
                    "Use shell with head/tail/grep to inspect specific parts."
                )
            content = p.read_text(errors="replace")
            return content[:_FILE_CONTENT_MAX] + "\n...(truncated)" if len(content) > _FILE_CONTENT_MAX else content

        elif name == "write_file":
            p, validation_error = validate_write_path(args.get("path", ""))
            if validation_error:
                await state.record_audit_event(
                    action="write_file",
                    target=str(args.get("path", ""))[:200],
                    source="agent",
                    status="blocked",
                    detail=validation_error,
                )
                return validation_error
            assert p is not None
            p.parent.mkdir(parents=True, exist_ok=True)
            content = args.get("content", "")
            p.write_text(content)
            await state.record_audit_event(
                action="write_file",
                target=str(p)[:200],
                source="agent",
                status="ok",
                detail=f"bytes={len(content)}",
            )
            return f"File written: {p}"

        else:
            return f"Unknown tool: {name}"

    except subprocess.TimeoutExpired:
        await state.record_audit_event(
            action=name,
            target=str(args)[:200],
            source="agent",
            status="error",
            detail="timeout",
        )
        return "Shell command timed out after 30 seconds."
    except FileNotFoundError as e:
        return f"File not found: {args.get('path', str(e))}"
    except PermissionError as e:
        return f"Permission denied: {args.get('path', str(e))}"
    except Exception as e:
        await state.record_audit_event(
            action=name,
            target=str(args)[:200],
            source="agent",
            status="error",
            detail=str(e)[:200],
        )
        return f"Tool error ({name}): {e}"


# ── Base agent ─────────────────────────────────────────────────────────────────

class BaseAgent:
    name: str = "agent"
    system_prompt: str = "You are a helpful assistant."

    def __init__(self):
        from lhassa.config import Config
        config = Config()
        self._config = config
        self.model = config.model
        # Provider shape selects the loop branch / tool format; the model
        # round-trip itself goes through the LLM gateway (cache + budget + ledger).
        self._provider = "anthropic" if config.llm_provider == "anthropic" else "openai"

    async def run(self, task: str) -> str:
        """Execute the task with a tool loop. Streams tokens to the dashboard."""
        run_id = str(uuid.uuid4())[:8]
        run = AgentRun(id=run_id, name=self.name, status="running", task=task)
        state.register_agent(run)

        await state.broadcast({"type": "agent_started", "run_id": run_id, "agent": self.name, "task": task})

        try:
            if self._provider == "anthropic":
                result = await self._run_anthropic(run_id, run, task)
            else:
                result = await self._run_openai_compat(run_id, run, task)

            state.finish_agent(run_id, result, success=True)
            await state.broadcast({"type": "agent_done", "run_id": run_id, "output": result})
            return result

        except asyncio.CancelledError:
            state.finish_agent(run_id, run.output, success=False)
            raise
        except Exception as e:
            logger.error(f"[{self.name}] Error: {e}")
            state.finish_agent(run_id, str(e), success=False)
            await state.broadcast({"type": "agent_error", "run_id": run_id, "error": str(e)})
            return f"Agent {self.name} error: {e}"

    # ── OpenAI-compatible loop (Groq, OpenAI, OpenRouter, Ollama, Cerebras) ───

    async def _run_openai_compat(self, run_id: str, run: AgentRun, task: str) -> str:
        from lhassa.llm.gateway import gateway

        # System prompt is passed separately to the gateway (it prepends it).
        messages: list[dict] = [{"role": "user", "content": task}]

        for iteration in range(MAX_ITERATIONS):
            turn = await asyncio.wait_for(
                gateway.complete_with_tools(
                    messages,
                    AGENT_TOOLS,
                    config=self._config,
                    model=self.model,
                    system=self.system_prompt,
                    max_tokens=4096,
                ),
                timeout=60.0,
            )

            # Stream the text portion to the dashboard
            if turn.text:
                run.output += turn.text
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": turn.text})

            # No tool calls — we're done
            if not turn.tool_calls:
                return run.output or turn.text or ""

            # Re-materialise the assistant turn (with tool calls) for the next round.
            assistant_tool_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])},
                }
                for tc in turn.tool_calls
            ]
            messages.append(
                {"role": "assistant", "content": turn.text or None, "tool_calls": assistant_tool_calls}
            )

            # Execute each tool call and feed results back
            for tc in turn.tool_calls:
                logger.info(f"[{self.name}] tool: {tc['name']}({tc['input']})")
                note = f"\n[calling {tc['name']}...]\n"
                run.output += note
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": note})

                result = await _execute_tool(tc["name"], tc["input"])
                messages.append({"tool_call_id": tc["id"], "role": "tool", "content": result})

        note = f"\n\n[Agent stopped after {MAX_ITERATIONS} iterations — task may be incomplete.]"
        run.output += note
        await state.broadcast({"type": "agent_token", "run_id": run_id, "token": note})
        logger.warning(f"[{self.name}] reached MAX_ITERATIONS ({MAX_ITERATIONS}) — returning partial output")
        return run.output

    # ── Anthropic tool-use loop ────────────────────────────────────────────────

    async def _run_anthropic(self, run_id: str, run: AgentRun, task: str) -> str:
        from lhassa.llm.gateway import gateway

        messages: list[dict] = [{"role": "user", "content": task}]
        tools = _to_anthropic_tools(AGENT_TOOLS)

        for iteration in range(MAX_ITERATIONS):
            turn = await asyncio.wait_for(
                gateway.complete_with_tools(
                    messages,
                    tools,
                    config=self._config,
                    model=self.model,
                    system=self.system_prompt,
                    max_tokens=4096,
                ),
                timeout=60.0,
            )

            if turn.text:
                run.output += turn.text
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": turn.text})

            # No tool use — done
            if not turn.tool_calls:
                return run.output

            # Re-materialise the assistant turn (text + tool_use blocks).
            assistant_content: list[dict] = []
            if turn.text:
                assistant_content.append({"type": "text", "text": turn.text})
            for tc in turn.tool_calls:
                assistant_content.append(
                    {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
                )
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools and build tool_result blocks
            tool_result_content: list[dict] = []
            for tc in turn.tool_calls:
                logger.info(f"[{self.name}] tool: {tc['name']}({tc['input']})")
                note = f"\n[calling {tc['name']}...]\n"
                run.output += note
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": note})

                result = await _execute_tool(tc["name"], tc["input"])
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_result_content})

        note = f"\n\n[Agent stopped after {MAX_ITERATIONS} iterations — task may be incomplete.]"
        run.output += note
        await state.broadcast({"type": "agent_token", "run_id": run_id, "token": note})
        logger.warning(f"[{self.name}] reached MAX_ITERATIONS ({MAX_ITERATIONS}) — returning partial output")
        return run.output
