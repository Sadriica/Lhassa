# Capability packs

A **pack** is one capability, end to end, shipped as its own distribution.
Core (the repo root, distribution `lhassa`) never imports a pack: it reaches a
pack only through a registry. Removing a pack's directory and re-syncing must
leave core working, with that capability simply absent.

```
packages/lhassa-<name>/
  pyproject.toml              # distribution "lhassa-<name>", depends on "lhassa"
  src/lhassa/<sub>/           # NO lhassa/__init__.py here; core's __init__ extends the namespace
    __init__.py
    pack.py                   # the entry point: importing it registers everything
    ...                       # the capability's own modules (keep their layer)
    surfaces/                 # the pack's shell slice: API router, CLI sub-app,
      __init__.py             #   chat commands, actions that compose shells
      api.py  cli.py  ...
  tests/
    conftest.py               # re-exports tests/conftest.py (see rule 8)
    test_<sub>_*.py           # test file names carry the pack prefix (pytest
                              #   rootdir mode rejects duplicate basenames)
```

## The contract

1. **Namespace, not rename.** The pack ships `lhassa.<sub>` under its own
   `src/`. Import paths do not change when a module moves into a pack.
2. **Discovery.** `pyproject.toml` declares
   `[project.entry-points."lhassa.plugins"] <name> = "lhassa.<sub>.pack"`.
   `actionpack.register_all()` (called by every entrypoint) runs
   `plugins.load_plugins()`, which imports `pack.py`.
3. **`pack.py` only registers.** It calls `extensions.register_pack(...)` first,
   then the existing registries: `core.actions.register_action`,
   `notify.commands.register_command`, `seeds.register_seed`,
   `core.watchlist.register_check`, `notify.channels.register_channel`,
   `llm.provider.register_provider`; and the surface ones in
   `lhassa/extensions.py`: `register_api_router(name, factory)`,
   `register_cli_app(name, factory)`, `register_pack_migrations(pack, migrations)`.
   Surface factories import their module lazily (inside the factory), so a
   process that never serves the API never imports fastapi through a pack.
   Registration must be idempotent (tests import twice).
4. **Tables.** New tables a pack owns go in its own migration track
   (`register_pack_migrations`, versioned per pack in `pack_schema`). Tables
   that already exist in core's `MIGRATIONS` stay there: rewriting applied
   migrations would break existing databases.
5. **Dependencies.** Third-party deps the capability needs move from the root
   `pyproject.toml` into the pack's. A pack that imports another pack declares
   it in its `pyproject.toml` AND in `PACK_DEPENDENCIES` in
   `tests/test_architecture.py`.
6. **Inbound edges become seams.** If core code called into the capability,
   replace the call with a registry lookup or hook that degrades to "absent"
   (never raises) when the pack is not installed.
7. **Proof.** `make test` (everything) and `make test-core` (root alone, no
   packs) both pass, and `tests/test_architecture.py` stays green: the guard
   walks `packages/*/src/lhassa` too and enforces `core-never-imports-a-pack`
   and `pack-isolation`.
8. **Hermetic tests.** `tests/conftest.py` only reaches files under `tests/`,
   so every pack's `tests/conftest.py` re-exports it (copy the one in
   `packages/lhassa-bench/tests/`). Without it a pack test gets none of core's
   isolation, including the autouse fixture that keeps tests off the real
   `~/.lhassa/brain.db`.

## Installing

```bash
uv sync --all-packages        # core + every pack (what `make install` does)
uv sync                       # core alone
uv sync --package lhassa-cv   # core + one pack
```
