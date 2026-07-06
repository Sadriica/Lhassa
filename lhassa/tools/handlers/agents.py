"""Agent-delegation tool handlers (background agents + local AI CLIs)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.llm_service import FunctionCallParams

import asyncio

from loguru import logger

from lhassa.agents.claude_code import make_agent as make_claude_agent
from lhassa.agents.code import CodeAgent
from lhassa.agents.research import ResearchAgent
from lhassa.agents.system import SystemAgent
from lhassa.config import Config
from lhassa.tools.handlers._shared import _bump_tool


async def handle_delegate_to_agent(params: FunctionCallParams):
    agent_type = params.arguments.get("agent", "research")
    task = params.arguments.get("task", "")
    logger.info(f"[tool] delegate_to_agent: {agent_type} → {task[:60]!r}")
    print(f"\033[2m[🤖 delegating to {agent_type}: {task[:60]}]\033[0m")
    _bump_tool()

    if Config().agent_backend == "claude_code":
        agent = make_claude_agent(agent_type)
    else:
        agent_map = {"code": CodeAgent, "research": ResearchAgent, "system": SystemAgent}
        agent = agent_map.get(agent_type, ResearchAgent)()

    async def _run():
        try:
            result = await agent.run(task)
            from lhassa.voice.notifier import notify
            summary = result[:200] + "..." if len(result) > 200 else result
            await notify(f"Agent {agent_type} finished. {summary}")
        except Exception as exc:
            logger.error(f"[delegate_to_agent] background task failed: {exc}")

    asyncio.create_task(_run(), name=f"agent-{agent_type}")
    await params.result_callback(
        f"Agent '{agent_type}' started in the background. I'll notify you when it's done."
    )


async def handle_run_ai_cli(params: FunctionCallParams):
    task = params.arguments.get("task", "")
    cli = params.arguments.get("cli") or None
    _bump_tool()

    try:
        from lhassa.agents.local_cli import make_agent as make_local_cli_agent
        agent = make_local_cli_agent(cli)
        logger.info(f"[tool] run_ai_cli: {agent.cli} → {task[:60]!r}")
        print(f"\033[2m[🛠 {agent.cli}: {task[:60]}]\033[0m")
        result = await agent.run(task)
        await params.result_callback(result)
    except Exception as e:
        logger.error(f"run_ai_cli error: {e}")
        await params.result_callback(f"AI CLI error: {e}")
