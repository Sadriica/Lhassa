"""
BaseAgent — autonomous background agent with tool access.

Uses the active LLM provider (same as the voice pipeline) and runs a ReAct-style
tool loop: call LLM → execute any tool calls → feed results back → repeat until done.

Available agent tools: web_search, shell, read_file, write_file.
"""

import asyncio
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from potts.state import AgentRun, state

MAX_ITERATIONS = 10  # Safety cap on tool-calling loops

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
            "description": "Run a shell command on the Linux/Arch machine.",
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
            from potts.tools.search import web_search
            results = await web_search(args.get("query", ""), args.get("max_results", 4))
            if not results:
                return "No results found."
            return "\n\n".join(f"{r['title']}\n{r['body']}\n{r['url']}" for r in results)

        elif name == "shell":
            cmd = args.get("command", "")
            result = await asyncio.to_thread(
                lambda: subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no output)"
            return output[:3000] + "\n...(truncated)" if len(output) > 3000 else output

        elif name == "read_file":
            content = Path(args.get("path", "")).expanduser().read_text()
            return content[:4000] + "\n...(truncated)" if len(content) > 4000 else content

        elif name == "write_file":
            p = Path(args.get("path", "")).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args.get("content", ""))
            return f"File written: {p}"

        else:
            return f"Unknown tool: {name}"

    except subprocess.TimeoutExpired:
        return "Shell command timed out after 30 seconds."
    except Exception as e:
        return f"Tool error ({name}): {e}"


# ── Base agent ─────────────────────────────────────────────────────────────────

class BaseAgent:
    name: str = "agent"
    system_prompt: str = "You are a helpful assistant."

    def __init__(self):
        from potts.config import Config
        from potts.llm.provider import get_agent_client
        config = Config()
        self.model = config.model
        self._client, self._provider = get_agent_client(config)

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
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]

        for iteration in range(MAX_ITERATIONS):
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=AGENT_TOOLS,
                tool_choice="auto",
                stream=False,
                max_tokens=4096,
            )
            state.metrics["groq_calls"] += 1

            msg = response.choices[0].message
            finish = response.choices[0].finish_reason

            # Stream the text portion to the dashboard
            if msg.content:
                run.output += msg.content
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": msg.content})

            # No tool calls — we're done
            if finish == "stop" or not msg.tool_calls:
                return run.output or msg.content or ""

            # Execute each tool call
            tool_results = []
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                logger.info(f"[{self.name}] tool: {tc.function.name}({args})")
                note = f"\n[calling {tc.function.name}...]\n"
                run.output += note
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": note})

                result = await _execute_tool(tc.function.name, args)
                tool_results.append({"tool_call_id": tc.id, "role": "tool", "content": result})

            messages.append(msg)  # assistant message with tool_calls
            messages.extend(tool_results)

        return run.output

    # ── Anthropic tool-use loop ────────────────────────────────────────────────

    async def _run_anthropic(self, run_id: str, run: AgentRun, task: str) -> str:
        import anthropic

        messages: list[dict] = [{"role": "user", "content": task}]
        tools = _to_anthropic_tools(AGENT_TOOLS)

        for iteration in range(MAX_ITERATIONS):
            response = await self._client.messages.create(
                model=self.model,
                system=self.system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=4096,
            )
            state.metrics["groq_calls"] += 1

            # Collect text and tool_use blocks
            text_parts: list[str] = []
            tool_calls: list[dict] = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(block)

            if text_parts:
                text = "".join(text_parts)
                run.output += text
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": text})

            # No tool use — done
            if response.stop_reason == "end_turn" or not tool_calls:
                return run.output

            # Execute tools and build tool_result blocks
            messages.append({"role": "assistant", "content": response.content})
            tool_result_content: list[dict] = []

            for tc in tool_calls:
                logger.info(f"[{self.name}] tool: {tc.name}({tc.input})")
                note = f"\n[calling {tc.name}...]\n"
                run.output += note
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": note})

                result = await _execute_tool(tc.name, tc.input)
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_result_content})

        return run.output
