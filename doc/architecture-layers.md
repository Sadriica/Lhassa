# Lhassa — architecture layers (the modularity guard)

Lhassa must stay **fully elastic and highly modular**: clean layering that does
**not** erode over time. This document describes the layers and the forbidden
import directions between them. The rules are enforced mechanically by
`tests/test_architecture.py` — a dependency-free pytest that parses every
`lhassa/**/*.py` with the standard-library `ast` module and fails if a future
change crosses a boundary. The rules were *measured from the current tree*, so
they pass today and only trip on a **new** violation.

## The layers

From the bottom (depends on nothing) to the top (the world-facing surfaces):

| Layer | Subpackages | Role |
| --- | --- | --- |
| **Leaf infrastructure** | `registry.py`, `plugins.py` (and `config.py`) | Generic, reusable machinery. Imports no feature code. |
| **Spine** | `core/` | The do-and-sleep loop (`step`, `heartbeat`, `episode`, `db`). Driven *by* the shells; never reaches up to them. |
| **Interfaces (mid)** | `memory/`, `security/`, `llm/` | Reusable contracts (typed facts, trust/approval, model provider + router). Usable by any shell; depend on none. |
| **Adapters (the hands)** | `harness/`, `tools/`, `agents/`, `remote/`, `health/` | The rentable "body" — harnesses, MCP/tools, specialist agents, the remote local-agent. |
| **Shells (outer)** | `cli/`, `api/`, `voice/`, `notify/`, `org/`, `dashboard/` (JS) | The user/world surfaces. Allowed to compose everything below them. |

Principle: **inner layers never depend on outer shells.** Dependencies point
"down" only. A shell may import the spine, interfaces and adapters; the spine and
the interfaces must not import a shell.

## Forbidden directions (encoded today)

1. **spine-isolation** — `core/` must not import any shell
   (`cli`, `api`, `voice`, `notify`, `org`, `dashboard`).
2. **interface-isolation** — `memory/`, `security/`, `llm/` must not import any
   shell.
3. **adapter-isolation** — `harness/`, `tools/`, `agents/`, `remote/`, `health/`
   must not import any shell (see the one whitelisted exception below).
4. **swap-axis-independence** — `harness/` (the agentic-body axis) must not
   import `llm/` (the think-directly model-provider axis). The two swap axes stay
   independent. The reverse edge — `llm/router.py` *choosing* a harness — is the
   intentional bridge seam and is **not** forbidden.
5. **keystone-leaf** — `registry.py` and `plugins.py` must not import any
   `lhassa.*` feature module (they are bottom-of-stack infrastructure).

## Allowed exceptions

Precise, intentional edges that are whitelisted in the test (keyed on the exact
`source-file → imported-module` pair, so they do not loosen the rest of the rule):

- `tools/monitor.py` and `tools/registry.py` → `lhassa.voice.notifier` —
  `voice/notifier.py` is the cross-platform **desktop notification router**
  (`notify-send` / `osascript` / BurntToast), a shared utility, **not** the voice
  conversation shell. Tools use it to surface alerts. The rest of the `voice`
  package (`voice.pipeline`, `voice.brain_bridge`, …) stays forbidden to adapters.
- `plugins.py` → `lhassa.config` — `config` is itself leaf infrastructure (pure
  configuration, not a feature module), so the plugin loader reading `Config`
  does not break the keystone rule.

## How to evolve the rules

If you intentionally add a new allowed edge, add a precise entry to
`ALLOWED_EXCEPTIONS` in `tests/test_architecture.py` **with a comment explaining
why**. Prefer fixing the layering over adding an exception. If you add a new
subpackage, slot it into the correct layer (and the rule sets) so the guard keeps
covering it — `test_rules_reference_real_subpackages` guards against typos.
