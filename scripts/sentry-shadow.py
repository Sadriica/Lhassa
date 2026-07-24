#!/usr/bin/env python3
"""
lhassa-sentry shadow harness - runs the Python watcher's deterministic ear
side by side with the Rust sentry (sentry/) and reports whether they see the
same events tick for tick.

READ-ONLY. This script imports lhassa.org.monitor / lhassa.org.watcher for
their pure/read functions only:

  - lhassa.org.monitor.gather_signals   (read-only signal collection)
  - lhassa.org.watcher.detect_events    (pure diff, no I/O)
  - lhassa.org.watcher._fingerprint     (pure)
  - lhassa.org.watcher._is_important    (pure)

It never calls react(), watch_loop(), or anything that writes an Episode,
sends a notification, or mints a task. It never touches the Rust binary's
territory beyond running it as a subprocess and reading its stdout.

Usage:
  uv run python scripts/sentry-shadow.py --ticks 10 --interval 3
  uv run python scripts/sentry-shadow.py --ticks 10 --interval 3 --sentry-bin sentry/target/release/lhassa-sentry
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SENTRY_BIN = REPO_ROOT / "sentry" / "target" / "release" / "lhassa-sentry"


def _load_python_watcher():
    """Import the watcher's pure functions by name (read-only usage only)."""
    sys.path.insert(0, str(REPO_ROOT))
    from lhassa.config import Config
    from lhassa.org.monitor import gather_signals
    from lhassa.org.watcher import _fingerprint, _is_important, detect_events

    return Config, gather_signals, detect_events, _fingerprint, _is_important


def _normalize_event(ev: dict) -> tuple:
    """A comparable (kind, level, summary) triple, dict-shape-agnostic."""
    return (str(ev.get("kind")), str(ev.get("level")), str(ev.get("summary")))


def _events_equal(py_events: list[dict], rust_events: list[dict]) -> bool:
    return sorted(map(_normalize_event, py_events)) == sorted(map(_normalize_event, rust_events))


def _fmt_events(events: list[dict]) -> str:
    if not events:
        return "(none)"
    return "; ".join(f"{e.get('kind')}:{e.get('summary')}" for e in events)


class RustSentry:
    """A running `lhassa-sentry --loop <interval> --json-lines` subprocess.

    Read-only: this harness never writes to its stdin and never sends it
    anything beyond SIGTERM at the end.
    """

    def __init__(self, binary: Path, interval: float):
        self.proc = subprocess.Popen(
            [str(binary), "--loop", str(interval), "--json-lines"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )

    def next_tick(self, timeout: float) -> dict | None:
        """Blocks for one JSON line from the sentry's stdout, or None on timeout/EOF."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                if self.proc.poll() is not None:
                    return None
                time.sleep(0.05)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        stderr = ""
        if self.proc.stderr:
            stderr = self.proc.stderr.read() or ""
        return stderr


def run_shadow(ticks: int, interval: float, sentry_bin: Path) -> int:
    if not sentry_bin.exists():
        print(f"sentry binary not found at {sentry_bin} - build it first:", file=sys.stderr)
        print("  cd sentry && cargo build --release", file=sys.stderr)
        return 2

    Config, gather_signals, detect_events, fingerprint, is_important = _load_python_watcher()
    config = Config()

    rust = RustSentry(sentry_bin, interval)

    prev_py = None
    results = []  # list of (tick_no, equal: bool, py_events, rust_events)

    try:
        for tick_no in range(ticks):
            rust_tick = rust.next_tick(timeout=interval * 3 + 5)
            if rust_tick is None:
                print(f"tick {tick_no}: sentry produced no output (process died?)", file=sys.stderr)
                break

            # Python side: gather its own signals at (roughly) the same moment and
            # diff against the harness's own running previous snapshot - mirrors
            # watch_loop's chaining without calling watch_loop/react at all.
            curr_py = gather_signals(config)
            py_events = detect_events(prev_py, curr_py)
            prev_py = curr_py

            rust_events = rust_tick.get("events") or []

            equal = _events_equal(py_events, rust_events)
            results.append((tick_no, equal, py_events, rust_events))

            py_important = [e for e in py_events if is_important(e)]
            rust_important = rust_tick.get("important") or []
            status = "==" if equal else "!="
            print(
                f"tick {tick_no}: {status}  py={_fmt_events(py_events)}"
                f"  rust={_fmt_events(rust_events)}"
                f"  (important py={len(py_important)} rust={len(rust_important)})"
            )
    finally:
        stderr = rust.stop()
        if stderr.strip():
            print("--- sentry stderr ---", file=sys.stderr)
            print(stderr, file=sys.stderr)

    same = sum(1 for _, equal, _, _ in results if equal)
    diff = len(results) - same
    print()
    print(f"summary: {len(results)} tick(s) compared - {same} equal, {diff} different")
    if diff:
        print("differing ticks:")
        for tick_no, equal, py_events, rust_events in results:
            if not equal:
                print(f"  tick {tick_no}:")
                print(f"    python: {_fmt_events(py_events)}")
                print(f"    rust:   {_fmt_events(rust_events)}")
    return 0 if diff == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=10, help="number of ticks to compare")
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between ticks")
    parser.add_argument(
        "--sentry-bin",
        type=Path,
        default=DEFAULT_SENTRY_BIN,
        help="path to the built lhassa-sentry release binary",
    )
    args = parser.parse_args()
    return run_shadow(args.ticks, args.interval, args.sentry_bin)


if __name__ == "__main__":
    raise SystemExit(main())
