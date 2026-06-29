"""
LocalCliAgent — drive any of several local AI coding CLIs as a subprocess.

Potts can dispatch a coding task to whichever local CLI the user has installed
and authenticated, taking advantage of an existing subscription instead of a
separate API key. The selected CLI is run in non-interactive ("print") mode, its
output is streamed line-by-line to the dashboard via state.broadcast, and the
final text is returned.

Supported CLIs (selectable via POTTS_AI_CLI):
  claude    → `claude --print "<task>"`
  codex     → `codex exec "<task>"`     (OpenAI Codex CLI non-interactive subcommand)
  opencode  → `opencode run "<task>"`   (opencode non-interactive subcommand)

The per-CLI argv prefixes live in the _CLI_ARGV table below. If a CLI changes
its non-interactive flag, edit that single table — the task string is always
appended as the final argument.

This is additive: it does not replace the claude_code agent backend. Auto-detection
picks the first installed CLI (in CLI_PRIORITY order) when POTTS_AI_CLI is unset.
"""

import asyncio
import os
import platform
import re
import shutil
import uuid
from pathlib import Path

from loguru import logger

from potts.state import AgentRun, state

# Strip ANSI escape codes from terminal output the CLIs may emit.
_ANSI = re.compile(r"\x1b\[[0-9;]*[mGKHF]")

# ── Per-CLI invocation table ───────────────────────────────────────────────────
# Maps a CLI name to the argv prefix that runs it non-interactively.
# The task string is appended as the final argument at call time, i.e.
#   argv = _CLI_ARGV[cli] + [task]
# Assumption: each CLI accepts the prompt as a single positional argument after
# its print/exec/run subcommand. Edit here if a CLI's flag differs.
_CLI_ARGV: dict[str, list[str]] = {
    "claude":   ["claude", "--print"],
    "codex":    ["codex", "exec"],
    "opencode": ["opencode", "run"],
}

# Order in which auto-detection prefers an installed CLI.
CLI_PRIORITY: tuple[str, ...] = ("claude", "codex", "opencode")

# Default timeout (seconds) for a single CLI run; override with POTTS_AI_CLI_TIMEOUT.
try:
    _DEFAULT_TIMEOUT = int(os.getenv("POTTS_AI_CLI_TIMEOUT", "600"))
except ValueError:
    _DEFAULT_TIMEOUT = 600


# Brief OS-aware context hint, mirroring claude_code's prompt building.
_SYSTEM = platform.system()
if _SYSTEM == "Darwin":
    _OS_HINT = f"Context: macOS {platform.mac_ver()[0]}, Homebrew, launchctl.\n\n"
elif _SYSTEM == "Windows":
    _OS_HINT = f"Context: Windows {platform.version()}, PowerShell, winget.\n\n"
else:
    _pkg = "pacman/yay" if shutil.which("pacman") else ("apt" if shutil.which("apt") else "dnf")
    try:
        _distro = platform.freedesktop_os_release().get("NAME", "Linux")  # Python 3.10+
    except Exception:
        _distro = "Linux"
    _OS_HINT = f"Context: {_distro}, {_pkg}, systemd.\n\n"


def cli_binary(cli: str) -> str:
    """Return the executable name for a CLI (the first argv element)."""
    argv = _CLI_ARGV.get(cli)
    return argv[0] if argv else cli


def is_installed(cli: str) -> bool:
    """True if the CLI's executable is found in PATH."""
    return cli in _CLI_ARGV and shutil.which(cli_binary(cli)) is not None


def available_clis() -> list[str]:
    """Return the installed CLIs, in CLI_PRIORITY order."""
    return [cli for cli in CLI_PRIORITY if is_installed(cli)]


def detect_ai_cli() -> str:
    """
    Resolve which AI CLI to use.

    Returns POTTS_AI_CLI if set (lowercased). Otherwise the first installed CLI in
    CLI_PRIORITY order, falling back to "claude" when none is installed (the tool
    surfaces a clear not-installed message at call time).
    """
    explicit = os.getenv("POTTS_AI_CLI")
    if explicit:
        return explicit.strip().lower()
    installed = available_clis()
    return installed[0] if installed else "claude"


def build_argv(cli: str, task: str) -> list[str]:
    """Build the full argv for running `cli` non-interactively on `task`."""
    if cli not in _CLI_ARGV:
        raise ValueError(f"Unknown AI CLI: {cli!r}. Valid options: {', '.join(_CLI_ARGV)}")
    return [*_CLI_ARGV[cli], task]


class LocalCliAgent:
    def __init__(self, cli: str | None = None, hint: str = "", timeout: int | None = None):
        self.cli = (cli or detect_ai_cli()).lower()
        self._hint = hint
        self._timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT

    async def run(self, task: str) -> str:
        """Dispatch `task` to the selected CLI. Never raises — returns a string."""
        from potts.config import Config
        config = Config()
        cwd = Path(config.agent_dir).expanduser() if config.agent_dir else Path.cwd()

        if self.cli not in _CLI_ARGV:
            return (
                f"Unknown AI CLI {self.cli!r}. "
                f"Valid options: {', '.join(_CLI_ARGV)} (set POTTS_AI_CLI)."
            )

        if not is_installed(self.cli):
            return (
                f"The '{cli_binary(self.cli)}' CLI is not installed or not in PATH. "
                f"Install it, or set POTTS_AI_CLI to one of: {', '.join(available_clis()) or 'none installed'}."
            )

        run_id = str(uuid.uuid4())[:8]
        run = AgentRun(
            id=run_id,
            name=f"ai-cli ({self.cli})",
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
            logger.error(f"[ai-cli/{self.cli}] {e}")
            state.finish_agent(run_id, str(e), success=False)
            await state.broadcast({"type": "agent_error", "run_id": run_id, "error": str(e)})
            return f"AI CLI {self.cli} error: {e}"

    async def _invoke(self, run_id: str, run: AgentRun, task: str, cwd: Path) -> str:
        prompt = self._hint + task
        argv = build_argv(self.cli, prompt)
        logger.info(f"[ai-cli/{self.cli}] cwd={cwd} task={task[:80]!r}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"{cli_binary(self.cli)} CLI not found in PATH. "
                f"Install it or set POTTS_AI_CLI to an installed CLI."
            )

        if proc.stdout is None or proc.stderr is None:
            raise RuntimeError(f"{self.cli} process pipes unavailable")

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

        try:
            await asyncio.wait_for(
                asyncio.gather(_drain_stdout(), _drain_stderr()),
                timeout=self._timeout,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"{self.cli} timed out after {self._timeout}s and was cancelled."
            )

        if proc.returncode != 0:
            err = "".join(stderr_lines)
            raise RuntimeError(f"{self.cli} exited {proc.returncode}: {err[:500]}")

        return run.output.strip()


def make_agent(cli: str | None = None) -> LocalCliAgent:
    """Return a LocalCliAgent for the given CLI (or the auto-detected default)."""
    return LocalCliAgent(cli=cli, hint=_OS_HINT)
