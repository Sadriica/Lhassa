# SPDX-License-Identifier: AGPL-3.0-or-later
"""
LocalCliAgent; drive any of several local AI coding CLIs as a subprocess.

Lhassa can dispatch a coding task to whichever local CLI the user has installed
and authenticated, taking advantage of an existing subscription instead of a
separate API key. The selected CLI is run in non-interactive ("print") mode, its
output is streamed line-by-line to the dashboard via state.broadcast, and the
final text is returned.

Supported CLIs (selectable via LHASSA_AI_CLI):
  claude    → `claude --print "<task>"`
  codex     → `codex exec "<task>"`     (OpenAI Codex CLI non-interactive subcommand)
  opencode  → `opencode run "<task>"`   (opencode non-interactive subcommand)

The per-CLI argv prefixes live in the _CLI_ARGV table below. If a CLI changes
its non-interactive flag, edit that single table; the task string is always
appended as the final argument.

This is additive: it does not replace the claude_code agent backend. Auto-detection
picks the first installed CLI (in CLI_PRIORITY order) when LHASSA_AI_CLI is unset.
"""

import asyncio
import os
import platform
import re
import shutil
import signal
import uuid
from pathlib import Path

from loguru import logger

from lhassa.state import AgentRun, state

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

# Default timeout (seconds) for a single CLI run; override with LHASSA_AI_CLI_TIMEOUT.
try:
    _DEFAULT_TIMEOUT = int(os.getenv("LHASSA_AI_CLI_TIMEOUT", "600"))
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


# ── In-flight run tracking (for STOP) ───────────────────────────────────────────
# Maps an active run_id → its running subprocess so POST /api/code/{id}/stop can
# terminate the whole process group (the CLI plus any children it spawns). Purely
# in-memory; entries are freed when the run ends (normally or via a stop). Stopping
# an unknown / already-finished run is a safe no-op.
_RUNNING: dict[str, object] = {}
# run_ids we deliberately stopped; a resulting nonzero exit is expected, not an error.
_STOPPED: set[str] = set()

# Grace period between SIGTERM and SIGKILL when stopping a run.
_STOP_GRACE = 1.5


def _register_proc(run_id: str, proc: object) -> None:
    _RUNNING[run_id] = proc


def _unregister_proc(run_id: str) -> None:
    _RUNNING.pop(run_id, None)
    _STOPPED.discard(run_id)


def is_running(run_id: str) -> bool:
    """True if a subprocess for this run is currently tracked as in-flight."""
    return run_id in _RUNNING


def was_stopped(run_id: str) -> bool:
    """True if this run was explicitly stopped (so a nonzero exit is expected)."""
    return run_id in _STOPPED


def _kill_group(proc: object, sig: int) -> None:
    """Send `sig` to the child's whole process group; degrade to the lone pid.

    Reuses the project's killpg pattern (see security/quarantine.py) so child CLIs
    spawned by the agent die too. Never raises; the process may already be gone.
    """
    try:
        os.killpg(os.getpgid(proc.pid), sig)  # type: ignore[attr-defined]
    except (ProcessLookupError, PermissionError, AttributeError):
        try:
            proc.send_signal(sig)  # type: ignore[attr-defined]
        except Exception:  # already dead / no such signal; nothing to do
            pass


async def stop_run(run_id: str) -> bool:
    """Terminate a tracked run's process group (graceful SIGTERM → SIGKILL).

    Returns True if a live subprocess was found and signalled, False if the run is
    unknown or already finished (idempotent no-op). Never raises; a stop must never
    take down the caller.
    """
    proc = _RUNNING.get(run_id)
    if proc is None:
        return False
    _STOPPED.add(run_id)
    try:
        _kill_group(proc, signal.SIGTERM)
        await asyncio.sleep(_STOP_GRACE)
        if getattr(proc, "returncode", None) is None:  # still alive after grace
            _kill_group(proc, signal.SIGKILL)
    except Exception as e:  # never propagate; the run is already being torn down
        logger.error(f"[ai-cli] stop_run({run_id}) failed: {e}")
    return True


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

    Returns LHASSA_AI_CLI if set (lowercased). Otherwise the first installed CLI in
    CLI_PRIORITY order, falling back to "claude" when none is installed (the tool
    surfaces a clear not-installed message at call time).
    """
    explicit = os.getenv("LHASSA_AI_CLI")
    if explicit:
        return explicit.strip().lower()
    installed = available_clis()
    return installed[0] if installed else "claude"


def build_argv(cli: str, task: str) -> list[str]:
    """Build the full argv for running `cli` non-interactively on `task`."""
    if cli not in _CLI_ARGV:
        raise ValueError(f"Unknown AI CLI: {cli!r}. Valid options: {', '.join(_CLI_ARGV)}")
    return [*_CLI_ARGV[cli], task]


def context_pack_hint(pack_dir) -> str:
    """A short pointer block naming a project's context pack files.

    Context is a gift, not a gate: this is pure string formatting over an
    already-resolved pack directory, so it never fails a launch. Callers pass
    ``None``/falsy when no project matched; the prompt is simply unchanged.
    """
    if not pack_dir:
        return ""
    try:
        base = str(pack_dir)
        names = ("VISION.md", "ARCHITECTURE.md", "RULES.md", "MEMORY.md")
        paths = ", ".join(f"{base}/{name}" for name in names)
        return f"Project context (read these first): {paths}\n\n"
    except Exception:
        return ""


class LocalCliAgent:
    def __init__(self, cli: str | None = None, hint: str = "", timeout: int | None = None,
                 on_line=None, context_pack_dir=None):
        self.cli = (cli or detect_ai_cli()).lower()
        self._hint = hint
        self._timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT
        # Optional sink called with each stdout line as it streams (e.g. the CLI's
        # `lhassa code` prints them live). Best-effort; a failing sink never stops the run.
        self._on_line = on_line
        # An already-resolved project context pack dir (see harness/context_pack.py),
        # or None when no project matched the run's cwd. Prepended to the prompt as a
        # pointer block; for CLIs that support an add-dir style flag, also added to argv.
        self.context_pack_dir = context_pack_dir

    async def run(self, task: str) -> str:
        """Dispatch `task` to the selected CLI. Never raises; returns a string."""
        from lhassa.config import Config
        config = Config()
        cwd = Path(config.agent_dir).expanduser() if config.agent_dir else Path.cwd()

        if self.cli not in _CLI_ARGV:
            return (
                f"Unknown AI CLI {self.cli!r}. "
                f"Valid options: {', '.join(_CLI_ARGV)} (set LHASSA_AI_CLI)."
            )

        if not is_installed(self.cli):
            return (
                f"The '{cli_binary(self.cli)}' CLI is not installed or not in PATH. "
                f"Install it, or set LHASSA_AI_CLI to one of: {', '.join(available_clis()) or 'none installed'}."
            )

        run_id = str(uuid.uuid4())[:8]
        run = AgentRun(
            id=run_id,
            name=f"ai-cli ({self.cli})",
            status="running",
            task=task,
            cli=self.cli,
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
        prompt = self._hint + context_pack_hint(self.context_pack_dir) + task
        argv = build_argv(self.cli, prompt)
        logger.info(f"[ai-cli/{self.cli}] cwd={cwd} task={task[:80]!r}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Own process group so STOP can killpg the CLI and any children it spawns.
                start_new_session=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"{cli_binary(self.cli)} CLI not found in PATH. "
                f"Install it or set LHASSA_AI_CLI to an installed CLI."
            )

        if proc.stdout is None or proc.stderr is None:
            raise RuntimeError(f"{self.cli} process pipes unavailable")

        # Track the live subprocess so POST /api/code/{id}/stop can find and kill it.
        _register_proc(run_id, proc)
        stderr_lines: list[str] = []
        recorder = _open_run_recorder(self.cli, run_id)

        async def _drain_stdout():
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = _ANSI.sub("", line_bytes.decode(errors="replace"))
                run.output += line
                if recorder is not None:
                    recorder.record_line(line)
                if self._on_line is not None:
                    try:
                        self._on_line(line)
                    except Exception:  # a bad sink must never break the run
                        pass
                await state.broadcast({"type": "agent_token", "run_id": run_id, "token": line})

        async def _drain_stderr():
            while True:
                line_bytes = await proc.stderr.readline()
                if not line_bytes:
                    break
                stderr_lines.append(_ANSI.sub("", line_bytes.decode(errors="replace")))

        try:
            try:
                await asyncio.wait_for(
                    asyncio.gather(_drain_stdout(), _drain_stderr()),
                    timeout=self._timeout,
                )
                await proc.wait()
            except asyncio.TimeoutError:
                _kill_group(proc, signal.SIGKILL)
                await proc.wait()
                raise RuntimeError(
                    f"{self.cli} timed out after {self._timeout}s and was cancelled."
                )

            # A stopped run exits nonzero by design (we killed it); that's a clean
            # stop, not a failure. Return whatever it streamed so far.
            if proc.returncode != 0 and not was_stopped(run_id):
                err = "".join(stderr_lines)
                raise RuntimeError(f"{self.cli} exited {proc.returncode}: {err[:500]}")

            return run.output.strip()
        finally:
            _unregister_proc(run_id)
            if recorder is not None:
                recorder.close(f"exit {proc.returncode}" if proc.returncode is not None else "")


def _open_run_recorder(cli: str, run_id: str):
    """A line recorder for this CLI's stream, or ``None`` (recording off, unknown CLI).

    The stream is parsed with the CLI's own harness adapter, so a run started here
    leaves the same history as a job launched through ``harness/runner.py``.
    """
    try:
        from lhassa.config import Config
        from lhassa.harness.adapter import ADAPTERS
        from lhassa.harness.recorder import open_recorder

        adapter = ADAPTERS.get_or_none(cli)
        rec = open_recorder(Config.instance_id, run_id, cli) if adapter is not None else None
        if rec is None:
            return None

        class _LineRecorder:
            def record_line(self, line: str) -> None:
                ev = adapter.parse_line(line)
                if ev is not None:
                    rec.record(ev)

            def close(self, estado: str = "") -> None:
                rec.close(estado)

        return _LineRecorder()
    except Exception:
        return None


def make_agent(cli: str | None = None, on_line=None, context_pack_dir=None) -> LocalCliAgent:
    """Return a LocalCliAgent for the given CLI (or the auto-detected default).

    ``on_line`` is an optional per-stdout-line sink for live streaming.
    ``context_pack_dir`` is an already-resolved project context pack directory
    (see ``harness/context_pack.py``), or ``None`` when no project matched.
    """
    return LocalCliAgent(
        cli=cli, hint=_OS_HINT, on_line=on_line, context_pack_dir=context_pack_dir
    )
