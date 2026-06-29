"""AppState singleton — shared mutable state across voice pipeline, API, and scheduler."""

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class AgentRun:
    id: str
    name: str
    status: str           # "running" | "done" | "error"
    output: str = ""
    task: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0


@dataclass
class AuditEvent:
    ts: float
    action: str
    target: str
    source: str
    status: str
    detail: str = ""


class AppState:
    _AUDIT_PATH = Path.home() / ".potts" / "audit.json"

    def __init__(self):
        # Configurable via POTTS_AUDIT_MAX (default 100, minimum 10)
        self._MAX_AUDIT_EVENTS: int = max(10, int(os.getenv("POTTS_AUDIT_MAX", "100")))

        # Set by the voice pipeline — used by notifier and for session recovery
        self.pipeline_task: Optional[object] = None
        self.llm_context: Optional[object] = None   # LLMContext ref for session saving on shutdown
        self.is_voice_active: bool = False

        self._ws_clients: set[asyncio.Queue] = set()
        self.agent_runs: dict[str, AgentRun] = {}
        self.metrics: dict = {
            "llm_calls": 0,
            "tool_calls": 0,
            "sessions": 0,
            "agents_launched": 0,
            "blocked_actions": 0,
        }
        self.audit_events: list[AuditEvent] = []
        self._audit_loaded = False

    async def set_voice_active(self, active: bool):
        if self.is_voice_active == active:
            return
        self.is_voice_active = active
        await self.broadcast({"type": "voice_state", "voice_active": active})

    def load_audit_events(self):
        if self._audit_loaded:
            return
        self._audit_loaded = True
        try:
            if not self._AUDIT_PATH.exists():
                return
            raw = json.loads(self._AUDIT_PATH.read_text())
            events = [
                AuditEvent(**item)
                for item in raw[: self._MAX_AUDIT_EVENTS]
                if isinstance(item, dict)
            ]
            self.audit_events = events
            self.metrics["blocked_actions"] = sum(
                1 for event in events if event.status == "blocked"
            )
        except Exception:
            self.audit_events = []

    def _save_audit_events(self):
        self._AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(event) for event in self.audit_events[: self._MAX_AUDIT_EVENTS]]
        self._AUDIT_PATH.write_text(json.dumps(payload, ensure_ascii=True, indent=2))

    def replace_audit_events(self, events: list[dict] | list[AuditEvent]):
        loaded: list[AuditEvent] = []
        for item in events[: self._MAX_AUDIT_EVENTS]:
            if isinstance(item, AuditEvent):
                loaded.append(item)
            elif isinstance(item, dict):
                loaded.append(AuditEvent(**item))
        self.audit_events = loaded
        self._audit_loaded = True
        self.metrics["blocked_actions"] = sum(
            1 for event in loaded if event.status == "blocked"
        )
        self._save_audit_events()

    async def record_audit_event(
        self,
        action: str,
        target: str,
        source: str,
        status: str,
        detail: str = "",
    ):
        event = AuditEvent(
            ts=time.time(),
            action=action,
            target=target,
            source=source,
            status=status,
            detail=detail,
        )
        self.audit_events.insert(0, event)
        del self.audit_events[self._MAX_AUDIT_EVENTS :]
        if status == "blocked":
            self.metrics["blocked_actions"] += 1
        self._save_audit_events()
        await self.broadcast({"type": "audit_event", "event": asdict(event)})

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
            logger.debug("WebSocket client dropped (queue full — too slow to consume events)")

    # ── Agent tracking ─────────────────────────────────────────────────────────

    _MAX_AGENT_RUNS = 50  # keep only the most recent N agents in memory

    def register_agent(self, run: AgentRun):
        self.agent_runs[run.id] = run
        self.metrics["agents_launched"] += 1
        # Evict oldest completed agents if we exceed the cap
        if len(self.agent_runs) > self._MAX_AGENT_RUNS:
            finished = [
                (rid, r) for rid, r in self.agent_runs.items()
                if r.status in ("done", "error")
            ]
            finished.sort(key=lambda x: x[1].finished_at)
            for rid, _ in finished[: len(self.agent_runs) - self._MAX_AGENT_RUNS]:
                del self.agent_runs[rid]

    def finish_agent(self, run_id: str, output: str, success: bool = True):
        run = self.agent_runs.get(run_id)
        if run:
            run.status = "done" if success else "error"
            run.output = output
            run.finished_at = time.time()


# Module-level singleton — import from anywhere via `from potts.state import state`
state = AppState()
