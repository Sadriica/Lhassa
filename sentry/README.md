# lhassa-sentry

The ear in Rust, shadow mode. Ports the deterministic listening plane of
`lhassa/org/watcher.py`: signal gathering (episodes and approvals from
`brain.db`, health digest, flows), event detection, fingerprints, and the
importance gate. It never writes, never launches anything, and has no
network access. The reaction (task minting, trust, episodes, notification)
stays in Python by design: compile the ear, keep the judgment.

Status: shadow only. Do not wire it into systemd or Docker until the
shadow diff has run clean for days.

```bash
cargo build --release            # target/release/lhassa-sentry, ~1.4 MB
cargo test && cargo clippy --all-targets -- -D warnings

./target/release/lhassa-sentry --once --json        # one tick, JSON out
./target/release/lhassa-sentry --loop 15 --json-lines

# parity against the live Python watcher (read-only on both sides):
../.venv/bin/python ../scripts/sentry-shadow.py --ticks 10 --interval 3
```

Known v1 gap, by design: `agent_runs` lives in in-process asyncio state
(`lhassa.state`), which no external process can observe, so run lifecycle
events never fire from the sentry. See the doc comment in `src/signals.rs`.

Measured on samos, 2026-07-24: binary 1.4 MB, resident RSS ~3.9 MB in
`--loop`, against ~50 MB for the resident Python watcher.
