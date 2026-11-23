# SPDX-License-Identifier: AGPL-3.0-or-later
"""File observer: inotify (through ``ctypes``, no dependency) on declared folders.

Only folders the user lists in the configuration file are watched; with none
declared the observer does nothing. Watching is bounded:

* a hard cap on the number of watches (``max_watches``, shared by all folders),
  so a huge tree can never exhaust the kernel's per-user inotify budget;
* recursion only when a folder asks for it, to at most ``max_depth`` levels,
  never through symlinks, skipping excluded names (``.git``, ``node_modules``...);
* a folder (or sub-folder) the never-keep policy covers is not watched at all.

``file_event`` carries the kind (create, modify, delete, move, overflow), the
path relative to the declared folder and whether it is a directory. A path the
never-keep policy covers keeps the event but not the path (``<withheld>``).
Rapid ``modify`` events of one file are coalesced (one per ``debounce`` seconds).
fanotify and eBPF are deliberately not used.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import select
import struct
import threading
import time
from typing import Callable, Iterator, Optional

from loguru import logger

from lhassa.observe.config import Folder
from lhassa.observe.writer import ObserveWriter
from lhassa.record import WITHHELD, withheld_rule

IN_MODIFY = 0x00000002
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_EXCL_UNLINK = 0x04000000
IN_ISDIR = 0x40000000
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000

WATCH_MASK = (
    IN_MODIFY | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF
    | IN_MOVE_SELF | IN_ONLYDIR | IN_DONT_FOLLOW | IN_EXCL_UNLINK
)
_EVENT = struct.Struct("=iIII")


def parse_events(buf: bytes) -> Iterator[tuple[int, int, int, str]]:
    """Yield ``(wd, mask, cookie, name)`` from a raw ``read()`` of an inotify fd."""
    off = 0
    while off + _EVENT.size <= len(buf):
        wd, mask, cookie, length = _EVENT.unpack_from(buf, off)
        off += _EVENT.size
        raw = buf[off : off + length]
        off += length
        yield wd, mask, cookie, raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


class Inotify:
    """A thin ``ctypes`` wrapper over the three libc calls."""

    def __init__(self) -> None:
        name = ctypes.util.find_library("c") or "libc.so.6"
        self._libc = ctypes.CDLL(name, use_errno=True)
        self._libc.inotify_init1.argtypes = [ctypes.c_int]
        self._libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self._libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        fd = self._libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        self.fd = fd

    def add(self, path: str, mask: int = WATCH_MASK) -> int:
        wd = self._libc.inotify_add_watch(self.fd, os.fsencode(path), mask)
        if wd < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), path)
        return wd

    def remove(self, wd: int) -> None:
        self._libc.inotify_rm_watch(self.fd, wd)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def inotify_available() -> bool:
    try:
        ino = Inotify()
    except (OSError, AttributeError):
        return False
    ino.close()
    return True


class _Dir:
    __slots__ = ("root", "rel")

    def __init__(self, root: "_Root", rel: str) -> None:
        self.root = root
        self.rel = rel


class _Root:
    __slots__ = ("label", "abs", "recursive", "max_depth")

    def __init__(self, label: str, abs_path: str, recursive: bool, max_depth: int) -> None:
        self.label = label
        self.abs = abs_path
        self.recursive = recursive
        self.max_depth = max_depth


class FilesObserver:
    name = "files"

    def __init__(
        self,
        writer: ObserveWriter,
        folders: list[Folder],
        *,
        max_watches: int = 256,
        exclude: tuple[str, ...] = (),
        debounce: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        on_state: Callable[[str, str], None] = lambda state, note: None,
    ) -> None:
        self.writer = writer
        self.folders = folders
        self.max_watches = max_watches
        self.exclude = frozenset(exclude)
        self.debounce = debounce
        self._clock = clock
        self._on_state = on_state
        self._ino: Optional[Inotify] = None
        self._wd: dict[int, _Dir] = {}
        self._recent: dict[str, float] = {}
        self.skipped: list[str] = []

    @property
    def watches(self) -> int:
        return len(self._wd)

    # -- watch management ---------------------------------------------------

    def _watch(self, root: _Root, abs_dir: str, rel: str) -> bool:
        assert self._ino is not None
        if len(self._wd) >= self.max_watches:
            return False
        if withheld_rule(abs_dir):
            self.skipped.append(f"{root.label}/{rel}".rstrip("/") + " (never-keep)")
            return True  # not an error, just not watched
        try:
            wd = self._ino.add(abs_dir)
        except OSError as exc:
            self.skipped.append(f"{root.label}/{rel}".rstrip("/") + f" ({exc.strerror})")
            return True
        self._wd[wd] = _Dir(root, rel)
        return True

    def _walk(self, root: _Root, abs_dir: str, rel: str, depth: int) -> bool:
        """Watch ``abs_dir`` and, when recursive, its sub-folders. False when out of budget."""
        if not self._watch(root, abs_dir, rel):
            return False
        if not root.recursive or depth >= root.max_depth:
            return True
        try:
            entries = sorted(os.scandir(abs_dir), key=lambda e: e.name)
        except OSError:
            return True
        for entry in entries:
            try:
                if entry.name in self.exclude or not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            sub_rel = f"{rel}/{entry.name}" if rel else entry.name
            if not self._walk(root, entry.path, sub_rel, depth + 1):
                return False
        return True

    def setup(self) -> None:
        for folder in self.folders:
            label = folder.path
            abs_path = os.path.realpath(os.path.expanduser(folder.path))
            if not os.path.isdir(abs_path):
                self.skipped.append(f"{label} (not a directory)")
                continue
            if withheld_rule(abs_path):
                self.skipped.append(f"{label} (never-keep)")
                continue
            root = _Root(label, abs_path, folder.recursive, folder.max_depth)
            if not self._walk(root, abs_path, "", 0):
                self.skipped.append(f"{label} (watch limit {self.max_watches} reached)")

    # -- events ----------------------------------------------------------------

    def _emit(self, kind: str, d: _Dir, name: str, is_dir: bool, *, src: Optional[tuple[_Dir, str]] = None) -> None:
        rel = f"{d.rel}/{name}" if d.rel and name else (name or d.rel)
        fields: dict[str, object] = {"type": kind, "root": d.root.label, "path": rel, "dir": is_dir}
        rule = withheld_rule(os.path.join(d.root.abs, rel))
        if src is not None:
            s_rel = f"{src[0].rel}/{src[1]}" if src[0].rel else src[1]
            fields["from"] = s_rel
            rule = rule or withheld_rule(os.path.join(src[0].root.abs, s_rel))
        if rule:
            fields["path"] = WITHHELD
            if "from" in fields:
                fields["from"] = WITHHELD
            fields["withheld"] = rule
        self.writer.emit("file_event", fields, checked=("path", "from", "root"))

    def _debounced(self, key: str) -> bool:
        now = self._clock()
        last = self._recent.get(key)
        if last is not None and now - last < self.debounce:
            return True
        if len(self._recent) > 2048:
            self._recent = {k: t for k, t in self._recent.items() if now - t < self.debounce}
        self._recent[key] = now
        return False

    def handle(self, events: list[tuple[int, int, int, str]]) -> None:
        moved_from: dict[int, tuple[_Dir, str, bool]] = {}
        for wd, mask, cookie, name in events:
            if mask & IN_Q_OVERFLOW:
                self.writer.emit("file_event", {"type": "overflow", "root": "", "path": "", "dir": False})
                continue
            d = self._wd.get(wd)
            if mask & IN_IGNORED:
                self._wd.pop(wd, None)
                continue
            if d is None:
                continue
            is_dir = bool(mask & IN_ISDIR)
            if mask & IN_MOVED_FROM:
                moved_from[cookie] = (d, name, is_dir)
            elif mask & IN_MOVED_TO:
                src = moved_from.pop(cookie, None)
                if src is not None:
                    self._emit("move", d, name, is_dir, src=(src[0], src[1]))
                else:
                    self._emit("create", d, name, is_dir)
                if is_dir and d.root.recursive:
                    self._grow(d, name)
            elif mask & IN_CREATE:
                self._emit("create", d, name, is_dir)
                if is_dir and d.root.recursive:
                    self._grow(d, name)
            elif mask & IN_DELETE:
                self._emit("delete", d, name, is_dir)
            elif mask & IN_MODIFY and name:
                if not self._debounced(f"{wd}/{name}"):
                    self._emit("modify", d, name, False)
        for d, name, is_dir in moved_from.values():  # moved out of the watched tree
            self._emit("delete", d, name, is_dir)

    def _grow(self, parent: _Dir, name: str) -> None:
        """A new sub-folder appeared in a recursive root: watch it if within limits."""
        if name in self.exclude:
            return
        depth = (parent.rel.count("/") + 1 if parent.rel else 0) + 1
        if depth > parent.root.max_depth:
            return
        rel = f"{parent.rel}/{name}" if parent.rel else name
        abs_dir = os.path.join(parent.root.abs, rel)
        if os.path.islink(abs_dir):
            return
        self._walk(parent.root, abs_dir, rel, depth)

    # -- the loop ------------------------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        if not self.folders:
            self._on_state("idle", "no folders declared; observing nothing")
            return
        try:
            self._ino = Inotify()
        except (OSError, AttributeError) as exc:
            self._on_state("inactive", f"inotify unavailable: {exc}")
            return
        try:
            self.setup()
            if not self._wd:
                self._on_state("idle", "no watchable folder: " + "; ".join(self.skipped)[:200])
                return
            note = f"{len(self._wd)} watches"
            if self.skipped:
                note += f", skipped {len(self.skipped)}"
            self._on_state("active", note)
            while not stop.is_set():
                ready, _, _ = select.select([self._ino.fd], [], [], 0.5)
                if not ready:
                    continue
                try:
                    data = os.read(self._ino.fd, 65536)
                except BlockingIOError:
                    continue
                self.handle(list(parse_events(data)))
        except OSError as exc:
            self._on_state("failed", f"{type(exc).__name__}: {exc}")
            logger.debug(f"[observe.files] {exc}")
        finally:
            self._ino.close()
            self._wd.clear()
