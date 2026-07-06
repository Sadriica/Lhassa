# Contributing

Lhassa is a young project with one demanding first user: its author. Issues,
questions and PRs are welcome — small and focused beats large and sweeping.

## Ground rules

1. **Read [`doc/architecture-layers.md`](doc/architecture-layers.md) first.**
   Dependencies point down, never out to a shell; `tests/test_architecture.py`
   enforces it and your change must keep it green. Prefer fixing layering over
   adding an exception.
2. **Capabilities are drop-in.** Providers, channels, commands, actions,
   verifiers, seeds — every swap point sits behind a registry. If your feature
   edits core to add a capability, there is probably a registry you missed
   (see the keystone section of [`CLAUDE.md`](CLAUDE.md)).
3. **The deterministic plane stays deterministic.** Actions, verifiers, the
   monitor, the digest template and routing never call a model. Thinking is
   the exception; guard it behind the router and degrade on failure — a
   heartbeat must never crash on a provider error.
3b. **The hierarchy is a philosophy, not a feature.** Work flows down the org
   chart, results and artifacts flow back up through it, and every brain in a
   chain logs its own hop (deterministic, $0). New capabilities that produce
   or deliver anything are born on this path — never add a surface-direct
   send or an unlogged hop.
4. **Boundaries** ([`doc/boundaries.md`](doc/boundaries.md)): test fixtures use
   invented identities only; seeds are generic presets; nothing personal ever
   enters the tree.
5. **Voice** ([`doc/narrative.md`](doc/narrative.md)): product text is third
   person; anything Lhassa says to its owner is first person, calm, honest.
   English everywhere in code and docs; Lhassa is an "it".

## Dev loop

```bash
make install   # uv sync (with the voice extra) + dashboard build
make test      # the suite — keep it green, add tests with your change
make lint      # ruff
make check     # validate the environment, no API calls
```

A single test file: `uv run --extra dev python -m pytest tests/test_x.py`.
CI runs the suite, ruff, dashboard lint and the Playwright E2E on every PR.

## Commit style

Plain, descriptive messages that explain the why (read `git log` for the
house voice). No attribution footers of any kind.
