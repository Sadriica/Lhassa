"""
ClaudeCliAgent — runs any agent type through the Claude Code CLI (claude --print).

When POTTS_AGENT_BACKEND=claude_code, all three agent types (code, research, system)
are delegated to `claude --print "<task>"` instead of the built-in LLM tool loop.
This uses your Claude Code subscription (Pro/Max) rather than a separate API key.

The `claude` CLI must be installed and already authenticated on the machine.
Set POTTS_AGENT_DIR to the directory you want Claude Code to operate in
(defaults to the cwd where Potts was launched).
"""

import asyncio
import platform
import re
import uuid
from pathlib import Path

from loguru import logger

from potts.state import AgentRun, state

# Strip ANSI escape codes from claude's terminal output
_ANSI = re.compile(r"\x1b\[[0-9;]*[mGKHF]")

# Build a brief OS-aware context hint for the system agent.
_SYSTEM = platform.system()
if _SYSTEM == "Darwin":
    _OS_HINT = f"Context: macOS {platform.mac_ver()[0]}, Homebrew, launchctl.\n\n"
elif _SYSTEM == "Windows":
    _OS_HINT = f"Context: Windows {platform.version()}, PowerShell, winget.\n\n"
else:
    import shutil as _shutil
    _pkg = "pacman/yay" if _shutil.which("pacman") else ("apt" if _shutil.which("apt") else "dnf")
    try:
        _distro = platform.freedesktop_os_release().get("NAME", "Linux")  # Python 3.10+
    except Exception:
        _distro = "Linux"
    _OS_HINT = f"Context: {_distro}, {_pkg}, systemd.\n\n"

# Brief context hints prepended to the task for each agent type.
# Claude doesn't need a full system prompt the way smaller models do, but the
# system agent benefits from knowing the machine environment.
_HINTS = {
    "code":     "",
    "research": "",
    "system":   _OS_HINT,
}


def make_agent(agent_type: str) -> "ClaudeCliAgent":
    """Return a ClaudeCliAgent configured for the given agent type."""
    return ClaudeCliAgent(
        name=agent_type,
        hint=_HINTS.get(agent_type, ""),
    )


class ClaudeCliAgent:
    def __init__(self, name: str, hint: str = ""):
        self.name = name
        self._hint = hint

    async def run(self, task: str) -> str:
        from potts.config import Config
        config = Config()
        cwd = Path(config.agent_dir).expanduser() if config.agent_dir else Path.cwd()

        run_id = str(uuid.uuid4())[:8]
        run = AgentRun(
            id=run_id,
            name=f"{self.name} (claude)",
            status="running",
            task=task,
        )
        state.register_agent(run)

        await state.broadcast({
            "type": "agent_started",
            "run_id": run_id,
            "agent": run.name,
            "task": task,
        })

        try:
            result = await self._invoke(run_id, run, task, cwd)
            state.finish_agent(run_id, result, success=True)
            await state.broadcast({"type": "agent_done", "run_id": run_id, "output": result})
            return result

        except asyncio.CancelledError:
            state.finish_agent(run_id, run.output or "(cancelled)", success=False)
            raise
        except Exception as e:
            logger.error(f"[{self.name}/claude] {e}")
            state.finish_agent(run_id, str(e), success=False)
            await state.broadcast({"type": "agent_error", "run_id": run_id, "error": str(e)})
            return f"Agent {self.name} (claude) error: {e}"

    async def _invoke(self, run_id: str, run: AgentRun, task: str, cwd: Path) -> str:
        prompt = self._hint + task
        logger.info(f"[{self.name}/claude] cwd={cwd} task={task[:80]!r}")

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", prompt,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "claude CLI not found in PATH. "
                "Install Claude Code (https://claude.ai/code) "
                "or set POTTS_AGENT_BACKEND=builtin in .env."
            )

        if proc.stdout is None or proc.stderr is None:
            raise RuntimeError("claude process pipes unavailable")

        stderr_lines: list[str] = []

        async def _drain_stdout():
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = _ANSI.sub("", line_bytes.decode(errors="replace"))
                run.output += line
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": line})

        async def _drain_stderr():
            while True:
                line_bytes = await proc.stderr.readline()
                if not line_bytes:
                    break
                stderr_lines.append(_ANSI.sub("", line_bytes.decode(errors="replace")))

        await asyncio.gather(_drain_stdout(), _drain_stderr())

        await proc.wait()

        if proc.returncode != 0:
            err = "".join(stderr_lines)
            raise RuntimeError(f"claude exited {proc.returncode}: {err[:500]}")

        return run.output.strip()
