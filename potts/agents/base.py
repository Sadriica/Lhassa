"""BaseAgent — wraps AsyncGroq streaming for background sub-agent tasks."""

import asyncio
import time
import uuid

from groq import AsyncGroq
from loguru import logger

from potts.config import Config
from potts.state import AgentRun, state


class BaseAgent:
    name: str = "agent"
    system_prompt: str = "You are a helpful assistant."

    def __init__(self, model: str | None = None):
        config = Config()
        self.model = model or config.model
        # Pass api_key explicitly so it works regardless of env var name
        self._client = AsyncGroq(api_key=config.groq_api_key)

    async def run(self, task: str) -> str:
        """Execute the task, stream tokens to the dashboard, return the full response."""
        run_id = str(uuid.uuid4())[:8]
        run = AgentRun(id=run_id, name=self.name, status="running", task=task)
        state.register_agent(run)

        await state.broadcast({
            "type": "agent_started",
            "run_id": run_id,
            "agent": self.name,
            "task": task,
        })

        output_parts: list[str] = []

        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": task},
                ],
                stream=True,
                max_tokens=4096,
            )
            state.metrics["groq_calls"] += 1

            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    output_parts.append(delta)
                    run.output += delta
                    await state.broadcast({
                        "type": "agent_token",
                        "run_id": run_id,
                        "token": delta,
                    })

            result = "".join(output_parts)
            state.finish_agent(run_id, result, success=True)
            await state.broadcast({
                "type": "agent_done",
                "run_id": run_id,
                "output": result,
            })
            return result

        except asyncio.CancelledError:
            state.finish_agent(run_id, "".join(output_parts), success=False)
            raise

        except Exception as e:
            logger.error(f"[{self.name}] Error: {e}")
            state.finish_agent(run_id, str(e), success=False)
            await state.broadcast({
                "type": "agent_error",
                "run_id": run_id,
                "error": str(e),
            })
            return f"Agent {self.name} error: {e}"
