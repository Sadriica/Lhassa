# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shell observer: receives one metadata record per command from the shell hook.

Transport. A bash or zsh hook cannot start Python per command, and bash cannot
speak ``AF_UNIX`` datagrams with builtins alone. So the receiver listens on two
private doors that carry the same one-line records:

* a **FIFO** (``shell.fifo``, ``0600``): the hook opens it once read-write
  (``exec {fd}<>fifo``) and writes each record with the ``printf`` builtin. A
  line shorter than ``PIPE_BUF`` is written atomically, so a record behaves as a
  datagram. No fork, no exec per command;
* a **datagram socket** (``shell.sock``, ``0600``) for any client that can speak
  ``AF_UNIX`` (``socat``, ``nc -U -u``, Python).

Both live in a ``0700`` directory owned by the user; a directory that is not
private is refused.

Record (v1), fields joined by ``0x1f`` and ended by a newline::

    L1 <shell> <cwd> <exit> <duration_ms> <first-token> <full-line>

``<full-line>`` is empty unless the user opted in at BOTH ends (the hook variable
``LHASSA_OBSERVE_FULL=1`` and ``shell.full_command`` in the configuration). When
kept it is scrubbed by ``lhassa.privacy`` and withheld if any token in it touches
a never-keep path.
"""

from __future__ import annotations

import os
import re
import selectors
import socket
import stat
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from lhassa.observe.writer import ObserveWriter
from lhassa.record import WITHHELD, withheld_rule_in_text

SEP = "\x1f"
VERSION = "L1"
SHELLS = frozenset({"bash", "zsh"})
MAX_RECORD = 4000  # below PIPE_BUF (4096) so a FIFO write stays atomic
CWD_MAX = 512
FULL_MAX = 500
_TOKEN_OK = re.compile(r"^[A-Za-z0-9_.+@%:,-]{1,64}$")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def runtime_dir(configured: str = "", env: Optional[dict[str, str]] = None, uid: Optional[int] = None) -> Path:
    """The directory of the FIFO and the socket. Mirrors the hook's own logic."""
    env = os.environ if env is None else env
    if configured:
        return Path(configured).expanduser()
    xdg = env.get("XDG_RUNTIME_DIR", "")
    if xdg:
        return Path(xdg) / "lhassa-observe"
    return Path(f"/tmp/lhassa-observe-{os.getuid() if uid is None else uid}")


def clean_token(token: str) -> str:
    """The first word of a command line reduced to a safe, small label.

    Paths keep only their base name (and a never-keep path becomes ``<withheld>``);
    an assignment (``TOKEN=...``) is never kept; anything with unusual characters
    becomes ``<other>``.
    """
    token = token.strip()
    if not token:
        return ""
    if _ASSIGN.match(token):
        return "<assign>"
    if "/" in token:
        if withheld_rule_in_text(token):
            return WITHHELD
        token = token.rstrip("/").rsplit("/", 1)[-1]
    return token if _TOKEN_OK.match(token) else "<other>"


def parse_record(raw: bytes, *, full_command: bool = False) -> Optional[tuple[str, dict[str, Any], tuple[str, ...]]]:
    """Decode one record into ``(kind, fields, path_fields)`` or ``None`` if malformed."""
    try:
        text = raw.decode("utf-8", errors="strict").rstrip("\n")
    except UnicodeDecodeError:
        return None
    if len(text) > MAX_RECORD:
        return None
    parts = text.split(SEP)
    if len(parts) != 7 or parts[0] != VERSION:
        return None
    _, shell, cwd, rc, dur, tok, full = parts
    if shell not in SHELLS or not cwd.startswith("/"):
        return None
    try:
        rc_i, dur_i = int(rc), int(dur)
    except ValueError:
        return None
    fields: dict[str, Any] = {
        "shell": shell,
        "cwd": cwd[:CWD_MAX],
        "rc": max(-1, min(rc_i, 255)),
        "dur_ms": max(0, dur_i),
        "cmd": clean_token(tok),
    }
    if full_command and full.strip():
        rule = withheld_rule_in_text(full)
        if rule:
            fields["line"] = WITHHELD
        else:
            from lhassa.privacy import pseudonymize

            fields["line"] = pseudonymize(full.strip())[0][:FULL_MAX]
    return "shell_cmd", fields, ("cwd",)


def prepare_dir(path: Path) -> Optional[str]:
    """Create or verify the private directory; a reason string means refuse."""
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        st = os.lstat(path)
    except OSError as exc:
        return f"cannot create {path}: {exc.strerror}"
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return f"{path} is not a directory owned by this user"
    if st.st_mode & 0o077:
        return f"{path} is not private (mode {st.st_mode & 0o777:o})"
    return None


class ShellObserver:
    name = "shell"

    def __init__(
        self,
        writer: ObserveWriter,
        *,
        directory: Path,
        full_command: bool = False,
        on_state: Callable[[str, str], None] = lambda state, note: None,
    ) -> None:
        self.writer = writer
        self.dir = Path(directory)
        self.full_command = full_command
        self._on_state = on_state
        self.fifo_path = self.dir / "shell.fifo"
        self.sock_path = self.dir / "shell.sock"
        self.pid_path = self.dir / "shell.pid"
        self.rejected = 0

    # -- setup / teardown -------------------------------------------------

    def _open_fifo(self) -> int:
        try:
            st = os.lstat(self.fifo_path)
            if not stat.S_ISFIFO(st.st_mode):
                raise OSError("shell.fifo exists and is not a FIFO")
        except FileNotFoundError:
            os.mkfifo(self.fifo_path, 0o600)
        os.chmod(self.fifo_path, 0o600)
        return os.open(self.fifo_path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)

    def _open_socket(self) -> socket.socket:
        try:
            if stat.S_ISSOCK(os.lstat(self.sock_path).st_mode):
                os.unlink(self.sock_path)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        old = os.umask(0o177)
        try:
            sock.bind(str(self.sock_path))
        finally:
            os.umask(old)
        os.chmod(self.sock_path, 0o600)
        sock.setblocking(False)
        return sock

    def _cleanup(self) -> None:
        for p in (self.pid_path, self.sock_path, self.fifo_path):
            try:
                p.unlink()
            except OSError:
                pass

    # -- the loop -----------------------------------------------------------

    def _handle(self, raw: bytes) -> None:
        parsed = parse_record(raw, full_command=self.full_command)
        if parsed is None:
            self.rejected += 1
            return
        kind, fields, paths = parsed
        self.writer.emit(kind, fields, paths=paths)

    def run(self, stop: threading.Event) -> None:
        reason = prepare_dir(self.dir)
        if reason:
            self._on_state("inactive", reason)
            return
        fifo_fd = -1
        sock: Optional[socket.socket] = None
        sel = selectors.DefaultSelector()
        try:
            fifo_fd = self._open_fifo()
            sock = self._open_socket()
            self.pid_path.write_text(f"{os.getpid()}\n")
            os.chmod(self.pid_path, 0o600)
            sel.register(fifo_fd, selectors.EVENT_READ, "fifo")
            sel.register(sock, selectors.EVENT_READ, "sock")
            self._on_state("active", f"listening in {self.dir}")
            buf = b""
            while not stop.is_set():
                for key, _ in sel.select(timeout=0.5):
                    if key.data == "fifo":
                        try:
                            chunk = os.read(fifo_fd, 65536)
                        except BlockingIOError:
                            continue
                        buf += chunk
                        *lines, buf = buf.split(b"\n")
                        for line in lines:
                            self._handle(line)
                        if len(buf) > MAX_RECORD * 4:
                            buf = b""  # runaway writer without newlines
                            self.rejected += 1
                    else:
                        try:
                            data = sock.recv(8192)
                        except BlockingIOError:
                            continue
                        self._handle(data)
        except OSError as exc:
            self._on_state("failed", f"{type(exc).__name__}: {exc.strerror or exc}")
            logger.debug(f"[observe.shell] {exc}")
        finally:
            sel.close()
            if sock is not None:
                sock.close()
            if fifo_fd >= 0:
                os.close(fifo_fd)
            self._cleanup()
