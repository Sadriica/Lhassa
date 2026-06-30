# Lhassa — architecture overview

A current, high-level map of the Lhassa "brain". For the full spec read
[`lhassa-brief.md`](lhassa-brief.md); for the phase-by-phase decisions read
[`decisions.md`](decisions.md); for the mechanically-enforced layer rules read
[`architecture-layers.md`](architecture-layers.md).

## What Lhassa is

An identity-first core (a JARVIS) that runs the **do-and-sleep** loop — state on
disk + a `step()` that advances it one tick, woken by a systemd **heartbeat** or
the API. It **orchestrates** models and tools behind two stable interfaces rather
than depending on any one of them: *build the brain, rent the body*. It is
local-first, **elastic and modular** (every swap point behind a registry;
capabilities drop in without editing core), and scales from one brain to an org of
1..N brains.

## Layers

Dependencies point **down** only — inner layers never import an outer shell. The
guard test (`tests/test_architecture.py`) fails on any new layer-crossing import.

| Layer | Subpackages | Role |
|---|---|---|
| Leaf infra | `registry.py`, `plugins.py`, `config.py` | Generic machinery; imports no feature code |
| Spine | `core/` | The do-and-sleep loop: `step`, `heartbeat`, `episode`, `db`, `watchlist`, `actions`, `task` |
| Interfaces | `memory/`, `security/`, `llm/` | Reusable contracts: typed facts, trust/approval, `ModelProvider` + router |
| Adapters (hands) | `harness/`, `tools/`, `agents/`, `remote/`, `health/` | The rentable body: harnesses, MCP/tools, specialist agents, the check-in local agent |
| Shells (outer) | `cli/`, `api/`, `voice/`, `notify/`, `org/`, dashboard (JS) | World-facing surfaces; compose everything below |

The two swap axes are kept **independent**: `harness/` (agentic body) must not
import `llm/` (think-directly providers). The reverse edge — `llm/router.py`
*choosing* a harness — is the one intentional bridge.

## Data dir — `~/.lhassa/`

Every default path derives from `Path.home()`, so pointing `HOME` relocates all
state (the Docker image sets `HOME=/data`).

| Path | Contents |
|---|---|
| `~/.lhassa/brain.db` | The "grande" — append-only SQLite (WAL): facts, episodes, jobs, approvals, org tables, … |
| `~/.lhassa/chica.db` | The "chica" — persistent working cache (LRU) |
| `~/.lhassa/backups/` | WAL-safe online backups |
| `~/.lhassa/keys/ed25519` | Signing key for `Task` provenance |
| `~/.lhassa/org.json` | The org topology (1..N brains) |
| `~/.lhassa/plugins/` | Drop-in plugin modules (self-register) |
| `~/.lhassa/tools/` | Local-git repo for self-written tool versioning |
| `~/.lhassa/lhassa.log` | Rotated application log |
| `~/.config/lhassa/mcp.json` | MCP server config (optional) |

## Phase map (0–7)

Built additively over an existing skeleton (extend, never rewrite). From
`decisions.md`:

| Phase | Title | One line |
|---|---|---|
| 0 | The spine | `instance_id` everywhere, SQLite "grande", structured episodes, the deterministic `step()`/watchlist/actions loop, Ed25519-signed tasks + deny-by-default allowlist, check-in/report, heartbeat + systemd + parametrized identity |
| 1 | Memory | Typed facts (`dato`/`directiva`) in the grande, layered `load()`, seed facts; the chica working cache (threshold-LRU) + WAL-safe backup/restore |
| 2 | Economy + router | `ModelProvider` interface + `GatewayProvider`, the two-axis router (cascade cheap→verify→escalate), gateway kill-switch + daily budget + ccusage ledger, semantic consolidation |
| 3 | The árbitro | Objective verifiers (never call an LLM) wired into the judgment branch; HITL-per-flow approval + the autonomy streak knob |
| 4 | The hands | `HarnessAdapter` (OpenCode/Claude Code/Codex) + runner/supervisor + `pick_harness`; MCP client + a typed task (`tracking_postulaciones`) |
| 5 | Self-improvement (caged) | Quarantine sandbox + the four gates (segura/correcta/mejor/retiro); specialists as **skills not fine-tuning** + local-git tool versioning |
| 6 | Maturity (continuous) | `LocalModelProvider` + PII pseudonymization + router routine→local; memory **layer 4** (semantic embeddings); CLI power subcommands + Telegram + opencode heartbeat + voice brain-bridge |
| 7 | Org layer | Typed inter-brain messages, read-only commons, RBAC, `GeneralBrain` (coordinates) / `DepartmentBrain` (executes), the validated topology tree (1..N brains) + coordinator/routing/executor + `lhassa org` |

## Diagram — the loop, the seams, the tree

```
        systemd heartbeat timer ──► one fresh process per beat
                                      │  (re-reads .env)
                                      ▼
   ┌──────────────────────────  core.step()  ───────────────────────────┐
   │  watchlist (0 tokens) ─► fixed action ─► structured episode (instance_id)
   │                       └─► else: judgment / think ─────────┐         │
   └────────────────────────────────────────────────────────────┼────────┘
                                                                 ▼
                      memory.load(context)  ──►  llm.router  ──► pick
                                                       │
        ┌──────────────────────────────────────────────┴──────────────────┐
        ▼                                                                  ▼
  ModelProvider (axis 1: think directly)        HarnessAdapter (axis 2: agentic body)
  PROVIDERS registry:                           ADAPTERS registry:
    default (gateway) · local · opencode          opencode · claude-code · codex
    claude · codex · zen(stub)                    pick_harness = cheapest available
    [PseudonymizingProvider wraps remote]         (the two axes stay independent)

  Surfaces (shells) compose the above:
    CLI · Telegram (notify) · dashboard + API (:7766) · voice

  Org topology (1..N brains, who-commands-whom):
                       general  (coordinates, never executes)
                      /          |              \
            optimizacion    documentacion     finanzas     ← leaves execute
            (TASK_ASSIGN down · only leaves run · TASK_RESULT bubbles up)
```

Everything that can be swapped sits behind a `Registry` (`registry.py`) and can be
added by dropping a self-registering module into `plugins_dir` or a `lhassa.plugins`
entry point (`plugins.py`) — no core edit. `lhassa capabilities` (alias `doctor`)
prints the whole wired / on-off picture.
</content>
