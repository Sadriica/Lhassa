# Lhassa; architecture layers (the modularity guard)

Lhassa must stay **fully elastic and highly modular**: clean layering that does
**not** erode over time. This document describes the layers and the forbidden
import directions between them. The rules are enforced mechanically by
`tests/test_architecture.py`: a dependency-free pytest that parses every
`lhassa/**/*.py` with the standard-library `ast` module and fails if a future
change crosses a boundary. The rules were *measured from the current tree*, so
they pass today and only trip on a **new** violation.

## The layers

From the bottom (depends on nothing) to the top (the world-facing surfaces):

| Layer | Subpackages | Role |
| --- | --- | --- |
| **Leaf infrastructure** | `registry.py`, `plugins.py` (and `config.py`) | Generic, reusable machinery. Imports no feature code. |
| **Spine** | `core/` | The do-and-sleep loop (`step`, `heartbeat`, `episode`, `db`). Driven *by* the shells; never reaches up to them. |
| **Interfaces (mid)** | `memory/`, `security/`, `llm/`, `personal/` | Reusable contracts (typed facts, trust/approval, model provider + router, the personal agenda read-model). Usable by any shell; depend on none. |
| **Adapters (the hands)** | `harness/`, `tools/`, `agents/`, `remote/`, `health/`, `bench/` | The rentable "body": harnesses, MCP/tools, specialist agents, the remote local-agent, the LhassaBench eval harness. |
| **File-I/O seams** | `cv/`, `calendar/` | Leaf, stdlib-only seams (`.tex` CV, `.ics` calendar) used by shells; never reach into one. |
| **Shells (outer)** | `cli/`, `api/`, `voice/`, `notify/`, `org/`, `actionpack/`, `flows/`, `dashboard/` (JS) | The user/world surfaces. Allowed to compose everything below them. `flows/` counts as a shell because its engine composes the org layer. |

Principle: **inner layers never depend on outer shells.** Dependencies point
"down" only. A shell may import the spine, interfaces and adapters; the spine and
the interfaces must not import a shell.

## Forbidden directions (encoded today)

1. **spine-isolation**: `core/` must not import any shell
   (`cli`, `api`, `voice`, `notify`, `org`, `actionpack`, `flows`, `dashboard`).
2. **interface-isolation**: `memory/`, `security/`, `llm/` must not import any
   shell. **personal-isolation** applies the same rule to `personal/`.
3. **adapter-isolation**: `harness/`, `tools/`, `agents/`, `remote/`, `health/`,
   `bench/` must not import any shell (see the one whitelisted exception below).
4. **swap-axis-independence**: `harness/` (the agentic-body axis) must not
   import `llm/` (the think-directly model-provider axis). The two swap axes stay
   independent. The reverse edge; `llm/router.py` *choosing* a harness; is the
   intentional bridge seam and is **not** forbidden.
5. **cv-infra-isolation** and **calendar-infra-isolation**: `cv/` and `calendar/`
   must not import any shell.
   `cv/` ships in the `lhassa-cv` pack (`packages/lhassa-cv`); its shell slice
   (API router, chat commands, Telegram hook, flow entries, seed) lives under
   `cv/surfaces/`, which the rule exempts.
6. **keystone-leaf**: `registry.py` and `plugins.py` must not import any
   `lhassa.*` feature module (they are bottom-of-stack infrastructure).
7. **research-isolation**: the research pack's inner modules (`research/`
   outside `research/surfaces/`) must not import any shell.

## Allowed exceptions

Precise, intentional edges that are whitelisted in the test (keyed on the exact
`source-file → imported-module` pair, so they do not loosen the rest of the rule):

- `plugins.py` → `lhassa.config`: `config` is itself leaf infrastructure (pure
  configuration, not a feature module), so the plugin loader reading `Config`
  does not break the keystone rule.
- `health/checks.py` → `lhassa.notify.dispatch`: Etapa 2 unifies outbound alerts:
  `health/checks.py`'s `_notify` fans a check result out over every configured
  notify channel (desktop is then just one channel among Telegram/log/…) instead
  of hard-coding a desktop-only `notify-send` call. The import is lazy, inside the
  function body.

- `personal/secretary.py` → `lhassa.org`, `lhassa.org.management`,
  `lhassa.org.topology`, `lhassa.flows.model`, `lhassa.flows.store`: the personal
  department is provisioned into the org chart together with its canonical
  briefing flows (a seed-shaped bootstrap, not a runtime dependency).
- `security/approval.py` → `lhassa.flows.durable`, `lhassa.flows.projection`: the
  durable flow-gate bridge, so an approval can resume a paused flow run.

## Capability packs

A capability can leave the core tree and ship as its own distribution under
`packages/lhassa-<x>/src/lhassa/<sub>` (contract: `packages/README.md`). The
`lhassa` package is a pkgutil namespace, so import paths do not change when a
module moves into a pack. Packs are discovered as plugins (`lhassa.plugins`
entry points, loaded by `actionpack.register_all()`), and reach the surfaces
through `lhassa/extensions.py` (`register_api_router`, `register_cli_app`,
`register_pack_migrations`, `register_pack`) plus the registries that already
existed (actions, commands, seeds, channels, senses, providers).

The guard walks `packages/*/src/lhassa` too and adds two rules on top of the
layer rules (which still apply to a pack by its subpackage name):

- **core-never-imports-a-pack**: nothing under `lhassa/` imports a pack.
- **pack-isolation**: a pack imports another pack only when the edge is
  declared in `PACK_DEPENDENCIES`.

A pack's `surfaces/` subpackage is its shell slice (router, CLI sub-app, chat
commands), so the shell-isolation rules do not apply to files under it.
`make test-core` runs the root suite in an environment with no pack installed.

## How to evolve the rules

If you intentionally add a new allowed edge, add a precise entry to
`ALLOWED_EXCEPTIONS` in `tests/test_architecture.py` **with a comment explaining
why**. Prefer fixing the layering over adding an exception. If you add a new
subpackage, slot it into the correct layer (and the rule sets) so the guard keeps
covering it; `test_rules_reference_real_subpackages` guards against typos.
