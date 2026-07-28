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
| Interfaces | `memory/`, `security/`, `llm/`, `settings.py` | Reusable contracts: typed facts, trust/approval, `ModelProvider` + router, the editable-settings service |
| Adapters (hands) | `harness/`, `tools/`, `agents/`, `skills/`, `remote/`, `health/` | The rentable body: harnesses, MCP/tools, specialist agents, first-class skills, the check-in local agent |
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
| `~/.lhassa/llm_gateway.db` | The cost gateway's ledger (per-call tokens/cost, the budget/kill-switch state) |
| `~/.lhassa/backups/` | WAL-safe online backups |
| `~/.lhassa/keys/ed25519` | Signing key for `Task` provenance |
| `~/.lhassa/settings.env` | Runtime overrides from `lhassa config` / the Settings panel (loaded after `.env`, override on; **no secrets**) |
| `~/.lhassa/org.json` | The org command tree (1..N brains, Lhassa the root admin) — `LHASSA_ORG_TOPOLOGY_FILE` |
| `~/.lhassa/relationships.json` | Typed relationship **overlay** on the tree (reads/notifies/reviews/collaborates) — `LHASSA_ORG_RELATIONSHIPS_FILE` |
| `~/.lhassa/flows.json` | Work-flow graphs (trigger→task→gate→output + branch) — `LHASSA_FLOWS_FILE` |
| `~/.lhassa/skills/` | First-class skills (`SKILL.md` per skill: frontmatter + instructions) |
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
                                      │  (re-reads .env + settings.env)
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
    claude · codex (subscription CLIs) · zen      pick_harness = cheapest available
    [PseudonymizingProvider wraps remote]         (the two axes stay independent)

  Two model seats (set live via `lhassa config` / Settings → settings.env):
    chat  = llm_provider/model     (CLI / voice / agent; API key OR a local CLI)
    beat  = heartbeat_provider     (routine think; fallback opencode→claude→codex→cloud)

  Surfaces (shells) compose the above:
    CLI (incl. `code` → agentic local CLI) · Telegram (notify) ·
    dashboard + API (:7766, Lhasa identity) · voice
    dashboard sections: Overview · Brains (Flow/Tree/Templates + relationship
      overlay) · Skills · Run (agentic) · Flows (work-flow editor) · Projects ·
      Settings · Reports · Fleet · Activity

  Org: a command TREE + a typed RELATIONSHIP overlay (1..N brains)
                  lhassa  (root administrator — never removed/demoted)
                       │
                    general  (coordinates, never executes)
                  /          |              \
        optimization    documentation     finance      ← leaves execute
            (TASK_ASSIGN down · only leaves run · TASK_RESULT bubbles up)
        overlay edges (not hierarchy): reads · notifies · reviews · collaborates
        skills: a NAME on a brain → a SKILL.md → loaded into its think-time prompt

  Work-FLOWS: a DAG wiring  trigger → task → gate → output  (+ branch)
        task nodes route HYBRID (pin a brain, else auto-route by skill via the
        org topology); the engine threads data along the edges; a gate is
        verify/approval; runs are offline/stubbed unless `LHASSA_ORG_LIVE`.

  Agentic RUN: POST /api/code {task, cli?} → a local CLI (claude/codex/opencode)
        streamed over /ws (agent_started/agent_token/agent_done). CLI: `lhassa code`.

  API routes (FastAPI, :7766) include — besides the brain/LLM/approval surface —
    /api/org/* (topology · brains · route · templates · relationships) ·
    /api/flows[/{id}[/run]] · /api/code (+ /clis, /runs) · /api/skills/* ·
    /api/projects[/{id}[/runs|artifacts|episodes]] · /api/config
```

Everything that can be swapped sits behind a `Registry` (`registry.py`) and can be
added by dropping a self-registering module into `plugins_dir` or a `lhassa.plugins`
entry point (`plugins.py`) — no core edit. `lhassa capabilities` (alias `doctor`)
prints the whole wired / on-off picture.

### The delivery chain (sender of record)

Work goes DOWN the tree through `org/delegate.py`; whatever a leaf produces comes
back UP through its exact mirror, `org/deliver.py`. A department that finishes a
work product — a tailored CV (`.tex` + compiled `.pdf`), a report, any file the
owner ends up receiving — doesn't send it itself: `deliver()` walks the SAME
command chain the down-hop used, and every brain on it (the department, any
intermediate coordinator, the root) logs its own `org-deliver` Episode before
Lhassa, the permanent root administrator, actually sends. One append-only row per
delivered file lands in the `artifacts` registry (`core/artifacts.py` —
`GET /api/artifacts[/{id}]`, `lhassa artifacts list|show`), carrying its paths,
free-form metadata, origin department, and the episode it travelled with. The
golden rule mirrors `delegate`'s: **audit is best-effort, delivery is not** — a
broken topology or a failed episode write degrades to a single root-attributed
hop and is logged, never blocks the send; the only thing that can make `deliver()`
report failure is the send itself.

### The temple (`temple`) — Lhassa's inner workshop, and its monks

`temple` is a department seeded directly under Lhassa (`seeds/temple.py`, `lhassa
seed apply temple`) — distinct from `interns` (the CV pipeline's workers) and
`personal` (the owner's own department) — that owns Lhassa's INTERNAL functions:
docs, internal creation (tools/skills/flows authoring, the consciousness
proposals), synchronization (repo/calendar/memory), and self-maintenance.

The same seed also creates temple's five leaf WORKER brains — its **monks**, each
owning a sub-domain: `scribe` (docs), `artificer` (internal creation — tools/
skills/flows authoring), `synchronizer` (sync — repo/calendar/memory), `guardian`
(self-maintenance) and `oracle` (the consciousness). `org/temple.py:ACTION_OWNERS`
is the deterministic action→monk ownership map for the maintenance seed's
hygiene actions (a representative subset shown below — the map has grown
past this list without every addition being echoed here):

| action | owning monk |
|---|---|
| `distill_memory` | `guardian` |
| `review_tools` | `guardian` |
| `curate_skills` | `guardian` |
| `detect_repetition` | `oracle` |
| `calendar_sync` | `synchronizer` |
| `kg_sync` | `guardian` |

An action absent from the map, or whose resolved worker is missing from the
topology, falls back to `temple` itself — the department is always someone.

`org/temple.py:delegate_internal` runs the SAME down/up shape as the delivery
chain above, but for internal work instead of a work product, now with the
worker resolved FIRST: one `org-delegate` Episode logs Lhassa delegating an
action DOWN the path `lhassa → temple → <worker>` (`resolve_worker` picks the
monk, `command_path` walks the topology), the monk then EXECUTES the action via
`core/actions.run` with its own Episode attributed `dominio=<worker>`, and the
result travels back UP via `deliver(origin_department=<worker>)` — the per-brain
hops walk worker → temple → Lhassa.

This is the first instance of an org-wide pattern: **every tool reports to its
brain.** The worker's execution Episode already names the tool (`accion`) and its
outcome (`resultado`); when a real monk ran it (not the bare department), the
`resultado` also states it reports up through `temple` — so the trail reads
"guardian ran curate_skills → 3 stale skills archived → reports ↑ temple" from the
tool's own log line, before the deliver seam's per-brain hops even start.

The `maintenance` seed's five hygiene actions now mint `temple_run` (a closed
allowlisted wrapper, `actionpack/_temple.py`) instead of running bare, so every
beat carries the full hop and worker attribution instead of an unattributed
heartbeat action. The Telegram `/temple <instruction>` door keyword-matches a
known action (LLM-free) or falls through to `org_run(department="temple")` for
free-form internal work.

## Beyond phase 7 — product/hardening

Continuing past the phase plan: a keystone `Registry` + plugin loader adopted at
every swap point; **first-class skills** (3-layer: a name on a brain → a `SKILL.md`
→ loaded into its prompt; AI create/improve + an offline recommend catalog); **org
management** with Lhassa as the permanent root administrator, use-case **templates**
(code-review, bug-investigation, refactor-migration, incident-response, the
multi-level software-factory); a **settings service** (`settings.py` +
`~/.lhassa/settings.env`, the dashboard Settings panel + `lhassa config`); notify
**channels** + slash-**commands** registries with a live Telegram bot; and the Lhasa
visual identity (gold/red/white/green, a stupa mark) shared by the CLI and dashboard.

Three further surfaces landed on top of the org layer (dashboard + API, no dedicated
CLI subcommand yet for the first two):

- **Typed relationship overlay** (`org/relationships.py` + `api/relationship_routes.py`)
  — a directed OVERLAY graph layered on the command tree, stored separately at
  `~/.lhassa/relationships.json` (`LHASSA_ORG_RELATIONSHIPS_FILE`) so hierarchy and
  relationships never tangle. A closed kind vocabulary: `reads` / `notifies` /
  `reviews` (directed A→B) and `collaborates` (symmetric, stored canonically).
  `permits(from, to, kind)` is the pure runtime read seam ("may A read B?"). API:
  `GET /api/org/relationships`, `GET /api/org/relationships/kinds`,
  `POST /api/org/relationships {from,to,kind,note?}`,
  `DELETE /api/org/relationships {from,to,kind}` — every mutating op validates (real
  nodes, known kind, no self-edge) before it writes. On the dashboard the Brains
  canvas draws the edges as a dashed per-kind colored overlay (legend + toggles) and
  the inspector adds/removes them.
- **Work-flows** (`lhassa/flows/` model/store/engine + `api/flow_routes.py`) — an
  n8n-style DAG that wires `trigger → task → gate → output` (+ `branch`), persisted to
  `~/.lhassa/flows.json` (`LHASSA_FLOWS_FILE`). Task nodes use HYBRID routing (pin a
  brain via `config.brain`, else auto-route by skill through the org topology); the
  engine walks from the single trigger and threads each node's output along the edges;
  a gate is verify/approval; a non-passing gate halts the path. Runs are
  offline/stubbed unless `LHASSA_ORG_LIVE`. API: `GET/POST /api/flows`,
  `GET/PUT/PATCH/DELETE /api/flows/{id}`, `POST /api/flows/{id}/run`. The dashboard
  has a "Flows" editor section (the n8n-style canvas) driving this API. The
  `personal` seed is the canonical example of a stable department with multiple
  time-specific flows: `personal-morning-briefing` and
  `personal-evening-briefing` both pin work to the `personal` brain, while their
  DAG order differs for planning versus review.
- **Agentic run surface** (`api/code_routes.py` + `agents/local_cli.py`) —
  `POST /api/code {task, cli?}` launches an agentic task through a local CLI
  (`claude` / `codex` / `opencode`, on subscription, no key) and streams it over the
  WebSocket as `agent_started` / `agent_token` / `agent_done`; `GET /api/code/clis`
  and `GET /api/code/runs` list the installed CLIs and recent runs. The dashboard
  "Run" section is the UI; the CLI equivalent is `lhassa code "<task>"`. The
  `lhassa-app` service adds `~/.local/bin` to PATH so it finds the `claude` CLI.
- **Projects** (`core/projects.py` + `api/project_routes.py`) — the first-class
  workspace entity, scoped through existing tables via one namespaced-string
  convention (`entity_tag`) rather than new columns. `kind` is a policy key: it
  maps to an org template (`org/templates.py`, overridable via
  `LHASSA_KIND_TEMPLATES`) that a $0 watchlist sense (`actionpack/_standing.py`)
  proposes attaching — gated unless the template is provably read-only. A second
  sense (`actionpack/_projects.py`) detects an unregistered repo on
  `LHASSA_AUTO_SYNC_REPOS` and proposes a project with `MUNDO` origin — detection
  never creates silently. `lhassa project create|list|show|archive|runs|artifacts`
  is the CLI door; `ProjectsPanel.jsx` (dashboard, "Projects" section) is
  read-only. Runs/tasks scoped to a project render an **evidence rung**
  (`memory/evidence.py`: `prepared` / `observed` / `verified`), a pure derived
  read with no new storage.
- **The persisted knowledge graph** (`memory/kg.py` + `memory/graph_retrieve.py`,
  opt-in via `LHASSA_MEMORY_GRAPH`) — a durable accumulation of the same
  entity/co-occurrence extraction `memory/graph.py`'s on-the-fly read-model
  already does, into two new grande tables (`kg_entities`/`kg_edges`, a closed
  edge-kind vocabulary). `kg_sync` (owned by the `guardian` monk, weekly) is the
  $0 incremental feeder; `graph_retrieve.retrieve()` is vector-seeded, 1-hop
  graph-expanded fact retrieval, wired into `memory/load.py` as its lowest-
  priority `graph` context layer (see `doc/memory.md`).
</content>
