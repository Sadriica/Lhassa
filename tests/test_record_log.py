# SPDX-License-Identifier: AGPL-3.0-or-later
"""The append-only event log: durability, determinism and crash behaviour."""

import os
import signal
import stat
import subprocess
import sys
import textwrap

from lhassa.record import EventLog, log_path, read_log


def _clock():
    t = iter(x / 10 for x in range(1, 1000))
    return lambda: next(t)


def test_events_are_appended_in_order_with_sequence(tmp_path):
    log = EventLog(tmp_path / "r.jsonl", run_id="r1", source="claude", clock=_clock())
    log.emit({"t": "start"})
    log.emit({"t": "tool", "tool": "Read"})
    log.close()
    events, torn = read_log(tmp_path / "r.jsonl")
    assert not torn
    assert [e["seq"] for e in events] == [1, 2]
    assert [e["t"] for e in events] == ["start", "tool"]
    assert events[0]["run"] == "r1" and events[0]["source"] == "claude"


def test_same_events_and_clock_give_identical_bytes(tmp_path):
    def run(name):
        log = EventLog(tmp_path / name, run_id="r1", source="x", clock=_clock())
        for i in range(5):
            log.emit({"t": "tool", "tool": f"T{i}", "target": "/a"})
        log.close()
        return (tmp_path / name).read_bytes()

    assert run("a.jsonl") == run("b.jsonl")


def test_file_and_parent_are_private(tmp_path):
    log = EventLog(tmp_path / "deep" / "r.jsonl", run_id="r", source="x")
    log.emit({"t": "start"})
    log.close()
    assert stat.S_IMODE(os.stat(tmp_path / "deep" / "r.jsonl").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "deep").st_mode) == 0o700


def test_reading_a_missing_log_is_an_empty_run(tmp_path):
    assert read_log(tmp_path / "nope.jsonl") == ([], False)


def test_a_partial_last_line_is_reported_not_raised(tmp_path):
    p = tmp_path / "r.jsonl"
    log = EventLog(p, run_id="r", source="x")
    log.emit({"t": "start"})
    log.emit({"t": "tool"})
    log.close()
    with open(p, "ab") as fh:
        fh.write(b'{"v":1,"run":"r","seq":3,"t":"to')  # torn write, no newline
    events, torn = read_log(p)
    assert torn is True
    assert [e["seq"] for e in events] == [1, 2]


def test_an_unwritable_destination_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    log = EventLog(blocker / "sub" / "r.jsonl", run_id="r", source="x")
    log.emit({"t": "start"})  # must not raise
    log.close()


def test_a_failing_sink_never_stops_the_log(tmp_path):
    seen = []

    def boom(rec):
        seen.append(rec["seq"])
        raise RuntimeError("sink down")

    log = EventLog(tmp_path / "r.jsonl", run_id="r", source="x", sinks=[boom])
    log.emit({"t": "a"})
    log.emit({"t": "b"})
    log.close()
    assert seen == [1, 2]
    assert [e["t"] for e in read_log(tmp_path / "r.jsonl")[0]] == ["a", "b"]


def test_log_path_is_derived_from_the_ids(tmp_path):
    assert log_path(tmp_path, "inst", "j_1") == tmp_path / "inst" / "j_1.jsonl"


def test_a_process_killed_mid_run_keeps_everything_written_before_the_kill(tmp_path):
    """The crash test: SIGKILL with no close(), then a torn write, then read back."""
    p = tmp_path / "killed.jsonl"
    child = textwrap.dedent(
        f"""
        import os, signal
        from lhassa.record import EventLog
        log = EventLog({str(p)!r}, run_id="r", source="x")
        for i in range(5):
            log.emit({{"t": "tool", "tool": "T%d" % i}})
        os.write(log._fd, b'{{"v":1,"seq":99,"t":"half')  # dies mid-line
        os.kill(os.getpid(), signal.SIGKILL)
        """
    )
    proc = subprocess.run([sys.executable, "-c", child], capture_output=True)
    assert proc.returncode == -signal.SIGKILL
    events, torn = read_log(p)
    assert torn is True
    assert [e["tool"] for e in events] == [f"T{i}" for i in range(5)]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]
