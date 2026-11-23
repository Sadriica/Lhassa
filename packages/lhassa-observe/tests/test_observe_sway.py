"""Sway observer: framing, event mapping and a fake IPC server on a tmp socket."""

import json
import socket
import threading

import pytest

from lhassa.observe.sway import (
    EVENT_WINDOW, EVENT_WORKSPACE, SwayObserver, SwayState, pack, parse_frames,
)
from lhassa.observe.writer import ObserveWriter
from lhassa.record import read_log


def _frame(t, obj):
    return pack(t, json.dumps(obj).encode())


def test_frames_round_trip_and_partial_buffers():
    data = _frame(1, [{"name": "1"}]) + _frame(EVENT_WINDOW, {"change": "focus"})
    frames, rest = parse_frames(data[:-3])
    assert [f[0] for f in frames] == [1] and rest
    frames2, rest2 = parse_frames(rest + data[-3:])
    assert frames2[0][0] == EVENT_WINDOW and rest2 == b""


def test_bad_magic_is_rejected():
    with pytest.raises(ValueError):
        parse_frames(b"xx-ipc" + b"\0" * 20)


WIN = {
    "change": "focus",
    "container": {"app_id": "foot", "name": "mail from boss: secret", "window_properties": None},
}


def test_window_focus_has_no_title_by_default():
    st = SwayState()
    st.seed([{"name": "1", "focused": False}, {"name": "3", "focused": True}])
    kind, fields = st.handle(EVENT_WINDOW, WIN)
    assert kind == "window_focus"
    assert fields == {"workspace": "3", "app_id": "foot"}


def test_x11_class_and_workspace_tracking():
    st = SwayState()
    assert st.handle(EVENT_WORKSPACE, {"change": "focus", "current": {"name": "2"}}) == ("workspace_focus", {"workspace": "2"})
    kind, fields = st.handle(EVENT_WINDOW, {"change": "focus", "container": {"app_id": None, "window_properties": {"class": "Firefox"}}})
    assert fields == {"workspace": "2", "class": "Firefox"}


def test_repeated_focus_and_other_changes_are_ignored():
    st = SwayState()
    assert st.handle(EVENT_WINDOW, WIN) is not None
    assert st.handle(EVENT_WINDOW, WIN) is None
    assert st.handle(EVENT_WINDOW, {"change": "title", "container": {"app_id": "foot"}}) is None
    assert st.handle(EVENT_WINDOW, {"change": "focus", "container": {}}) is None
    assert st.handle(EVENT_WORKSPACE, "garbage") is None


def test_title_is_opt_in_and_scrubbed():
    st = SwayState(titles=True)
    _, fields = st.handle(EVENT_WINDOW, {"change": "focus", "container": {"app_id": "x", "name": "mail ana@example.com"}})
    assert "ana@example.com" not in fields["title"] and fields["title"].startswith("mail")


def test_title_with_a_never_keep_path_is_withheld(tmp_path):
    w = ObserveWriter("sway", tmp_path / "rec", "inst")
    st = SwayState(titles=True)
    _, fields = st.handle(EVENT_WINDOW, {"change": "focus", "container": {"app_id": "foot", "name": "vim ~/.ssh/id_ed25519"}})
    ev = w.emit("window_focus", fields)
    w.close()
    assert ev["title"] == "<withheld>" and ev["withheld"]


def test_no_swaysock_is_inactive_without_error(tmp_path, monkeypatch):
    monkeypatch.delenv("SWAYSOCK", raising=False)
    states = []
    obs = SwayObserver(ObserveWriter("sway", tmp_path, "i"), on_state=lambda *a: states.append(a))
    obs.run(threading.Event())
    assert states[0][0] == "inactive"


def test_unreachable_socket_retries_then_stops_cleanly(tmp_path):
    states = []
    obs = SwayObserver(ObserveWriter("sway", tmp_path, "i"), socket_path=str(tmp_path / "none.sock"),
                       backoff=(0.01, 0.02), on_state=lambda *a: states.append(a))
    stop = threading.Event()
    t = threading.Thread(target=obs.run, args=(stop,))
    t.start()
    for _ in range(100):
        if states:
            break
        stop.wait(0.01)
    stop.set()
    t.join(2)
    assert not t.is_alive() and states[0][0] == "retrying"


def test_full_session_against_a_fake_sway(tmp_path):
    path = str(tmp_path / "s.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def server():
        conn, _ = srv.accept()
        conn.recv(4096)  # GET_WORKSPACES
        conn.sendall(_frame(1, [{"name": "5", "focused": True}]))
        conn.recv(4096)  # SUBSCRIBE
        conn.sendall(_frame(2, {"success": True}))
        conn.sendall(_frame(EVENT_WINDOW, WIN))
        conn.sendall(_frame(EVENT_WORKSPACE, {"change": "focus", "current": {"name": "6"}}))
        threading.Event().wait(0.3)
        conn.close()

    threading.Thread(target=server, daemon=True).start()
    w = ObserveWriter("sway", tmp_path / "rec", "inst")
    obs = SwayObserver(w, socket_path=path, backoff=(0.01, 0.02))
    stop = threading.Event()
    t = threading.Thread(target=obs.run, args=(stop,))
    t.start()
    for _ in range(300):
        if w.events >= 2:
            break
        stop.wait(0.01)
    stop.set()
    t.join(3)
    w.close()
    srv.close()
    events, _ = read_log(next((tmp_path / "rec").rglob("*.jsonl")))
    kinds = [(e["t"], e.get("workspace"), e.get("app_id")) for e in events]
    assert ("window_focus", "5", "foot") in kinds and ("workspace_focus", "6", None) in kinds
    assert all("title" not in e and "secret" not in json.dumps(e) for e in events)
