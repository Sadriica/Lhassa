"""File observer: real inotify on a tmp directory (skipped when the kernel refuses)."""

import os
import struct
import threading
import time

import pytest

from lhassa.observe.config import Folder
from lhassa.observe.files import (
    IN_CREATE, IN_ISDIR, IN_MOVED_FROM, FilesObserver, inotify_available, parse_events,
)
from lhassa.observe.writer import ObserveWriter
from lhassa.record import read_log

needs_inotify = pytest.mark.skipif(not inotify_available(), reason="inotify not available here")


def _pack(wd, mask, cookie, name):
    raw = name.encode() + b"\0"
    raw += b"\0" * (-len(raw) % 4)
    return struct.pack("=iIII", wd, mask, cookie, len(raw)) + raw


def test_parse_events_decodes_a_synthetic_buffer():
    buf = _pack(1, IN_CREATE, 0, "a.txt") + _pack(2, IN_MOVED_FROM | IN_ISDIR, 9, "d")
    assert list(parse_events(buf)) == [(1, IN_CREATE, 0, "a.txt"), (2, IN_MOVED_FROM | IN_ISDIR, 9, "d")]
    assert list(parse_events(b"\x01\x02")) == []


def _run(tmp_path, folders, **kw):
    w = ObserveWriter("files", tmp_path / "rec", "inst")
    states = []
    obs = FilesObserver(w, folders, on_state=lambda *a: states.append(a), **kw)
    stop = threading.Event()
    t = threading.Thread(target=obs.run, args=(stop,))
    t.start()
    for _ in range(300):
        if states:
            break
        time.sleep(0.01)
    return obs, w, stop, t, states


def _finish(w, stop, t, tmp_path):
    stop.set()
    t.join(3)
    w.close()
    logs = list((tmp_path / "rec").rglob("*.jsonl"))
    return read_log(logs[0])[0] if logs else []


def _wait(w, n):
    for _ in range(300):
        if w.events >= n:
            return
        time.sleep(0.01)


def test_no_declared_folders_observes_nothing(tmp_path):
    obs, w, stop, t, states = _run(tmp_path, [])
    t.join(2)
    assert states[0][0] == "idle" and obs.watches == 0 and w.events == 0
    assert not (tmp_path / "rec").exists()


@needs_inotify
def test_create_modify_delete_move_with_relative_paths(tmp_path):
    root = tmp_path / "watched"
    root.mkdir()
    obs, w, stop, t, states = _run(tmp_path, [Folder(str(root))])
    assert states[0][0] == "active"
    (root / "a.txt").write_text("x")
    (root / "a.txt").write_text("xy")  # coalesced with the first modify
    os.rename(root / "a.txt", root / "b.txt")
    os.unlink(root / "b.txt")
    _wait(w, 4)
    evs = _finish(w, stop, t, tmp_path)
    kinds = [(e["type"], e["path"]) for e in evs]
    assert ("create", "a.txt") in kinds
    assert kinds.count(("modify", "a.txt")) == 1
    move = next(e for e in evs if e["type"] == "move")
    assert move["path"] == "b.txt" and move["from"] == "a.txt"
    assert ("delete", "b.txt") in kinds
    assert all(str(root) not in str(e["path"]) for e in evs)


@needs_inotify
def test_never_keep_paths_are_withheld_but_the_event_is_kept(tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    obs, w, stop, t, _ = _run(tmp_path, [Folder(str(root))])
    (root / ".env").write_text("TOKEN=1")
    (root / "vault.kdbx").write_text("x")
    _wait(w, 2)
    evs = _finish(w, stop, t, tmp_path)
    creates = [e for e in evs if e["type"] == "create"]
    assert len(creates) == 2
    assert all(e["path"] == "<withheld>" and e["withheld"] for e in creates)
    raw = open(next((tmp_path / "rec").rglob("*.jsonl"))).read()
    assert "vault" not in raw and '"path":".env"' not in raw


@needs_inotify
def test_declaring_a_never_keep_folder_watches_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    obs, w, stop, t, states = _run(tmp_path, [Folder("~/.ssh")])
    t.join(2)
    assert states[0][0] == "idle" and obs.watches == 0
    assert any("never-keep" in s for s in obs.skipped)


@needs_inotify
def test_recursion_respects_depth_excludes_symlinks_and_cap(tmp_path):
    root = tmp_path / "r"
    (root / "a/b/c").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "link").symlink_to(tmp_path)
    w = ObserveWriter("files", tmp_path / "rec", "i")
    obs = FilesObserver(w, [Folder(str(root), recursive=True, max_depth=2)], max_watches=100, exclude=(".git",))
    from lhassa.observe.files import Inotify

    obs._ino = Inotify()
    obs.setup()
    rels = sorted(d.rel for d in obs._wd.values())
    obs._ino.close()
    assert rels == ["", "a", "a/b"]  # not c (depth), not .git, not link
    capped = FilesObserver(w, [Folder(str(root), recursive=True, max_depth=4)], max_watches=2)
    capped._ino = Inotify()
    capped.setup()
    n, skipped = capped.watches, capped.skipped
    capped._ino.close()
    assert n == 2 and any("watch limit" in s for s in skipped)


@needs_inotify
def test_new_subfolder_gets_a_watch(tmp_path):
    root = tmp_path / "g"
    root.mkdir()
    obs, w, stop, t, _ = _run(tmp_path, [Folder(str(root), recursive=True)])
    (root / "sub").mkdir()
    _wait(w, 1)
    time.sleep(0.2)
    (root / "sub" / "f.txt").write_text("1")
    _wait(w, 2)
    evs = _finish(w, stop, t, tmp_path)
    assert ("create", "sub/f.txt") in [(e["type"], e["path"]) for e in evs]


def test_inotify_failure_is_reported_not_raised(tmp_path, monkeypatch):
    import lhassa.observe.files as files

    def boom():
        raise OSError(24, "too many open files")

    monkeypatch.setattr(files, "Inotify", boom)
    states = []
    obs = FilesObserver(ObserveWriter("files", tmp_path, "i"), [Folder(str(tmp_path))], on_state=lambda *a: states.append(a))
    obs.run(threading.Event())
    assert states[0][0] == "inactive"


def test_unpaired_move_out_is_a_delete(tmp_path):
    from lhassa.observe.files import _Dir, _Root

    w = ObserveWriter("files", tmp_path / "rec", "i")
    obs = FilesObserver(w, [])
    obs._wd[1] = _Dir(_Root("r", str(tmp_path), False, 0), "")
    obs.handle([(1, IN_MOVED_FROM, 5, "gone.txt")])
    w.close()
    ev = read_log(next((tmp_path / "rec").rglob("*.jsonl")))[0][0]
    assert ev["type"] == "delete" and ev["path"] == "gone.txt"
