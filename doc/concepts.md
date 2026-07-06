# Concepts — why Lhassa is built this way

This is the *why* document. For the *how* — layers, data paths, module map — read
[`architecture.md`](architecture.md); for the phase-by-phase build log read
[`decisions.md`](decisions.md); for the full spec read [`lhassa-brief.md`](lhassa-brief.md)
(Spanish, living doc). Getting the brain running is [`getting-started.md`](getting-started.md).

## The thesis: build the brain, rent the body

Lhassa's one hammered claim is **build the brain, rent the body**. The distinction is
between what is *durable* and what is *rented by the hour*:

- **Durable** — identity (the persona and its directives, `lhassa/persona.py`), memory
  (`memory/`), judgment (the deterministic policy + router path), and the trust record
  (`security/` — an append-only ledger of what was allowed, approved, denied). This is
  *who Lhassa is*, and it never depends on which model happens to answer today.
- **Rented** — the models (`llm/provider.py`'s `ModelProvider` registry: gateway, local,
  opencode, claude, codex, zen) and the agentic harnesses (`harness/adapter.py`'s
  `HarnessAdapter` registry: OpenCode, Claude Code, Codex). Both sit behind a registry
  (`registry.py`) so a new one drops in with `@register_provider("x")` /
  `@register_adapter("x")` — no core edit, no surgery to swap a model out from under
  the brain.

The practical result: `core/step.py` and `memory/load.py` never import a specific model
name. They import an *interface*. Swapping Claude for a local Llama, or a paid API for a
CLI subscription, is a config change (`llm_provider`, `heartbeat_provider`), not a
rewrite. The brain is the product; the body is plumbing.

## Do-and-sleep: the heartbeat, the watchlist, the reflexes

Lhassa is **state on disk + a `step()` function** (`core/step.py`) that advances it one
tick and returns. Nothing runs continuously in the foreground — a systemd timer
(`scripts/systemd/lhassa-heartbeat.timer`) fires `lhassa-heartbeat.service` every 2
minutes (`OnUnitActiveSec=2min`, `AccuracySec=15s`), and each firing is a **fresh, one-shot
process** (`Type=oneshot`) that re-reads `.env`/`settings.env`, calls
`core/heartbeat.py:beat()`, and exits. There is no long-lived daemon holding state in
memory between beats — the state is the SQLite "grande" (`~/.lhassa/brain.db`), and the
process is disposable.

Inside one tick, three deterministic layers run **before** a single token is spent:

1. **The watchlist** (`core/watchlist.check()`) — zero tokens. It looks for triggers:
   a due task, a finished job, a repo change, a health signal. No triggers → the brain
   sleeps, no episode is even written.
2. **Fixed actions** (`core/actions.py`) — if a trigger maps directly to a deterministic
   action (`trigger.maps_to_fixed_action()`), it just runs. Still zero tokens.
3. **Reflexes** (`core/policy.py`) — for everything else, `policy.evaluate(trigger)`
   inspects the trigger's *content*, not just its shape, and returns a `PolicyDecision`
   before the judgment path even loads memory. A reflex is a pure function
   `(Trigger) -> PolicyDecision | None`; the first to match wins, in a registry that
   mirrors `core/actions.ACTIONS`. Three built-ins ship today:
   - `_job_review` — a job that finished cleanly (`estado in ("done","success","ok")`,
     no error signal, cost ≤ `$0.01`) is auto-triaged (`kind="skip"`) — logged, no model
     touched. A failed or costly job still gets judgment.
   - `_repo_changed` — a repo on `LHASSA_AUTO_SYNC_REPOS` gets `sync_repo` run directly
     (`kind="action"`); anything else falls to judgment.
   - `_health_alert` — a `"warn"`-level health digest is just notified
     (`kind="action"`, the `notify` action); only `"alert"`-level needs judgment, and it
     is marked `hard=True` so the smart model handles it.
   - `_default` — the catch-all: `kind="think"`, `context_tier="minimal"`, not hard.

   `policy.evaluate()` **never raises**: a broken reflex is skipped and logged at debug,
   and a total failure degrades to the same safe default the catch-all returns.

Only when a reflex actually returns `kind="think"` does `llm/router.py:decide()` pick a
provider and `memory/load.py:load()` build a context — and even then, the reflex already
decided how *much* context to load (`context_tier`: `"minimal"` ≈600 tokens / `"full"` ≈2000
tokens, via `config.context_token_budget_minimal` / `config.context_token_budget`) and
whether this judgment is hard enough to deserve the smart model (`hard=True`).

**The economics.** A routine day is ≈720 heartbeats (2-minute cadence × 24h). The
overwhelming majority resolve at the watchlist (nothing to do) or a fixed action/reflex
(zero tokens). A handful of triggers — a handful, not hundreds — actually reach judgment,
and even those default to the cheap model unless a reflex or an escalation marks them
`hard`. The digest and the ledger (`llm/gateway.py`'s ledger, `check_spend` action,
`/api/llm/ledger`) turn this into a number, not an adjective: a normal day's total spend
sits in **fractions of a cent**. Thinking is the exception, budgeted and logged; sleeping
is $0 and the default.

If judgment can't run — no configured provider (`ProviderNotConfigured`) or the daily
budget is spent (`BudgetExceeded`) — `core/step.py:_judge()` walks the fallback chain
(below) and, failing everything, **degrades the episode to `"deferred"` rather than
crashing the tick**. `db.sweep_deferred_tasks()` retries deferred tasks with backoff each
subsequent beat, up to `LHASSA_DEFERRED_MAX_RETRIES`, then marks them `rechazada`
(rejected) — a tick never throws away work silently, and it never blocks on it either.

Long agentic work (a harness run, not a think) is handled the same do-and-sleep way:
`router.decide()` can return `kind="delegate"`, `core/step.py:_delegate()` launches the
harness as a **separate process** (`harness/runner.launch`) and returns immediately — the
episode records the job id and PID, and a *later* tick's `runner.supervise()` reaps the
result from disk. `step()` never blocks on a job.

## The two model seats

Lhassa keeps two model seats deliberately distinct, because "chat" and "routine thinking"
have different cost profiles and different owners:

- **Chat** — the CLI, voice, and the built-in agent use `llm_provider` + `model`. This can
  be a metered API (Anthropic/OpenAI/Groq/OpenRouter/Cerebras, one key is enough to
  auto-detect) *or* a $0 CLI subscription already paid for (`claude`, `codex`, `opencode`)
  *or* `ollama` for a fully local model.
- **Heartbeat** — routine, unattended thinking uses `heartbeat_provider` independently
  (`auto` / `local` / `cloud` / `opencode` / `claude` / `codex`). `llm/router.py:pick_provider()`
  makes the routine (non-`hard`) path prefer the **local** model when `local_model_url` is
  set, else the `opencode` CLI when it's on `PATH`, before ever reaching for the cloud
  gateway — the owner's already-paid subscriptions come first.

Both seats share one **fallback chain** (`llm/router.py:fallback_chain()`, config
`fallback_providers`, default `"claude,codex,default"`): when the primary provider throws
`ProviderNotConfigured` or `BudgetExceeded`, the chain is walked in order, skipping any CLI
whose binary isn't installed (`shutil.which`) and never retrying the same provider kind
twice. This is the concrete meaning of "subscriptions before APIs" — `opencode → claude →
codex → cloud` — and it's the same chain `core/step.py`'s judgment path and the Telegram
inbound bridge (`notify/telegram_inbound.py`) both walk.

A **budget soft-limit** (`router.decide()`, `config.budget_soft_pct`, default `0.8`) sits
in front of both seats: once today's spend crosses 80% of the daily token budget, `hard`
is forced back to `False` regardless of what the reflex asked for — which both picks the
cheap model *and* re-enters the routine provider preference (local/opencode before cloud).
Spend pressure steers the brain toward $0 providers automatically, before the hard
`BudgetExceeded` kill-switch ever trips.

## Memory in layers

Memory is not one store; it's a stack of layers with an explicit priority order, all
assembled by `memory/load.py:load()` and trimmed to a token budget by `truncate()`
(`PRIORITY = ["pinned", "recent", "tags", "semantic", "recall"]`, dropped from the tail):

1. **The grande** (`core/db.py` + `memory/facts.py`) — the durable, append-only fact
   store. Every fact is typed `dato` (a fact about the world/owner) or `directiva` (a
   standing instruction that changes *how* the brain behaves). Facts are **never
   destructively overwritten**: superseding one means inserting a new row with a newer
   `actualizado_en` and possibly a different `confianza`. Reading back a single "current"
   view is `facts.resolve()`'s job — it collapses same-content conflicts and the winner is
   the **most recent, with confianza as the tiebreaker** (`_conflict_key` +
   `(actualizado_en, confianza)` ranking, `memory/facts.py:153`). Pinned facts and *all*
   `directiva` facts load **always**, as identity — this is the layer that never
   truncates, even if it alone exceeds the token budget (`memory/load.py:truncate()`,
   the "mandatory core").
2. **The chica** (`memory/chica.py`) — a persistent working cache with threshold-LRU
   `consolidate` (it never evicts pinned facts or directivas) and a `regenerate` path that
   rebuilds it from the grande. `memory/backup.py` does WAL-safe online backup/restore.
3. **Layer 4 — semantic** (`memory/embeddings.py`) — facts ranked by cosine similarity to
   the active `query`, via a pluggable `Embedder` (`HashingEmbedder` offline default,
   `RemoteEmbedder` for a real endpoint). Off unless `config.memory_layer4_enabled` **and**
   a query is given **and** the fact has been indexed (`memory/load.py:index_facts()`).
   It is the lowest-but-one priority layer, so it truncates before recency/tags but after
   layer 5.
4. **Layer 5 — episodic recall** (`core/db.py:search_episodes`, FTS5) — the top few past
   episodes whose text matches the active query, via SQLite's `episodes_fts` virtual
   table (an external-content FTS5 index over `decision`/`accion`/`resultado`/
   `despertar_por`, kept in sync by triggers, ranked with `bm25()`). $0, deterministic,
   no model. It is the **lowest-priority** layer — dropped first under budget — and fully
   defensive: any failure (a stripped SQLite without FTS5, a bad DB state) yields an
   empty layer rather than disturbing the rest of the context build.

`core/step.py:_judge()` feeds `load()` both a `query` (the trigger's `objetivo`) and
`active_tags=[trigger.kind]`, so the tag and semantic/recall layers actually have
something to match against — a judgment isn't just "recent episodes plus identity", it
can genuinely recall a related fact or a related past decision.

Why the identity core never truncates: a brain that forgets its own standing directives
under context pressure isn't a brain with amnesia about facts, it's a brain that stops
being *itself*. Everything else — recency, tags, semantic hits, episodic recall — is
disposable per-turn context; the directives are the constant.

## Episodes: observability as a first principle

Every wake-up produces a structured `Episode` (`core/episode.py`): what woke it
(`despertar_por`), the decision made, the action taken, the result, and its cost. Episodes
are `begin()`-ed, `finish()`-ed, and `append()`-ed to the grande — **append-only, written
once, never mutated** — and every row carries an `instance_id` from the first commit, so a
future org of many brains never needs to retrofit multi-tenancy into the log.

This is not an afterthought bolted onto logging: `core/step.py` opens an episode for
*every* trigger, whether it resolves as a fixed action, a reflex skip, a judgment, a
delegation, or a deferral — the four `PolicyDecision` kinds and the delegate path all
write through the same `Episode.finish()` shape. The `episodes_fts` FTS5 index (layer 5,
above) turns this log into a live memory, not just an audit trail: the brain's own past
decisions become retrievable context for its future ones. The digest
(`notify/digest.py`), the ledger, and the "day in the life" narrative are all just
different views over this one append-only table.

## The org: 1..N brains

The same core scales from a single instance to a hierarchy without a second codebase.
`org/topology.py:Topology` is a validated tree of `BrainNode`s (unique ids, one root,
acyclic); **Lhassa is the permanent root administrator** (`config.root_admin_id`, default
`"lhassa"`) — `_ensure_admin()` re-parents any other root under it and synthesizes the
node if it's missing, so "administrator" and "root" are enforced to be the same node on
every load, never removed or demoted.

`org/coordinator.py` encodes the one rule that keeps this from turning into an
uncontrolled swarm: **only leaves execute**. `GeneralBrain.assign()` sends a typed
`TASK_ASSIGN` message down and `collect()` gathers `TASK_RESULT`s back up by
correlation id — "the general never runs the work itself." `DepartmentBrain.handle()` is
the only place that calls an executor (`org/executor.py:brain_executor`, the real
one-turn brain path, gated behind `LHASSA_ORG_LIVE` so it's offline/$0 by default), and
the tree-walk (`_walk()`) enforces this structurally: a coordinator node is always
recursed into, never handed to `_execute_leaf`. RBAC (`org/rbac.py`) gates each role to
its own verb (`assign` for a coordinator, `handle` for a leaf) as a second, independent
check on top of the tree shape.

Which department gets a goal is **deterministic and table-driven, not an LLM call**
(`org/routing.py:route()`): each registered department carries a `keywords: frozenset`,
the goal text is scored against every department by whole-word match count, the highest
scorer wins, ties break by registration order, and an unmatched goal falls back to a
default department. A tie (or an explicit `params={"multi": True}`) fans out to the top-3
departments and synthesizes their results — offline as a deterministic $0 concatenation
unless `LHASSA_ORG_LIVE` is set, in which case one extra think merges them (falling back
to the offline merge on any error).

A **relationship overlay** (`org/relationships.py`) sits alongside the command tree in a
separate file (`~/.lhassa/relationships.json`) so hierarchy and cross-brain relationships
never tangle: closed edge kinds `reads`/`notifies`/`reviews` (directed) and
`collaborates` (symmetric), with `permits(from, to, kind)` as the pure runtime read seam.

**The monitor role.** Any brain can be made a monitor (`make_brain_monitor`, applied by
default to `interns` — "everyone reports to it"). `org/monitor.py:gather_signals()` is a
read-only, $0 sweep (recent episodes, health, flows, approvals, failures); `monitor_pass()`
turns that into `{observations, risks, improvements, delegations}`, offline/stub by
default. `org/watcher.py:watch_loop()` is the always-on "ear": it diffs two
`gather_signals()` snapshots for *transitions* (`detect_events()`) and runs on an
**elastic cadence** — idle at `monitor_idle_interval` (300s default) while nothing is
running, active at `monitor_active_interval` (15s default) while an `agent_runs` job is in
flight — logging a monitor episode always, but only alerting (de-duped, over Telegram) on
IMPORTANT transitions (failures, a worsened health level, a new approval). A token is
spent only under `LHASSA_MONITOR_THINK` + `LHASSA_ORG_LIVE`; idle watching is $0 by
construction.

## Work-flows run through the hierarchy

A work-flow (`flows/`) is a DAG wiring `trigger → task → gate → output` (+ `branch`),
persisted at `~/.lhassa/flows.json`. The standing principle: **a flow is never run bare**.
`POST /api/flows/{id}/run`, the Telegram bridge, and any future caller go through
`org/delegate.py:delegate_flow()`, not the engine directly — it resolves the flow's owning
department, walks the command path from Lhassa down to that department, logs one
deterministic $0 `org-delegate` episode, and *then* runs the flow. A topology failure
degrades to a direct run rather than blocking the work — the hierarchy hop is a
deliberate audit trail at no extra token cost, not a hard dependency.

## Self-improvement: the brain forges its own hands

Repetition is the trigger. `agents/repetition.py:detect_repetition()` is a **pure SQL
aggregation** over the episodes table (`GROUP BY despertar_por, accion HAVING COUNT(*) >=
threshold AND SUM(costo_usd) > 0`, default threshold 5) — it never calls a model, and it
only flags patterns that both repeated *and* actually cost money (a free pattern isn't
worth automating). The `detect_repetition` action parks a `mint_action` approval and
notifies, de-duplicated via `watch_state` — it never mints or executes anything on its
own.

Authoring is the response. `agents/author.py:author_candidate()` has the **routine (cheap)
model** — never the smart one — write a candidate tool plus its test, as a small JSON
payload of files; any budget/provider failure or malformed reply degrades to an empty
result, never to executing whatever came back. `propose_tool()` then chains:
`versioning.create_candidate()` commits the files to a local-git branch
(`candidate/<name>` in `~/.lhassa/tools/`, no network) → `agents/gates.py:promote()` runs
the candidate through the four gates → **only if every inline gate passes** does it park a
`tool_graduation` approval. Nothing here auto-graduates; a human (or the earned autonomy
streak) still says yes.

The four gates (`agents/gates.py`), in order, stopping at the first failure
(deny-by-default):

1. **Segura** — runs caged in `security/quarantine.py` (no network, no real data).
2. **Correcta** — a `Verifier` (`agents/verify.py`) proves it works — a
   command/test/lint/health/regex/metric check that exits clean. Verifiers never call an
   LLM; this is the árbitro principle.
3. **Mejor** — it must beat a concrete baseline on a concrete metric
   (tokens/time/steps). No baseline given → cannot prove "better" → fail. There is no
   partial credit.
4. **Retiro** — continuous, not inline: `should_retire()` demotes a graduated tool back to
   quarantine if it went unused (zero recorded uses) or its production failure rate
   crosses 50% over at least 3 uses. The `review_tools` action runs this sweep.

**PTC — Programmatic Tool Calling** (`agents/ptc.py`) is the "one-think-many-actions"
device: a script runs fully quarantined (its own temp workdir, scrubbed env, process
group), and its *only* door out is a Unix-domain-socket RPC back to the parent process,
which dispatches **one deterministic Lhassa action at a time** — every single RPC call is
re-checked against the exact same `security/allowlist.py:allow()` gate a normal task
dispatch uses, with a signed `Task` (actor `"ptc"`, trust `INTERNO`). Hard caps: a 300s
wall-clock timeout (`_kill_group()` SIGKILLs the whole process group), a 50-call cap
enforced inside the RPC handler, and truncated stdout/stderr. Because one script can drive
many actions, `run_script` itself always requires human approval to invoke — even though
each action *inside* it is separately re-checked, entering PTC at all crosses the approval
frontier.
</content>
