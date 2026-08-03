"""Tests for lhassa.tools.monitor process selection."""

import sys
import pytest


class _ProcInfo:
    def __init__(self, pid: int, name: str):
        self.info = {"pid": pid, "name": name}


class _ResolvedProc:
    def __init__(self, pid: int, create_time: float = 0.0):
        self.pid = pid
        self._create_time = create_time

    def create_time(self):
        return self._create_time


def test_find_process_rejects_ambiguous_name(monkeypatch):
    from lhassa.tools import monitor

    class FakePsutil:
        NoSuchProcess = RuntimeError

        @staticmethod
        def process_iter(_attrs):
            return [_ProcInfo(10, "python"), _ProcInfo(11, "python")]

        @staticmethod
        def Process(pid: int):
            return _ResolvedProc(pid, create_time=float(pid))

    monkeypatch.setitem(sys.modules, "psutil", FakePsutil)

    with pytest.raises(RuntimeError, match="Multiple running processes match 'python'"):
        monitor._find_process(None, "python")


def test_find_process_returns_single_name_match(monkeypatch):
    from lhassa.tools import monitor

    class FakePsutil:
        NoSuchProcess = RuntimeError

        @staticmethod
        def process_iter(_attrs):
            return [_ProcInfo(22, "server")]

        @staticmethod
        def Process(pid: int):
            return _ResolvedProc(pid, create_time=float(pid))

    monkeypatch.setitem(sys.modules, "psutil", FakePsutil)

    proc = monitor._find_process(None, "server")
    assert proc.pid == 22
