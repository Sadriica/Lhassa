# Plan 4 — From a personal brain to a system people use

## Context

Lhassa works as one owner's personal brain. The ask is now different:
**researchers, developers and knowledge workers** should be able to install it,
grant it permissions, and build their own execution flows, tests and
automations — a simple surface over a large capability, without losing the
Witara/Lhassa philosophy (*counsel, not control*; sovereignty; $0 by default;
thinking is the exception).

This plan starts from a verified map of the current state. Three findings shape
the work:

1. **Installing does not produce a working system.** Seeds are **never applied
   automatically** (`apply_all` is called only from `cli/seed_cmds.py`). Without
   `lhassa seed apply-all` there are no departments, no digest, no maintenance
   and no flows — only a bare chat. It is **8 manual steps** from `git clone`.
2. **The voice never touches the persona.** `notify/voice.py` is prefixes plus
   `k=v` tidying; it never calls `persona.py`, and the lead phrases are
   **duplicated** across both. Persona exists only as an opt-in model pass that
   is off by default. The phrasebook covers **9 actions** out of ~72 tools, and
   `deliver.py` with a `document` **bypasses the voice entirely**.
3. **The flow engine is a sequential DAG.** No loops, no parallelism, no
   sub-flows, no retries (`WAITING_RETRY` exists and is never entered), no typed
   I/O, no versioning. The **durability, by contrast, is excellent**: CAS,
   leases, fencing tokens, per-node checkpoints, exact resume, idempotency keys.

Factual correction to a common belief: **the TUI does exist** (`lhassa tui`,
Textual, 4 screens) — but it covers 4 of ~18 CLI domains and is read-mostly.

## Decisions

| | |
|---|---|
| Audience | All three profiles: a common core plus three packages on top |
| Install | One command plus an interactive wizard |
| Interfaces | All four level up: dashboard, TUI, CLI, messages |
| Multi-user | **Both shapes, starting with one brain per person** |
| Execution | Subagents in parallel batches |

---

## Stream 0 — Foundations (BLOCKING)

A **verified, still-open critical**: the heartbeat runs fixed actions without
crossing `security/allowlist.py:allow()` — `objetivos` and `min_trust` are
unenforced on the dominant path (`core/step.py` calls `actions.run` directly).
With a single owner the blast radius is small. **Multi-user removes that
mitigation**: a second actor plus an allowlist nobody consults is no permission
boundary at all.

1. Route `core/step.py`'s fixed-action branch through `allow()` with a signed
   Task, as `agents/ptc.py` and `remote/local_agent.py` already do. Care: the
   sensor-generated triggers (`repo_changed` → `sync_repo`) must keep working.
2. Extend the registry/allowlist parity test to `objetivos` **actually
   enforced**, not merely declared.
3. Close the four missing phrasings and the `deliver.py` document bypass.

**DoD**: a `sync_repo` outside `LHASSA_ALLOWLIST_REPOS` is refused by the
heartbeat, with an honest episode.

---

## Stream 1 — The versatile brain (modes and effort levels)

The seed already exists: `core/policy.py` returns a `PolicyDecision` with
`action`/`skip`/`think`, a `context_tier` (`minimal` ≈600tk / `full` ≈2000tk)
and a `hard` flag. Today that is a hidden binary. It becomes the explicit model
of how work is done. All three axes are deterministic and $0.

- **Task identification.** Grow `notify/intent.py` (today: command / live_data /
  cacheable / judgment) into a classifier of *work type*: question, research,
  build, review, maintenance, decision. Regex plus episode signals, no model.
- **Working mode.** Each type maps to a registered way of working: answer
  directly · search and cite · iterate with a verifier · delegate to the
  hierarchy · open a flow. A pluggable registry (`@register_mode`), like every
  other swap point.
- **Effort level.** `minimal` / `standard` / `deep` / `exhaustive`, governing
  token budget, loop turns, how many verifiers run, whether a MoA panel opens.
  Settable per message (`--deep`), per task type, or left to policy.

This is what lets a researcher say "this deserves exhaustive effort" and a
developer say "just answer me" on the same brain — and it keeps the invariant:
effort is **declared**, not discovered by spending.

**DoD**: the same request at `minimal` and at `deep` produces visibly different
trajectories in the episode, with cost declared before it is spent.

---

## Stream 2 — Flow engine v2 ("graph of loops")

**2.1 De-Lhassa-ify first (zero behavioural risk).** Three seams become
registries whose default IS today's behaviour:
- `task` routing → `TASK_ROUTERS` (default `org_topology`)
- `{base_cv}`/`{company}` → context providers (default `cv`)
- `output: tex` → `OUTPUT_HANDLERS` (default `tex` → `lhassa.cv.export`)

The CV and research flows keep running **unchanged**.

**2.2 Retries and timeouts.** `WAITING_RETRY` stops being dead scaffolding: the
status, the transition, the `claim_run` that already wakes on `next_wakeup_at`
and the `flow_step_attempts` table **all exist**. Only `engine.py` wiring is
missing. Per-node timeout rides the same path (`retry_on: timeout`).

**2.3 New vocabulary.** `LOOP` (bounded, verifier-terminated — `max_iterations`
mandatory, mirroring `agents/author.py`), `WAIT` (external event; generalizes
the approval park/resume), `SUBFLOW` (composition).

**2.4 Parallelism.** `MAP` / `JOIN`. The cursor moves from
`frontier: list[(node, data)]` to entries carrying `branch_id` and `attempt`;
`visited` becomes composite keys. All of it lives inside JSON blobs the DB never
interprets: **zero schema migration**.

**2.5 Typed I/O and versioning.** Optional `input_schema`/`output_schema`
validated by a minimal structural check (not a type system), plus `version` on
the flow. In-flight runs are already pinned by `flow_snapshot`.

**DoD**: a researcher builds a flow that iterates until a verifier passes, runs
in parallel over N inputs, crashes midway, and `resume` picks it up exactly.

---

## Stream 3 — Install and grant permissions

`lhassa setup` — one command, one wizard:

1. Detect the host (`host.py` already exists: Linux tier 1 / macOS tier 2).
2. Detect or configure a brain (API key or local Ollama).
3. **Ask for permissions by scope, explained in plain language**: read these
   directories, run these commands, reach the network, notify. Each scope writes
   a real allowlist entry plus optional sudoers. Never all-or-nothing.
4. **Apply seeds automatically** for the chosen profile.
5. Install services (systemd or launchd — both already exist).
6. Run a smoke check and **say in one sentence what is now alive**.

**DoD**: from `git clone` to a brain with departments, digest, maintenance and
flows in one command, with no hidden manual steps.

---

## Stream 4 — A real voice ($0, no AI)

Root cause, verified: the deterministic plane never consults `persona.py`.

1. **One source of voice**: `persona.py` exposes the vocabulary, `voice.py`
   consumes it. The duplicated lead phrases go away.
2. **The phrasebook becomes an obligation**: every allowlisted action needs a
   phrasing or a test fails (the parity pattern already proven).
3. **Identity reaches the $0 plane**: the owner's language, tone and limits
   apply without a model.
4. **Close the `deliver.py` document bypass.**
5. **Templates by intent**, not only by kind.

**DoD**: no action delivers machine text; a test prevents it.

---

## Stream 5 — The four interfaces

- **TUI**: from 4 screens to a real app — command palette, search, live updates,
  screens for org/skills/flows/memory/config, keyboard help.
- **Dashboard**: a real typed API client (`lib/api.js` is 10 lines today) and
  coverage for the orphaned endpoints (artifacts, integrity, memory graph,
  tasks, flow-runs, research assessments, episodes/search). The canvas gains the
  v2 node kinds.
- **CLI**: a full `lhassa flow` (create/edit/run/watch/logs), `lhassa init` to
  scaffold, human-readable output everywhere.
- **Messages**: Stream 4.

**DoD**: anything doable in the dashboard is doable in CLI and TUI; no endpoint
without a surface.

---

## Stream 6 — Multi-user (depends on Stream 0)

**Phase A — one brain per person, shared resources** (preserves the philosophy;
the org layer already models 1..N brains):
- `actor_id` as a first-class concept alongside `instance_id`.
- Skills, flows, templates and knowledge shared across instances; memory and
  identity stay private.
- Discovery and cross-instance permissions over `org/relationships.py`.

**Phase B — a shared team instance** (later):
- Per-actor auth (sessions, tokens, scopes) beyond `require_local_or_token`.
- Per-actor RBAC reusing `org/rbac.py`.
- **Approvals that mean something**: proposer ≠ approver becomes the rule rather
  than a defensive check.

---

## Stream 7 — The three packages

Presets on top of the core — one seed, flow templates and a screen each, not a
fork:

- **Research**: experiment flows, bench as a gate, evidence custody, cited
  corpora, exports (most of it exists; the packaging does not).
- **Dev**: read the repo/system, agentic runs, tests, automations, context packs.
- **Knowledge**: ingestion, graph, sourced notes, reviewed publication.

---

## Sequence

```
Stream 0 ──┬─→ Stream 1 (versatile brain) ─────────────┐
           ├─→ Stream 2 (engine: 2.1→2.2→2.3→2.4→2.5) ─┼─→ Stream 5 ─→ Stream 7
           ├─→ Stream 3 (install)                      │
           ├─→ Stream 4 (voice) ───────────────────────┘
           └─→ Stream 6 (multi-user, phase A)
```

1, 3, 4 and 6A parallelize after 0. Stream 2 is internally sequential.

## Verification

- Full suite green per stream (today: 2375).
- Invariant tests, not only happy paths.
- **A live test is mandatory before every merge.** This is not ceremony: in one
  session the suite missed a delivered-message stutter, test runs firing real
  desktop toasts at the owner, and a skill-name path traversal that could delete
  `~/.lhassa`. Green tests prove someone thought to check something, nothing more.
- A dated entry in `doc/decisions.md`, in English.
- One branch per stream; commits in English.

## Real design risks (not build tasks)

1. **Loops vs. acyclic validation** — which back-edges are legal when a `LOOP`
   body contains a `BRANCH` or a nested `LOOP`.
2. **Checkpoint granularity under parallelism** — the design trades per-node for
   per-batch checkpointing inside a parallel region. That needs either an
   idempotency contract on every executor, or a policy that a partially
   completed batch fails the run rather than silently re-running.
3. **`SUBFLOW` and the hierarchy** — whether the parent's delegation absorbs the
   child's or the child logs its own nested episode. A governance decision.
4. **`JOIN` under partial failure** — in `any`/`count` mode, what happens to
   branches that never arrived: cancel, orphan or discard. An orphaned branch
   still calling a paid brain is a leak in the $0 invariant.
