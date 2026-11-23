# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sway observer: window focus and workspace events over the i3/sway IPC.

The IPC is a Unix stream socket (``$SWAYSOCK``). Each message is the magic
``i3-ipc``, a 4 byte payload length, a 4 byte type (both native endian) and a
JSON payload. We subscribe to ``window`` and ``workspace`` events and write
``window_focus`` (app id or X11 class, workspace) and ``workspace_focus``.

Privacy: the window title is dropped by default (titles carry private data:
mail subjects, file names, URLs). With ``titles=True`` it is kept, scrubbed by
``lhassa.privacy`` and gated by never-keep like every other text.

No ``$SWAYSOCK`` means the observer is inactive, not failing. A lost connection
is retried with exponential backoff.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
from typing import Any, Callable, Optional

from loguru import logger

from lhassa.observe.writer import ObserveWriter

MAGIC = b"i3-ipc"
HEADER = struct.Struct("=6sII")
MSG_SUBSCRIBE = 2
MSG_GET_WORKSPACES = 1
EVENT_BIT = 0x80000000
EVENT_WORKSPACE = EVENT_BIT | 0
EVENT_WINDOW = EVENT_BIT | 3
MAX_PAYLOAD = 4 * 1024 * 1024
TITLE_MAX = 120


def pack(msg_type: int, payload: bytes = b"") -> bytes:
    return HEADER.pack(MAGIC, len(payload), msg_type) + payload


def parse_frames(buf: bytes) -> tuple[list[tuple[int, Any]], bytes]:
    """Split ``buf`` into ``[(type, decoded_json)]`` plus the unparsed rest.

    Raises ``ValueError`` on a bad magic or an absurd length (the stream is
    corrupt and the connection must be dropped).
    """
    out: list[tuple[int, Any]] = []
    while len(buf) >= HEADER.size:
        magic, length, mtype = HEADER.unpack_from(buf)
        if magic != MAGIC or length > MAX_PAYLOAD:
            raise ValueError("bad i3-ipc frame")
        if len(buf) < HEADER.size + length:
            break
        raw = buf[HEADER.size : HEADER.size + length]
        buf = buf[HEADER.size + length :]
        try:
            out.append((mtype, json.loads(raw)))
        except ValueError:
            out.append((mtype, None))
    return out, buf


class SwayState:
    """Maps IPC events to history events; pure apart from its own memory."""

    def __init__(self, *, titles: bool = False) -> None:
        self.titles = titles
        self.workspace = ""
        self._last: Optional[tuple[str, str, str]] = None

    def seed(self, workspaces: Any) -> None:
        """Take the focused workspace out of a GET_WORKSPACES reply."""
        if isinstance(workspaces, list):
            for ws in workspaces:
                if isinstance(ws, dict) and ws.get("focused"):
                    self.workspace = str(ws.get("name", ""))

    def handle(self, mtype: int, payload: Any) -> Optional[tuple[str, dict[str, Any]]]:
        """``(kind, fields)`` to record, or ``None``."""
        if not isinstance(payload, dict):
            return None
        change = payload.get("change")
        if mtype == EVENT_WORKSPACE and change == "focus":
            cur = payload.get("current")
            name = str(cur.get("name", "")) if isinstance(cur, dict) else ""
            if not name or name == self.workspace:
                return None
            self.workspace = name
            return "workspace_focus", {"workspace": name}
        if mtype == EVENT_WINDOW and change == "focus":
            con = payload.get("container")
            if not isinstance(con, dict):
                return None
            props = con.get("window_properties")
            klass = str(props.get("class", "")) if isinstance(props, dict) else ""
            app = str(con.get("app_id") or "")
            fields: dict[str, Any] = {"workspace": self.workspace}
            if app:
                fields["app_id"] = app
            if klass:
                fields["class"] = klass
            if not app and not klass:
                return None
            title = str(con.get("name") or "")
            if self.titles and title:
                from lhassa.privacy import pseudonymize

                fields["title"] = pseudonymize(title)[0][:TITLE_MAX]
            key = (app, klass, self.workspace)
            if key == self._last and not self.titles:
                return None
            self._last = key
            return "window_focus", fields
        return None


def _recv_frames(sock: socket.socket, buf: bytes) -> tuple[list[tuple[int, Any]], bytes]:
    data = sock.recv(65536)
    if not data:
        raise ConnectionError("sway closed the socket")
    return parse_frames(buf + data)


class SwayObserver:
    name = "sway"

    def __init__(
        self,
        writer: ObserveWriter,
        *,
        titles: bool = False,
        socket_path: Optional[str] = None,
        backoff: tuple[float, float] = (1.0, 60.0),
        on_state: Callable[[str, str], None] = lambda state, note: None,
    ) -> None:
        self.writer = writer
        self.state = SwayState(titles=titles)
        self.path = socket_path if socket_path is not None else os.environ.get("SWAYSOCK", "")
        self.backoff = backoff
        self._on_state = on_state

    def _session(self, stop: threading.Event) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(5.0)
            sock.connect(self.path)
            sock.sendall(pack(MSG_GET_WORKSPACES))
            buf = b""
            frames: list[tuple[int, Any]] = []
            while not frames:
                frames, buf = _recv_frames(sock, buf)
            self.state.seed(frames[0][1])
            sock.sendall(pack(MSG_SUBSCRIBE, b'["window","workspace"]'))
            self._on_state("active", "subscribed to window, workspace")
            sock.settimeout(1.0)
            pending = frames[1:]
            while not stop.is_set():
                for mtype, payload in pending:
                    hit = self.state.handle(mtype, payload)
                    if hit:
                        self.writer.emit(hit[0], hit[1])
                try:
                    pending, buf = _recv_frames(sock, buf)
                except socket.timeout:
                    pending = []
        finally:
            sock.close()

    def run(self, stop: threading.Event) -> None:
        if not self.path:
            self._on_state("inactive", "no SWAYSOCK in the environment")
            return
        delay = self.backoff[0]
        while not stop.is_set():
            try:
                self._session(stop)
                delay = self.backoff[0]
            except (OSError, ConnectionError, ValueError) as exc:
                self._on_state("retrying", f"{type(exc).__name__}, next try in {delay:g}s")
                logger.debug(f"[observe.sway] {exc}")
                stop.wait(delay)
                delay = min(self.backoff[1], delay * 2)
