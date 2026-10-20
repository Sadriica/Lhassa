# Contributing

Lhassa is a young project with one demanding first user: its author. Issues,
questions and PRs are welcome; small and focused beats large and sweeping.

## License and the Contributor License Agreement

> **DRAFT. NOT YET IN FORCE.** This CLA is a working draft written by the
> project author. **It must be reviewed by a lawyer before the first external
> pull request is accepted.** Until that review happens, do not treat it as a
> binding agreement, and do not sign it.

Lhassa is licensed under **AGPL-3.0-or-later** with an additional attribution
term under section 7(b) (see [`LICENSE`](LICENSE)).

### Why a CLA exists here

Plainly: **so that a commercial license can be offered later.**

The AGPL is the right default for a sovereign, self-hosted brain; it keeps
Lhassa free for everyone who runs it themselves, and it closes the gap where
someone could take it, run it as a network service, and give nothing back. But
some organisations cannot deploy AGPL software at all. Serving them requires
offering the same code under separate commercial terms, and that is only
possible if a single party holds sufficient rights over the whole codebase.

A CLA is what makes that possible. It is also, honestly, the one asymmetric
thing in this project: it gives the project author rights over your
contribution that you do not get over anyone else's. You should weigh that
before contributing. If you would rather not, an issue, a bug report or a
review is genuinely valuable and requires no agreement at all.

### The agreement (draft, modelled on the Apache ICLA)

By submitting a contribution to this project, you agree that:

1. **Grant of copyright licence.** You grant the project author a perpetual,
   worldwide, non-exclusive, royalty-free, irrevocable copyright licence to
   reproduce, prepare derivative works of, publicly display, publicly perform,
   sublicense and distribute your contribution and such derivative works;
   **including under licence terms of the author's choosing, such as a
   commercial licence.**
2. **Grant of patent licence.** You grant a perpetual, worldwide,
   non-exclusive, royalty-free, irrevocable (except as stated below) patent
   licence to make, use, offer to sell, sell, import and otherwise transfer
   your contribution, for patent claims you own that are necessarily infringed
   by your contribution alone or in combination with this project. If any
   entity institutes patent litigation alleging that this project or a
   contribution infringes a patent, the patent licences granted here for that
   entity terminate.
3. **You have the right to grant this.** Each contribution is your original
   work, or you have the necessary rights to submit it. If your employer has
   rights to work you create, you have permission to contribute on their
   behalf, or they have waived those rights.
4. **You keep your rights.** You retain all right, title and interest in your
   contribution. This is a **licence**, not an assignment; you may use your
   own work however you like, elsewhere.
5. **No warranty.** Contributions are provided as-is, without warranty of any
   kind, to the extent permitted by law.
6. **Third-party work.** If you submit work that is not your own, mark it
   clearly and include its licence and origin.

### How to sign

Once the CLA has passed legal review, a signing process will be published here.
**No signing process is in force yet**, because no external contribution has
been accepted yet.

### Attribution notice; please do not remove it

The AGPL 7(b) term requires the "Lhassa; a Witara project" notice to stay
visible in the CLI, the API and the dashboard. It is rendered from
[`lhassa/legal.py`](lhassa/legal.py) and [`dashboard/src/legal.js`](dashboard/src/legal.js),
and `tests/test_legal_notice.py` will fail if a surface stops showing it. That
is deliberate: it is a legal notice, not decoration.

## Ground rules

1. **Read [`doc/architecture-layers.md`](doc/architecture-layers.md) first.**
   Dependencies point down, never out to a shell; `tests/test_architecture.py`
   enforces it and your change must keep it green. Prefer fixing layering over
   adding an exception.
2. **Capabilities are drop-in.** Providers, channels, commands, actions,
   verifiers, seeds; every swap point sits behind a registry. If your feature
   edits core to add a capability, there is probably a registry you missed
   (see the keystone section of [`doc/architecture.md`](doc/architecture.md)).
3. **The deterministic plane stays deterministic.** Actions, verifiers, the
   monitor, the digest template and routing never call a model. Thinking is
   the exception; guard it behind the router and degrade on failure; a
   heartbeat must never crash on a provider error.
3b. **The hierarchy is a philosophy, not a feature.** Work flows down the org
   chart, results and artifacts flow back up through it, and every brain in a
   chain logs its own hop (deterministic, $0). New capabilities that produce
   or deliver anything are born on this path; never add a surface-direct
   send or an unlogged hop.
4. **Boundaries** ([`doc/boundaries.md`](doc/boundaries.md)): test fixtures use
   invented identities only; seeds are generic presets; nothing personal ever
   enters the tree.
5. **Voice** ([`doc/narrative.md`](doc/narrative.md)): product text is third
   person; anything Lhassa says to its owner is first person, calm, honest.
   English everywhere in code and docs; Lhassa is an "it".

## Dev loop

```bash
make install   # uv sync --all-packages --extra runtime (voice runtime) + dashboard build
make test      # the suite; keep it green, add tests with your change
make lint      # ruff
make check     # validate the environment, no API calls
```

A single test file: `uv run --extra dev python -m pytest tests/test_x.py`.
CI runs the suite, ruff, dashboard lint and the Playwright E2E on every PR.

## Commit style

Plain, descriptive messages that explain the why (read `git log` for the
house voice). No attribution footers of any kind.
