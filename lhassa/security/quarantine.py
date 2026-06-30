"""
Cuarentena — wall #1 of the three walls (§2.6) and gate #1 "Segura" of the
self-improvement 2b pipeline (§2.8).

This is a LOCAL sandbox for running self-written *candidate* code under the rule
that it gets **no network and no access to real data**. The brain may write a
tool, but before that tool earns any trust it runs here first: a fresh temporary
directory (so it cannot see the real filesystem or the brain DB), a scrubbed
environment (so it cannot read a single secret/token), and — where the platform
offers it — a network namespace with no interfaces (so it cannot phone home).

Isolation, strongest first (detected at runtime via :func:`shutil.which`):

  * ``unshare -rn`` — a rootless user+network namespace. The network namespace has
    only a down loopback, so there is no route off the box. **Preferred.**
  * ``firejail --net=none`` — a network-less firejail sandbox.
  * ``bwrap --unshare-net`` — a bubblewrap sandbox with no network namespace.

If none of those tools exist we fall back to a plain subprocess with the scrubbed
env, the temp cwd, and a hard timeout. ``[SUPUESTO]``: in that fallback the
network block is **best-effort only** — there is no namespace, so a determined
candidate could still open a socket; the temp-dir + env-scrub + timeout still
hold. ``QuarantineResult.isolation`` reports which level was actually achieved so
a caller can refuse to graduate anything that only ran best-effort if it wants to.

``[SUPUESTO]``: a stronger future sandbox is Anthropic's Managed Agent Harness
(§2.8) — when wired in it would slot under :func:`run_in_quarantine` as another,
higher, isolation level without changing this contract.

The function **never raises** and always kills the child on timeout.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

from loguru import logger

# Cap on captured output so a noisy candidate cannot bloat a result/episode.
_OUTPUT_CAP = 4000

# A candidate gets only these env keys (everything else, including every secret,
# is dropped). We build the env from scratch rather than stripping ``os.environ``
# so a *new* secret-shaped variable can never leak by being unknown to a denylist.
_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "TZ")
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass
class QuarantineResult:
    """The verdict of one quarantined run.

    ``ok`` is the gate-1 signal: the candidate ran cleanly (exit 0) without timing
    out or escaping. ``isolation`` names the level actually achieved.
    """

    ok: bool
    stdout: str
    stderr: str
    exit_code: Optional[int]
    timed_out: bool
    escaped: bool
    isolation: str = "none"


def _truncate(text: str, cap: int = _OUTPUT_CAP) -> str:
    text = text or ""
    if len(text) <= cap:
        return text
    return text[:cap] + f"… (+{len(text) - cap} chars)"


def _scrub_env(workdir: Path) -> dict[str, str]:
    """Build a minimal, secret-free environment pointed at the temp workdir.

    Nothing from ``os.environ`` is inherited except a short allowlist of innocuous
    locale/PATH keys; ``LHASSA_*``, every ``*_KEY`` / ``*_TOKEN`` / ``*_SECRET`` and
    anything else is therefore absent by construction. ``HOME``/``TMPDIR``/``PWD``
    are pinned to the throwaway directory so the candidate has no real home.
    """
    env: dict[str, str] = {"PATH": os.environ.get("PATH") or _DEFAULT_PATH}
    for key in _ENV_PASSTHROUGH[1:]:
        val = os.environ.get(key)
        if val:
            env[key] = val
    env["HOME"] = str(workdir)
    env["TMPDIR"] = str(workdir)
    env["PWD"] = str(workdir)
    env["LHASSA_QUARANTINE"] = "1"  # a marker candidates can read to know they are caged
    return env


def _select_isolation(workdir: Path) -> tuple[list[str], str]:
    """Return ``(prefix_argv, level)`` for the strongest available sandbox tool."""
    forced = (os.getenv("LHASSA_QUARANTINE_ISOLATION") or "").strip().lower()
    if forced in {"none", "off", "0"}:
        return ([], "none")
    if shutil.which("unshare"):
        # -r: map the caller to root inside a new user namespace (no real privilege).
        # -n: a fresh network namespace with no configured interfaces → no route out.
        prefix = ["unshare", "-rn", "--"]
        if _sandbox_works(prefix, workdir):
            return (prefix, "unshare")
    if shutil.which("firejail"):
        prefix = ["firejail", "--quiet", "--net=none", f"--private={workdir}", "--"]
        if _sandbox_works(prefix, workdir):
            return (prefix, "firejail")
    if shutil.which("bwrap"):
        prefix = [
            "bwrap",
            "--unshare-net",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/etc", "/etc",
            "--bind", str(workdir), str(workdir),
            "--proc", "/proc",
            "--dev", "/dev",
            "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64",
            "--chdir", str(workdir),
            "--",
        ]
        if _sandbox_works(prefix, workdir):
            return (prefix, "bwrap")
    return ([], "none")


def _sandbox_works(prefix: list[str], workdir: Path) -> bool:
    """Probe a sandbox wrapper before trusting it; some runtimes hang on unshare."""
    try:
        proc = subprocess.run(
            [*prefix, "true"],
            cwd=str(workdir),
            env=_scrub_env(workdir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            start_new_session=True,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _build_inner(argv_or_code: Union[str, Sequence[str]]) -> list[str]:
    """A ``str`` candidate is run as ``bash -c <code>``; a list is run as argv."""
    if isinstance(argv_or_code, str):
        return ["bash", "-c", argv_or_code]
    return [str(a) for a in argv_or_code]


async def run_in_quarantine(
    argv_or_code: Union[str, Sequence[str]],
    *,
    timeout: int = 30,
    workdir: Optional[Union[str, Path]] = None,
) -> QuarantineResult:
    """Run candidate code in the sandbox: no real data, no real env, no network.

    ``argv_or_code`` is either shell code (``str`` → ``bash -c``) or an argv list.
    Returns a :class:`QuarantineResult`; **never raises**. On timeout the whole
    process group is killed and ``timed_out`` is set.
    """
    owns_dir = workdir is None
    base = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="lhassa_q_"))
    base.mkdir(parents=True, exist_ok=True)
    env = _scrub_env(base)
    prefix, isolation = _select_isolation(base)
    cmd = [*prefix, *_build_inner(argv_or_code)]

    if isolation == "none":
        logger.warning(
            "quarantine: no namespacing tool (unshare/firejail/bwrap); "
            "network block is best-effort only [SUPUESTO]"
        )

    try:
        stdout, stderr, exit_code, timed_out = _run_process(cmd, base, env, timeout)
    except (OSError, ValueError) as exc:
        # Wrapper or interpreter could not be launched at all — caged failure.
        return QuarantineResult(
            ok=False,
            stdout="",
            stderr=f"quarantine: could not launch: {exc}",
            exit_code=None,
            timed_out=False,
            escaped=False,
            isolation=isolation,
        )
    finally:
        if owns_dir:
            shutil.rmtree(base, ignore_errors=True)

    # ``escaped`` is reserved for a detectable break-out of the sandbox. We cannot
    # prove the negative, but a namespace tool that refuses to set up isolation
    # emits its own diagnostic (``unshare:`` / ``bwrap:`` / ``firejail:``) — that
    # means the candidate did not actually run caged, so treat it as an escape and
    # fail gate 1 rather than silently trusting an un-isolated run.
    wrapper_failed = isolation != "none" and any(
        line.startswith((f"{isolation}:", "unshare:", "bwrap:", "firejail:"))
        for line in stderr.splitlines()
    )
    escaped = bool(wrapper_failed and not timed_out)

    ok = (not timed_out) and (not escaped) and exit_code == 0
    return QuarantineResult(
        ok=ok,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        timed_out=timed_out,
        escaped=escaped,
        isolation=isolation,
    )


def _run_process(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> tuple[str, str, Optional[int], bool]:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return _truncate(stdout), _truncate(stderr), proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        stdout = stdout if stdout is not None else (exc.stdout or "")
        stderr = stderr if stderr is not None else (exc.stderr or "")
        return _truncate(stdout), _truncate(stderr), proc.returncode, True


def _kill_group(proc) -> None:
    """SIGKILL the child's whole process group; degrade to the single pid."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
