# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Cuarentena; wall #1 of the three walls (§2.6) and gate #1 "Segura" of the
self-improvement 2b pipeline (§2.8).

This is a LOCAL sandbox for running self-written *candidate* code under the rule
that it gets **no network and, where the tool allows it, no access to real
data**. The brain may write a tool, but before that tool earns any trust it
runs here first: a fresh temporary directory as its cwd, a scrubbed environment
(so it cannot read a single secret/token from the process env), and; where the
chosen tool offers it; a real filesystem jail and a network namespace with no
interfaces (so it cannot phone home or read the real disk).

Isolation, strongest **filesystem** guarantee first (detected at runtime via
:func:`shutil.which`, each probed before being trusted):

  * ``bwrap --unshare-net``: a bubblewrap sandbox that only bind-mounts
    ``/usr``/``/etc``/the workdir; the real ``$HOME`` (and everything else,
    including ``~/.lhassa/keys/ed25519`` and any ``.env``) is simply not present
    inside the mount namespace. **Preferred**: real FS isolation, not just env
    scrubbing.
  * ``firejail --net=none --private=<workdir>``: ``--private`` rebinds the
    candidate's ``$HOME`` to the empty workdir, so it likewise cannot read the
    real one. Second choice.
  * ``unshare -rnm``: a rootless user + network + **mount** namespace. This
    is deliberately last, and reported as ``"env+net only"`` rather than a
    filesystem-isolating level: a bare mount namespace changes nothing about
    *which* files are visible (there is no pivot/chroot here, on purpose; a
    hand-rolled bind-mount jail over guessed "sensitive" paths would be
    fragile and could silently miss one), so a candidate that knows an
    absolute path can still read ``~/.lhassa/keys/ed25519`` or a project
    ``.env`` through it. The ``-m`` only stops the candidate's *own* mount
    operations from leaking back to the host; real, but narrow, containment.
    Prefer bwrap/firejail whenever either is present; this is what remains
    when neither is.

If none of those tools exist, Linux now behaves like every other platform:
**refuse to run rather than degrade silently** (``LHASSA_QUARANTINE_ALLOW_UNSANDBOXED=1``
or the pre-existing ``LHASSA_QUARANTINE_ISOLATION=none`` opt out of the refusal
explicitly, for a host whose operator has consciously accepted the risk). If one
of those escape hatches is set, the run proceeds as a plain subprocess with the
scrubbed env, the temp cwd, and a hard timeout; but ``[SUPUESTO]``: in that mode
the network block is **best-effort only**, there is no namespace, so a determined
candidate could still open a socket or read the real filesystem; only the
env-scrub + timeout still hold. ``QuarantineResult.isolation`` always reports
which level was actually achieved (``"bwrap"``/``"firejail"`` = FS-isolated,
``"env+net only"``/``"none"`` = not) so a caller/gate can refuse to graduate
anything that didn't get real filesystem isolation.

``[SUPUESTO]``: a stronger future sandbox is Anthropic's Managed Agent Harness
(§2.8); when wired in it would slot under :func:`run_in_quarantine` as another,
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

from lhassa.host import current_platform_name, get_platform

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
    out or escaping. ``isolation`` names the level actually achieved; one of
    ``"bwrap"``/``"firejail"`` (real filesystem isolation + no network),
    ``"env+net only"`` (``unshare -rnm``: network + user + mount namespace, but
    the real filesystem is still readable by absolute path; no FS isolation),
    or ``"none"`` (no namespace at all; only reached via an explicit opt-out, see
    ``run_in_quarantine``). A caller/gate that needs a real data guarantee should
    check for ``"bwrap"``/``"firejail"`` specifically, not merely ``!= "none"``.
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
    """Return ``(prefix_argv, level)`` for the strongest available sandbox tool.

    Preference order is FS-isolation first: ``bwrap`` and ``firejail`` both
    actually hide the real filesystem from the candidate; ``unshare -rn`` alone
    does not (it only namespaces network + user ids), so it is tried last and
    reported under the honest label ``"env+net only"`` rather than claiming a
    guarantee it cannot back up.
    """
    forced = (os.getenv("LHASSA_QUARANTINE_ISOLATION") or "").strip().lower()
    if forced in {"none", "off", "0"}:
        return ([], "none")
    if shutil.which("bwrap"):
        prefix = [
            "bwrap",
            "--unshare-net",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/etc", "/etc",
            *_python_runtime_binds(),
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
    if shutil.which("firejail"):
        prefix = ["firejail", "--quiet", "--net=none", f"--private={workdir}", "--"]
        if _sandbox_works(prefix, workdir):
            return (prefix, "firejail")
    if shutil.which("unshare"):
        # -r: map the caller to root inside a new user namespace (no real privilege).
        # -n: a fresh network namespace with no configured interfaces → no route out.
        # -m: a fresh mount namespace; contains the candidate's OWN mount/unmount
        #     calls so they cannot leak back to the host. It does NOT hide any
        #     existing file: with no pivot/chroot, the real filesystem (including
        #     ~/.lhassa/keys/ed25519 and any project .env) is still visible by
        #     absolute path. That is why this level is reported as "env+net
        #     only", never as a filesystem-isolating one; see the module
        #     docstring for why a hand-rolled bind-mount jail here would be
        #     fragile rather than robust.
        prefix = ["unshare", "-rnm", "--"]
        if _sandbox_works(prefix, workdir):
            return (prefix, "env+net only")
    # Non-Linux hosts: ask the platform layer for its own sandbox (macOS
    # ``sandbox-exec``). Deliberately consulted ONLY off Linux, so the tier-1
    # path above stays byte-identical; on Linux the three tools already ARE
    # the whole chain, and re-probing them through the layer would change
    # nothing but the cost.
    if current_platform_name() != "linux":
        plat = get_platform()
        if plat is not None:
            found = plat.sandbox_prefix(str(workdir))
            if found is not None:
                return (list(found[0]), found[1])
    return ([], "none")


def _python_runtime_binds() -> list[str]:
    """Extra ``--ro-bind`` args so a candidate that shells out to *this* Python
    interpreter (e.g. PTC's ``[sys.executable, script]``) can still find it
    under bwrap.

    A ``uv``/venv install commonly lives OUTSIDE ``/usr`` (e.g.
    ``~/.venv``, or a uv-managed toolchain under ``~/.local/share/uv``), so the
    plain ``/usr``+``/etc`` bind above hides it. Binding read-only, deduped,
    and only when the path actually exists keeps this narrow: it does not
    widen what a candidate can read to anything secret-shaped (a Python
    install is not "real data" in the sense this module protects), it just
    keeps the interpreter itself runnable.
    """
    import sys

    binds: list[str] = []
    seen: set[str] = set()
    for raw in (sys.prefix, sys.base_prefix):
        try:
            real = os.path.realpath(raw)
        except OSError:
            continue
        if real in seen or real in ("/usr", "/etc", "/"):
            continue
        seen.add(real)
        if os.path.isdir(real):
            binds += ["--ro-bind", real, real]
    return binds


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
        # An unsandboxed run is REFUSED by default, on EVERY platform; no
        # namespacing tool means no filesystem isolation and no network
        # isolation, which would quietly turn "authored code never executes
        # outside quarantine" into a run of model-written code directly on the
        # owner's machine. This used to warn-and-proceed on Linux specifically
        # (treating "no sandbox tool" as a rare misconfiguration rather than
        # the same risk it is everywhere else); that inconsistency is the bug
        #; Linux now refuses too, unless one of the two explicit escape
        # hatches below is set.
        forced_off = (
            os.getenv("LHASSA_QUARANTINE_ISOLATION") or ""
        ).strip().lower() in {"none", "off", "0"}
        # Two distinct, deliberate operator opt-outs, both honoured on every
        # platform (never auto-applied):
        #   * LHASSA_QUARANTINE_ISOLATION=none; "don't even try a sandbox
        #     tool" (also short-circuits _select_isolation itself, above).
        #   * LHASSA_QUARANTINE_ALLOW_UNSANDBOXED=1; "no sandbox tool was
        #     found; run anyway." The new, narrower hatch this refusal needs:
        #     a host that consciously accepts running self-written candidates
        #     with only env-scrub + temp-dir + timeout containment.
        allow_unsandboxed = (
            os.getenv("LHASSA_QUARANTINE_ALLOW_UNSANDBOXED") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not forced_off and not allow_unsandboxed:
            if owns_dir:
                shutil.rmtree(base, ignore_errors=True)
            return QuarantineResult(
                ok=False,
                stdout="",
                stderr=(
                    "quarantine: refusing to run unsandboxed on platform "
                    f"{current_platform_name()!r}; no sandbox tool available "
                    "(install unshare/bwrap/firejail, or set "
                    "LHASSA_QUARANTINE_ALLOW_UNSANDBOXED=1 to accept an "
                    "unsandboxed run)"
                ),
                exit_code=None,
                timed_out=False,
                escaped=False,
                isolation="none",
            )
        logger.warning(
            "quarantine: no namespacing tool (unshare/firejail/bwrap) and no "
            "platform sandbox; proceeding UNSANDBOXED because {} was set; "
            "network AND filesystem containment are best-effort only "
            "[SUPUESTO]",
            "LHASSA_QUARANTINE_ISOLATION=none" if forced_off
            else "LHASSA_QUARANTINE_ALLOW_UNSANDBOXED=1",
        )

    try:
        stdout, stderr, exit_code, timed_out = _run_process(cmd, base, env, timeout)
    except (OSError, ValueError) as exc:
        # Wrapper or interpreter could not be launched at all; caged failure.
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

    # ``escaped`` is reserved for a detectable break-out of the sandbox. This is a
    # NARROW heuristic, not a proof: it only catches the case where the namespace
    # tool itself refused to set up isolation and said so on stderr in its own
    # diagnostic format (``unshare:`` / ``bwrap:`` / ``firejail:``). It cannot
    # detect a candidate that successfully escapes the namespace *after* setup
    # (e.g. a kernel exploit) or one that simply reads real data through a level
    # that never hid it in the first place (see ``"env+net only"`` above); that
    # class of gap is surfaced via ``isolation``, not ``escaped``, so a caller
    # that cares about real filesystem containment must check the level, not
    # just this flag.
    wrapper_failed = isolation != "none" and any(
        line.startswith(("unshare:", "bwrap:", "firejail:"))
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
