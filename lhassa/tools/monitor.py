# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Proactive monitoring tools — watch processes and files in the background,
notify via voice (if active) or desktop when the condition is met.
"""

import asyncio
from pathlib import Path
from loguru import logger

from lhassa.state import state
from lhassa.voice.notifier import notify


def _stat_signature(p: Path) -> tuple[int, int, int, int]:
    st = p.stat()
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _read_bytes_from_offset(p: Path, offset: int) -> bytes:
    with p.open("rb") as f:
        f.seek(offset)
        return f.read()


async def _monitor_file_change(path: Path, display: str):
    try:
        baseline = _stat_signature(path)
    except FileNotFoundError:
        return

    while True:
        await asyncio.sleep(1)
        try:
            current = _stat_signature(path)
        except FileNotFoundError:
            continue

        if current != baseline:
            msg = f"File {display} changed."
            logger.info(f"[monitor] {msg}")
            await notify(msg, title="Lhassa — File Monitor")
            await state.broadcast({"type": "file_event", "path": str(path), "line": ""})
            return


async def _monitor_file_pattern(path: Path, display: str, pattern: str):
    offset = 0
    remainder = ""
    current_inode: tuple[int, int] | None = None
    pattern_lower = pattern.lower()

    while True:
        await asyncio.sleep(1)

        try:
            st = path.stat()
        except FileNotFoundError:
            continue

        inode = (st.st_dev, st.st_ino)
        if inode != current_inode:
            current_inode = inode
            offset = 0
            remainder = ""
        elif st.st_size < offset:
            offset = 0
            remainder = ""

        if st.st_size == offset:
            continue

        chunk = _read_bytes_from_offset(path, offset)
        offset += len(chunk)
        if not chunk:
            continue

        text = remainder + chunk.decode(errors="replace")
        lines = text.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            remainder = lines.pop()
        else:
            remainder = ""

        for raw_line in lines:
            line = raw_line.strip()
            if pattern_lower in line.lower():
                msg = f"File {display}: {line[:200]}"
                logger.info(f"[monitor] {msg}")
                await notify(msg, title="Lhassa — File Monitor")
                await state.broadcast({"type": "file_event", "path": str(path), "line": line})
                return


# ── Process monitor ────────────────────────────────────────────────────────────

async def watch_process(
    pid: int | None = None,
    name: str | None = None,
    label: str | None = None,
) -> str:
    """
    Watch a process by PID or name substring and notify when it exits.
    Returns immediately; monitoring runs in the background.
    """
    import psutil

    proc = _find_process(pid, name)
    display = label or f"'{proc.name()}' (PID {proc.pid})"

    async def _monitor():
        try:
            # Poll until the process is gone
            while True:
                try:
                    p = psutil.Process(proc.pid)
                    if not p.is_running() or p.status() == getattr(psutil, "STATUS_ZOMBIE", None):
                        break
                except psutil.NoSuchProcess:
                    break
                await asyncio.sleep(5)

            # Try to get exit code
            try:
                exit_code = psutil.Process(proc.pid).wait(timeout=1)
                status = f"exited with code {exit_code}"
            except Exception:
                status = "has finished"

            message = f"Process {display} {status}."
            logger.info(f"[monitor] {message}")
            await notify(message, title="Lhassa — Process Monitor")
            await state.broadcast({"type": "process_done", "pid": proc.pid, "label": display})

        except Exception as e:
            logger.error(f"[monitor] watch_process error: {e}")

    asyncio.create_task(_monitor(), name=f"watch-pid-{proc.pid}")
    return f"Watching {display} — I'll notify you when it finishes."


def _find_process(pid: int | None, name: str | None):
    import psutil

    if pid is not None:
        try:
            return psutil.Process(int(pid))
        except psutil.NoSuchProcess:
            raise RuntimeError(f"No process with PID {pid}.")

    if name is not None:
        matches = [
            p for p in psutil.process_iter(["pid", "name"])
            if name.lower() in (p.info["name"] or "").lower()
        ]
        if not matches:
            raise RuntimeError(f"No running process matching '{name}'.")
        if len(matches) > 1:
            details = []
            for proc in matches[:5]:
                try:
                    started = psutil.Process(proc.info["pid"]).create_time()
                except Exception:
                    started = 0.0
                details.append(f"PID {proc.info['pid']} ({proc.info['name']}, started {started:.0f})")
            joined = "; ".join(details)
            raise RuntimeError(
                f"Multiple running processes match '{name}'. Use a PID or a more specific name. Matches: {joined}"
            )
        return psutil.Process(matches[0].info["pid"])

    raise RuntimeError("Provide either pid or name.")


# ── File monitor ───────────────────────────────────────────────────────────────

async def watch_file(
    path: str,
    pattern: str | None = None,
    label: str | None = None,
) -> str:
    """
    Watch a file for new content (like `tail -f`).
    If pattern is given, notify when a line containing that pattern appears.
    If no pattern, notify when the file is modified (size increases).
    """
    p = Path(path).expanduser()
    display = label or p.name

    if not p.exists():
        return f"File not found: {path}"

    async def _monitor():
        try:
            if pattern is None:
                await _monitor_file_change(p, display)
            else:
                await _monitor_file_pattern(p, display, pattern)
        except Exception as e:
            logger.error(f"[monitor] watch_file error: {e}")

    asyncio.create_task(_monitor(), name=f"watch-file-{p.name}")
    if pattern:
        return f"Watching {display} for '{pattern}' — I'll notify you when it appears."
    return f"Watching {display} — I'll notify you when it changes."


# ── Command monitor ────────────────────────────────────────────────────────────

async def watch_command(
    command: str,
    interval: int = 30,
    until_output_contains: str | None = None,
    label: str | None = None,
) -> str:
    """
    Run a shell command every `interval` seconds until it succeeds or its
    output contains `until_output_contains`, then notify.
    """
    import subprocess
    display = label or command[:60]

    async def _monitor():
        try:
            for attempt in range(120):  # max 120 iterations (~1 hour at 30s)
                result = await asyncio.to_thread(
                    lambda: subprocess.run(
                        command, shell=True, capture_output=True, text=True, timeout=30
                    )
                )
                output = (result.stdout + result.stderr).strip()

                done = False
                if until_output_contains:
                    done = until_output_contains.lower() in output.lower()
                else:
                    done = result.returncode == 0

                if done:
                    msg = f"Command '{display}' completed. {output[:200]}" if output else f"Command '{display}' completed."
                    logger.info(f"[monitor] {msg}")
                    await notify(msg, title="Lhassa — Command Monitor")
                    await state.broadcast({"type": "command_done", "command": command, "output": output[:500]})
                    return

                await asyncio.sleep(interval)

            await notify(f"Command '{display}' monitor timed out after {120 * interval // 60} minutes.")
        except Exception as e:
            logger.error(f"[monitor] watch_command error: {e}")

    asyncio.create_task(_monitor(), name=f"watch-cmd-{id(command)}")
    if until_output_contains:
        return f"Monitoring '{display}' every {interval}s until output contains '{until_output_contains}'."
    return f"Monitoring '{display}' every {interval}s until it succeeds."
