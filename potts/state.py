"""AppState singleton — shared mutable state across voice pipeline, API, and scheduler."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentRun:
    id: str
    name: str
    status: str           # "running" | "done" | "error"
    output: str = ""
    task: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0


class AppState:
    def __init__(self):
        # Set by the voice pipeline — used by notifier and for session recovery
        self.pipeline_task: Optional[object] = None
        self.llm_context: Optional[object] = None   # LLMContext ref for session saving on shutdown
        self.is_voice_active: bool = False

        self._ws_clients: set[asyncio.Queue] = set()
        self.agent_runs: dict[str, AgentRun] = {}
        self.metrics: dict = {
            "groq_calls": 0,
            "tool_calls": 0,
            "sessions": 0,
            "agents_launched": 0,
        }

    # ── WebSocket fan-out ──────────────────────────────────────────────────────

    def add_ws_client(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._ws_clients.add(q)
        return q

    def remove_ws_client(self, q: asyncio.Queue):
        self._ws_clients.discard(q)

    async def broadcast(self, event: dict):
        """Fan-out a JSON-serialisable event to all connected WebSocket clients."""
        dead: list[asyncio.Queue] = []
        for q in list(self._ws_clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)  # slow client — remove it
        for q in dead:
            self._ws_clients.discard(q)

    # ── Agent tracking ─────────────────────────────────────────────────────────

    def register_agent(self, run: AgentRun):
        self.agent_runs[run.id] = run
        self.metrics["agents_launched"] += 1

    def finish_agent(self, run_id: str, output: str, success: bool = True):
        run = self.agent_runs.get(run_id)
        if run:
            run.status = "done" if success else "error"
            run.output = output
            run.finished_at = time.time()


# Module-level singleton — import from anywhere via `from potts.state import state`
state = AppState()
