"""Shell observer: record parsing, the receiver over a FIFO and a datagram socket, the real bash hook."""

import os
import shutil
import socket
import subprocess
import threading
import time
from importlib import resources

import pytest

from lhassa.observe.shell import SEP, ShellObserver, clean_token, parse_record, prepare_dir, runtime_dir
from lhassa.observe.writer import ObserveWriter
from lhassa.record import read_log


def rec(shell="bash", cwd="/work/proj", rc="0", dur="12", tok="git", full=""):
    return SEP.join(["L1", shell, cwd, rc, dur, tok, full]).encode() + b"\n"


def test_parse_record_maps_fields():
    kind, fields, paths = parse_record(rec(rc="2", dur="1500", tok="/usr/bin/git"))
    assert kind == "shell_cmd" and paths == ("cwd",)
    assert fields == {"shell": "bash", "cwd": "/work/proj", "rc": 2, "dur_ms": 1500, "cmd": "git"}


@pytest.mark.parametrize("raw", [
    b"", b"garbage\n", rec(shell="fish"), rec(cwd="relative"), rec(rc="x"),
    rec(dur="-"), b"L1" + SEP.encode() + b"bash\n", b"\xff\xfe\n", rec(tok="a" * 5000),
])
def test_malformed_records_are_rejected(raw):
    assert parse_record(raw) is None or parse_record(raw)[1]["cmd"] in ("<other>", "")


def test_malformed_are_none_for_structural_errors():
    for raw in (b"", b"garbage\n", rec(shell="fish"), rec(cwd="relative"), rec(rc="x"), b"\xff\n"):
        assert parse_record(raw) is None


def test_first_token_is_sanitised():
    assert clean_token("FOO=hunter2") == "<assign>"
    assert clean_token("$(curl") == "<other>"
    assert clean_token("./run.sh") == "run.sh"
    assert clean_token("~/.ssh/config_tool") == "<withheld>"
    assert clean_token("ls") == "ls"


def test_full_line_needs_opt_in_scrub_and_never_keep():
    raw = rec(full="mail ana@example.com now")
    assert "line" not in parse_record(raw)[1]
    assert "ana@example.com" not in parse_record(raw, full_command=True)[1]["line"]
    assert parse_record(rec(full="cat ~/.ssh/id_rsa"), full_command=True)[1]["line"] == "<withheld>"
    assert parse_record(rec(full="source .env"), full_command=True)[1]["line"] == "<withheld>"


def test_runtime_dir_logic():
    assert str(runtime_dir("", {"XDG_RUNTIME_DIR": "/run/user/1"})) == "/run/user/1/lhassa-observe"
    assert str(runtime_dir("", {}, uid=7)) == "/tmp/lhassa-observe-7"
    assert str(runtime_dir("/x/y", {})) == "/x/y"


def test_prepare_dir_refuses_a_non_private_dir(tmp_path):
    d = tmp_path / "open"
    d.mkdir(mode=0o755)
    d.chmod(0o755)
    assert "not private" in prepare_dir(d)
    assert prepare_dir(tmp_path / "fresh") is None
    assert (os.stat(tmp_path / "fresh").st_mode & 0o777) == 0o700


@pytest.fixture
def receiver(tmp_path):
    d = tmp_path / "rt"
    w = ObserveWriter("shell", tmp_path / "rec", "inst")
    states = []
    obs = ShellObserver(w, directory=d, on_state=lambda *a: states.append(a))
    stop = threading.Event()
    t = threading.Thread(target=obs.run, args=(stop,))
    t.start()
    for _ in range(200):
        if (d / "shell.sock").exists() and states:
            break
        time.sleep(0.01)
    yield obs, w, d
    stop.set()
    t.join(3)
    w.close()


def _wait(w, n):
    for _ in range(300):
        if w.events >= n:
            return
        time.sleep(0.01)


def _events(tmp_path):
    return read_log(next((tmp_path / "rec").rglob("*.jsonl")))[0]


def test_socket_and_fifo_are_private_and_both_deliver(receiver, tmp_path):
    obs, w, d = receiver
    assert os.stat(d / "shell.sock").st_mode & 0o777 == 0o600
    assert os.stat(d / "shell.fifo").st_mode & 0o777 == 0o600
    c = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    c.sendto(rec(tok="ls"), str(d / "shell.sock"))
    c.close()
    fd = os.open(d / "shell.fifo", os.O_RDWR)
    os.write(fd, rec(tok="make") + rec(tok="bad") .replace(b"bash", b"nope"))
    os.close(fd)
    _wait(w, 2)
    cmds = sorted(e["cmd"] for e in _events(tmp_path))
    assert cmds == ["ls", "make"] and obs.rejected == 1
    assert (d / "shell.pid").read_text().strip() == str(os.getpid())


def test_never_keep_cwd_is_withheld(receiver, tmp_path, monkeypatch):
    obs, w, d = receiver
    home = os.path.expanduser("~")
    fd = os.open(d / "shell.fifo", os.O_RDWR)
    os.write(fd, rec(cwd=home + "/.ssh", tok="ls"))
    os.close(fd)
    _wait(w, 1)
    ev = _events(tmp_path)[0]
    assert ev["cwd"] == "<withheld>" and ev["withheld"] and ev["cmd"] == "ls"


def test_receiver_cleans_up_on_stop(tmp_path):
    d = tmp_path / "rt2"
    obs = ShellObserver(ObserveWriter("shell", tmp_path / "rec", "i"), directory=d)
    stop = threading.Event()
    t = threading.Thread(target=obs.run, args=(stop,))
    t.start()
    for _ in range(200):
        if (d / "shell.pid").exists():
            break
        time.sleep(0.01)
    stop.set()
    t.join(3)
    assert not any(d.iterdir())


def test_unusable_directory_is_inactive_without_raising(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    states = []
    ShellObserver(ObserveWriter("shell", tmp_path / "rec", "i"), directory=blocker / "sub",
                  on_state=lambda *a: states.append(a)).run(threading.Event())
    assert states[0][0] == "inactive"


HOOK = resources.files("lhassa.observe") / "hooks" / "observe.bash"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_real_bash_hook_sends_one_record_per_command(receiver, tmp_path):
    obs, w, d = receiver
    script = f"""source {HOOK}
false
cd /tmp
SECRET=hunter2 true
"""
    env = {**os.environ, "LHASSA_OBSERVE_DIR": str(d), "HISTFILE": "/dev/null"}
    subprocess.run(["bash", "--norc", "--noprofile", "-i"], input=script, text=True, env=env,
                   capture_output=True, timeout=20)
    _wait(w, 3)
    evs = _events(tmp_path)
    got = [(e["cmd"], e["rc"]) for e in evs]
    assert ("false", 1) in got and ("cd", 0) in got and ("<assign>", 0) in got
    assert all(e["shell"] == "bash" and e["dur_ms"] >= 0 for e in evs)
    assert "hunter2" not in open(next((tmp_path / "rec").rglob("*.jsonl"))).read()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_hook_is_silent_without_a_receiver(tmp_path):
    env = {**os.environ, "LHASSA_OBSERVE_DIR": str(tmp_path / "none"), "HISTFILE": "/dev/null"}
    r = subprocess.run(["bash", "--norc", "--noprofile", "-i"], input=f"source {HOOK}\ntrue\n", text=True,
                       env=env, capture_output=True, timeout=20)
    assert r.returncode == 0 and "No such file" not in r.stderr and not (tmp_path / "none").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_hook_preserves_exit_status(receiver):
    obs, w, d = receiver
    env = {**os.environ, "LHASSA_OBSERVE_DIR": str(d), "HISTFILE": "/dev/null"}
    r = subprocess.run(["bash", "--norc", "--noprofile", "-i"],
                       input=f"source {HOOK}\nfalse\necho rc=$?\n", text=True, env=env,
                       capture_output=True, timeout=20)
    assert "rc=1" in r.stdout
