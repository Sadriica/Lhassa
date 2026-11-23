# Coverage baseline

The project had 2651 tests and no coverage measurement. `make cov` runs the
same unit suite `make test` runs (pytest respects `pyproject.toml`'s
`addopts = -m "not integration"`, so this is over the unit suite, not the
live-CLI integration test) instrumented with `pytest-cov`, writing a terminal
report and an HTML one to `htmlcov/index.html`. Coverage is a separate target
on purpose; instrumentation slows the run, so `make test` never pays for it.

`[tool.coverage.run]`/`[tool.coverage.report]` in `pyproject.toml` scope
`source` to `lhassa` and omit `lhassa/_vendor/*` (vendored third-party code)
and `*/lhassa/voice/*` (the voice pack's pipecat/torch audio pipeline; needs a mic/speaker,
never exercised by the unit suite).

## Baseline. 2026-08-04

**78% total** (29,088 statements, 6,336 missed), 2651 passed / 2 deselected,
run time ~4m20s.

10 worst-covered modules (all 0%; never imported by the unit suite):

| Module | Statements missed |
|---|---|
| `lhassa/tools/handlers/github.py` | 114/114 |
| `lhassa/tools/vision.py` | 113/113 |
| `lhassa/tools/handlers/outlook.py` | 98/98 |
| `lhassa/tools/graph.py` | 86/86 |
| `lhassa/tools/handlers/files.py` | 48/48 |
| `lhassa/tools/handlers/monitor.py` | 46/46 |
| `lhassa/tools/handlers/shell.py` | 46/46 |
| `lhassa/core/heartbeat_loop.py` | 45/45 |
| `lhassa/org/integrity_architecture.py` | 44/44 |
| `lhassa/tools/handlers/agents.py` | 44/44 |

These are mostly the voice-tool `handle_<name>` bodies (registered in
`tools/registry.py` but reachable only through the live voice pipeline, which
`tests/` never drives directly) and a couple of dead-looking entry points
(`core/heartbeat_loop.py`, `org/__main__.py`, `org/integrity_architecture.py`)
worth a follow-up look; either add direct unit tests for the handlers, or
confirm the entry points are exercised some other way and mark them
`# pragma: no cover` deliberately.

Re-measure any time with `make cov`; the HTML report under `htmlcov/` is the
easiest way to see missing lines file-by-file.
