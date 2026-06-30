# Lhassa — decisions log

Running log of the architectural/operational decisions for the Lhassa "brain"
build. The source of truth for the architecture is `doc/lhassa-brief.md` (a
living doc — correct it after each phase per §4). This file records the choices
made along the way. Newest first.

## 2026-06-30 — Phase 7 slice 2 (departments think) + heartbeat wired to opencode

**Ops:** the heartbeat now thinks via **opencode** — set in `~/Code/lhassa/.env`
(`LHASSA_HEARTBEAT_PROVIDER=opencode`, `LHASSA_OPENCODE_BIN=/usr/bin/opencode` — absolute
because the systemd user PATH is minimal, `LHASSA_OPENCODE_MODEL=opencode/deepseek-v4-flash-free`).
No systemd edit needed: each beat is a fresh process that re-reads `.env`. `.env` is
gitignored (secrets stay local). The routine path resolves to `PseudonymizingProvider(
OpenCodeProvider)` — opencode with PII scrubbing (opencode is cloud), **$0** on the free tier.
A `scripts/systemd/lhassa-telegram.service` user unit was added for the inbound poller
(credential-gated; install/enable when the owner sets a bot token).

**Phase 7 slice 2** (departments actually think; the general routes among many):
- `org/executor.py` `brain_executor()` runs a department task through the REAL one-turn
  brain path (identity + layered memory + router routine provider + `provider.complete`),
  the department's identity in the system prompt, logs a `despertar_por='org'` episode,
  degrades to a structured **$0** result on `ProviderNotConfigured`/budget (never raises —
  a failure becomes a `TASK_RESULT`, not a crash). The live model call is **gated behind
  `LHASSA_ORG_LIVE`** (default off → offline-deterministic) so slice-1 tests stay offline —
  the established turn-on-with-creds convention (subagent's call; honors the don't-break-
  tests/offline constraints).
- `org/coordinator.py`: `DepartmentBrain` defaults to `brain_executor` (still injectable);
  `GeneralBrain.assign_auto()` routes then assigns (the general routes, never executes).
- `org/routing.py`: deterministic table-driven registry (optimizacion/documentacion/
  finanzas) + `route()` with a documented fallback — **no LLM**. `org/__main__.py` offline
  two-brain demo.

**Verified:** 617 tests, ruff clean, `python -m lhassa.org` runs offline ($0). **Live smoke
(`LHASSA_ORG_LIVE=1`):** the general routed a goal → the `optimizacion` department **thought
for real via opencode** (free tier, **$0**), returned a genuine answer + a typed `TASK_RESULT`
correlated by `corr_id`. Two brains thinking end to end.

**Next Phase-7 candidates:** a second live department in the demo; cross-department commons
policy enforcement during a think; `STATUS_REQUEST`/`STATUS_REPORT` flow; an API/dashboard
view of the org message queue. **Backlog:** point a real department at `LHASSA_ORG_LIVE=1`
in production once desired.

## 2026-06-30 — Phase 6 cont. (heartbeat via opencode, voice, Telegram) + Phase 7 START

**Decision:** the owner has **no Ollama/Zen** but has the **opencode CLI** → the routine
heartbeat thinks through **opencode** (their existing creds), default the free tier
`opencode/deepseek-v4-flash-free`. Also: **start Phase 7 now** — the owner explicitly
authorized it (overriding the brief's "not yet"); built as the minimal two-brain skeleton.
4 slices, disjoint ownership, gates green (config surface added up front):
- **opencode heartbeat** `OpenCodeProvider` (`opencode run --pure --format json -m …`;
  router routine→opencode). **`--pure` matters:** a beat dropped from **>120s → ~8s**
  (without it opencode hangs on plugin/MCP startup). **Live-verified schema** (replaced the
  `[SUPUESTO]`): assistant text at `part.text` on a `text` event; totals nested under
  `step_finish` `part.tokens`/`part.cost` (the first parser read top-level only → fixed,
  + a regression test). Live smoke: free-tier round-trip → `pong`, tokens 11153/3, **$0**.
- **voice** `voice/brain_bridge.py` — spoken turns answered by the brain (identity +
  layered memory + episode `voz`), pipecat frames emitted via a lazy processor; swapped in
  only when `voice_enabled`. Default path unchanged. (Live audio needs Deepgram/Cartesia.)
- **Telegram** `notify/dispatch.py` (fan-out) + `notify/telegram_inbound.py` (getUpdates
  poller; honors only the configured chat; answers via `cli.agent.run_once`; `python -m`
  entrypoint). Credential-gated; no token → no network. (Live needs a bot token.)
- **Phase 7 `org/`** (foundation, golden rules enforced + smoke-verified on real SQLite):
  typed messages (`MessageKind` closed; free prose rejected) · read-only **commons** (only
  `general` writes; one shared row) · **RBAC** (deny-by-default, cross-department isolation)
  · **GeneralBrain coordinates, never executes** (no handle/executor) while DepartmentBrain
  executes via an injectable executor and logs a `despertar_por='org'` episode (audit plane)
  · **multi-person approval** (proposer≠approver for high-risk → `SelfApprovalError`) ·
  **two brains, never six**. `org_messages`/`org_commons` tables added to `db.py`.

**602 tests** (529→+73), ruff clean, imports/help ok, dashboard builds.

**Turn-on-with-creds / continuous:** point the systemd heartbeat at opencode by setting
`LHASSA_HEARTBEAT_PROVIDER=opencode` (or leave `auto` — opencode is auto-preferred when no
`local_model_url`); voice needs Deepgram/Cartesia keys + `LHASSA_VOICE_ENABLED=1`; Telegram
needs a bot token + chat id (+ `LHASSA_TELEGRAM_POLL=1` for inbound). **Backlog / `[SUPUESTO]`:**
wire the org default executor to the live one-turn brain path (`core.step`) — the injectable
`executor` is the seam; opencode `--pure` means org/MCP tools aren't available to a beat
(fine for routine thinking; delegated code work still uses the harnesses); the live pipecat
frame wiring of the voice bridge and the Telegram `getUpdates` shape are unverified without
creds. **Casa/MCP** (home integrations) remains the open Phase-6 continuous item.

## 2026-06-29 — Phase 6 (Madurez) — first push: local heartbeat, memory layer 4, rich interfaces

Phase 6 is **continuous** (no single DoD). This first push lands the autonomous,
offline-verifiable pieces; voice + home/casa MCP are noted as the remaining
continuous items (they need audio creds / per-home servers to smoke honestly).
Built (3 slices, gates green, file ownership disjoint — config surface added up front):
- **6.A** `llm/provider.py` `LocalModelProvider` (OpenAI-compatible local endpoint,
  no API key, **cost $0**, `zero_retention`+`is_local`; unreachable→`ProviderNotConfigured`
  → existing degradation path) + `privacy.py` (deterministic stdlib PII scrubber,
  reversible placeholders, never raises) + `PseudonymizingProvider` (wraps a remote
  provider: scrub before send, de-scrub after) + `router` routine→local preference
  (`heartbeat_provider` auto/local/cloud), hard→cloud, cloud path pseudonymized unless
  the provider is local (**the hook relaxes for local** — §3-Fase 6).
- **6.B** `memory/embeddings.py` (pluggable `Embedder`: `HashingEmbedder` deterministic
  offline default + optional `OpenAICompatEmbedder` degrading to hashing; no numpy) +
  `db.py` `fact_embeddings` table/helpers + `memory/load.py` **layer 4** (semantic
  `Context.semantic_facts`, lowest `PRIORITY` so it truncates first and never touches the
  identity core; `load(query=,config=)` + explicit `index_facts()`). **Off by default** —
  existing `load()` is byte-for-byte unchanged.
- **6.C** `cli/` power subcommands (`tools/approvals/approve/deny/report/memory/fleet`,
  each `--json`) preserving the default `lhassa "prompt"`/piped/REPL via a fallback
  `TyperGroup` + `notify/telegram.py` (credential-gated, no token→no network, never
  raises) + `/api/reports`·`/api/projections`·`/api/fleet` + dashboard Reports/Fleet panels.

**DoD-style live smoke** (offline tests + a smoke against a **real in-process
OpenAI-compatible socket server**): the routine path thinks on the **local** model at
**$0** with PII **un-scrubbed** (data never leaves the box — hook relaxed); the hard/cloud
path is wrapped in `PseudonymizingProvider` and PII is scrubbed→de-scrubbed round-trip;
memory **layer 4 ON** recovers an overheating fact that shares **no tag** with the query
("why is my computer warm and slow") and ranks it first, while **layer 4 OFF** yields no
semantic layer. **529 tests** (460→+69), ruff clean, `lhassa --help` lists 7 subcommands,
dashboard builds.

**Deferred / continuous (Phase 6 is ongoing):** **voice** (skeleton `voice/pipeline.py`
exists; end-to-end wiring needs Deepgram/Cartesia creds + audio to smoke) and **home/casa
MCP** integrations (per-home servers) are the remaining items. **Turn-on-with-creds:**
the local heartbeat needs a running local endpoint (`LHASSA_LOCAL_MODEL_URL`, e.g. Ollama);
layer 4 needs `LHASSA_MEMORY_LAYER4=1` (+ optional `LHASSA_EMBEDDING_MODEL`); Telegram needs
a bot token+chat id. **Backlog / `[SUPUESTO]`:** the cloud embedder + real-endpoint JSON
shapes (Ollama `usage`/`choices`, OpenAI `/embeddings`) are assumed, not yet live-verified;
the heartbeat is not yet pointed at a local model in the systemd unit (config default `auto`
with an empty URL = unchanged behavior); a free-text CLI prompt whose first word is exactly
a subcommand name routes to that subcommand unless quoted.

## 2026-06-29 — Phase 5 complete (self-improvement 2b in quarantine)

The brain can write its own tools and graduate them only through objective gates,
caged. Built (2 slices, gates green):
- **5.A** `security/quarantine.py` (`run_in_quarantine`: strongest-available sandbox
  via `unshare`/`firejail`/`bwrap` for **no-network**, a from-scratch **scrubbed env**
  so no secret leaks, temp cwd, `killpg` on timeout; `str`→`bash -c`, `list`→argv) +
  `agents/gates.py` — the **four gates of 2b**, deny-by-default, stop-at-first-failure:
  1 **Segura** (runs caged) · 2 **Correcta** (a command/tests verifier exits 0) ·
  3 **Mejor** (a `MetricVerifier` beats a concrete baseline; optimización is
  lower-is-better) · 4 **Retiro** (continuous: `should_retire` demotes a graduated
  tool that fails in production or goes unused). Gates **reuse** the Phase-3 verifiers
  and **never call an LLM**.
- **5.B** `agents/specialists/` (**skills, NOT fine-tuning**: `Specialist` = base model
  + instructions + tools + `gate_spec`, no model id anywhere; registry keyed by domain,
  first = `optimizacion`; `build_specialist_prompt` = persona identity + domain skill) +
  `agents/versioning.py` (**local-git** tool versioning: `create_candidate`→branch,
  **`approve` = merge** routed through the approval queue as a `tool_graduation`,
  `rollback` = `git revert`; `_git` never raises and degrades gracefully when git is
  missing; GitHub-remote PR/merge are **token-gated no-op hooks**, never reached
  offline) + `/api/tools` + graduate endpoint + `QuarantinePanel` + `tools_repo_dir`.

**DoD met** (offline tests + a live smoke with real git/SQLite/quarantine): the brain
writes a tool → candidate on its own branch → a good candidate **graduates** (segura+
correcta+mejor) while a worse one is **rejected at gate 3 with a reason** and an
incorrect one **at gate 2** (gate 3 never runs); graduation is an approval-queue
**merge** into `main` (a resolved `aprobada` `tool_graduation` row, content on `main`);
a graduated tool failing 3/4 in production (or unused) **retires** to cuarentena; and a
**git revert** rolls it back. "Mejor" is proven by a number (42 < 100), not asserted.
460 tests, ruff clean, dashboard builds.

**Deferred (token-gated, non-blocking):** the GitHub-remote path (push branch + open/
merge a real PR) is wired as a no-op hook behind `Config.github_token` — turns on with
a new token (old one revoked). **Backlog:** more specialist domains are just new
registry rows; the retirement supervisor's "went unused" grace window is `[SUPUESTO]`.

## 2026-06-29 — Phase 4 complete (hands: MCP + harnesses)

Decisions: 2nd typed task = **`tracking_postulaciones`**; the owner has all three
harnesses (opencode/Claude Code/Codex). Built (2 slices, gates green):
- **4.A** `harness/adapter.py` (`HarnessAdapter` Protocol + `RunEvent` + stream-json/
  plain parse; OpenCode/ClaudeCode/Codex adapters auto-detected via `which`; argv
  flags marked `[SUPUESTO]`) + `harness/runner.py` (`launch` fire-and-forget records a
  jobs row + PID, streams events, accrues cost; `supervise` reaps each tick) + router
  `pick_harness` (cheapest available: free<metered<subscription; `LHASSA_HARNESS`
  override) + `step` delegate branch (launch + episode notes harness/job/PID, no block).
- **4.B** `tools/mcp_client.py` (`mcp==1.28.1`; loads `~/.config/lhassa/mcp.json`,
  registers namespaced `mcp__<server>__<tool>` tools onto the LLM; defensive) +
  `postulaciones` table + `tracking_postulaciones` deterministic action (offline core,
  optional guarded MCP top-up) + `track_postulaciones` registry tool.

**DoD met** (offline + live runner smoke): an MCP tool is invocable; the brain delegates
to any of the three harnesses and supervises PID/cost live (a real subprocess launched,
streamed, reaped to `done` with cost $0.0021); adding an integration is config, not code.
421 tests, ruff clean.

**Backlog:** cross-restart job supervision by PID-from-disk (the reaper uses the
in-process registry — a launched-before-restart job isn't reaped after a brain restart);
validate the adapter argv `[SUPUESTO]` flags with a live harness smoke (spends — owner's
go); a real opencode/claude/codex delegation smoke is available on request.

## 2026-06-29 — Phase 3 complete (the arbiter)

Built (2 slices, gates green):
- **3.A** `agents/verify.py` — objective verifiers (`command/tests/lint/health/regex/
  metric`), **never call an LLM** (no provider/gateway import; asserted in tests) +
  `make_verifier`/`verify` dispatcher; wired into `core/step.py`'s judgment branch via
  `router.cascade(verify=…)` (failed verification escalates cheap→smart once; the
  episode records `verificado: passed/failed`). Reused by Phase-5 gates.
- **3.B** `security/approval.py` (HITL-**per-flow** `needs_approval`: per-action risk
  + origin trust + a high-risk always-approve set; parks pending in the `approvals`
  table) + `security/autonomy.py` (a streak knob: a HUMANO action approved N× becomes
  automatic; a denial resets it; high-risk never auto-promotes; **never promotes an
  `Origin`'s trust** — contagion-only) + `local_agent` approval gate + `/api/approvals`
  + `ApprovalQueuePanel`.

**DoD met** (offline + live smoke): a `mundo`/sensitive action waits for the owner's
OK; a repeated `humano` action becomes automatic (threshold 3); move-money always
requires approval even with a streak; a denial resets the streak; the verifier never
calls an LLM. 369 tests, ruff clean, dashboard builds.

Note: 2 pre-existing eslint warnings in `App.jsx handleEvent` (untouched code) — not
introduced here; `npm run build` is clean.

## 2026-06-29 — Phase 2 complete (economy + router)

Decision: the user has the **opencode CLI but no Zen** → the first `ModelProvider`
wraps the existing `.env` key via the gateway (`GatewayProvider`); `OpenCodeZenProvider`
is a ready stub (swap-in when Zen creds exist, per invariant #3). opencode-as-harness
is Phase 4.

Built (3 slices, gates green):
- **2.A** `ModelProvider` Protocol + `GatewayProvider`/`OpenCodeZenProvider`,
  `llm/router.py` (two axes, `pick_provider`/`pick_model`, cascade cheap→verify→
  escalate, `$think`/`$justdo`), and `core/step.py`'s judgment branch wired (load
  context → router → provider; `BudgetExceeded`/`ProviderNotConfigured` degrade to a
  deferred episode, no crash).
- **2.B** gateway **kill-switch** (`KillSwitchActive` ⊂ `BudgetExceeded`, refuses
  before any call) + persisted runtime budget override + `llm/ccusage.py` (defensive
  `ccusage --json` parser/runner) + `/api/llm/limits` & `/api/llm/ccusage` + dashboard
  controls (budget setter, kill-switch toggle, cross-harness breakdown).
- **2.C** `memory/consolidate.py` **semantic consolidation** (cheap model via the
  provider/gateway: cluster-summarize + detect contradictions; complements the
  mechanical LRU; only touches the chica; threshold-driven).

**DoD met:** routine routes to the cheap model; the ledger shows the split (real
per-model pricing, tested); **crossing the cap stops** — verified live (kill-switch
raises, provider called 0×). 321 tests, ruff clean, dashboard builds.

**[SUPUESTO] to validate later:** ccusage JSON field names (marked in code); and the
live "a day of heartbeats costs cents" smoke needs real cheap-model calls (deferred —
won't spend without the owner's go). **Backlog:** swap to opencode Zen when available.

## 2026-06-29 — Phase 1 complete (memory)

Built additively (old `memory/store.py` untouched), 2 slices, behind gates:
- **1.A** `core/db.py` facts table (Apéndice A, append-only, instance_id) +
  `memory/facts.py` (`Fact` dato/directiva, contradiction = newest+confianza) +
  `memory/load.py` (layered `load(token_budget)`: pinned+directivas always →
  recency → active-tags; truncate by priority; deterministic token estimate; **no
  embeddings / layer-4**) + `memory/seed.py` (Apéndice B seed facts).
- **1.B** `memory/chica.py` (persistent working cache; **threshold-LRU
  `consolidate`** that never evicts pinned/directivas; `regenerate` from the
  grande) + `memory/backup.py` (WAL-safe online `backup`/`restore`/`verify`).

**DoD met** (offline tests + live SQLite smoke): recover a fact from "days ago" by
tag · chica fills → consolidates without overflow (23→5, pinned kept) · kill the
chica → regenerates from the grande · backup → verify True (corrupt → False) →
restore round-trips. 272 tests, ruff clean.

**Deferred to Phase 2 (per the brief):** semantic memory consolidation (summarize
similar items / detect contradictions via a cheap LLM) — Phase 1 is mechanical LRU
only. **Backlog:** reconcile the old `memory/store.py` (voice session facts/
sessions) with the new typed-facts model.

## 2026-06-29 — Phase 0 complete (the spine)

Built additively over the existing skeleton, 5 vertical slices, all behind gates
(no AI attribution, pytest green, ruff clean):
- **0.1** `core/db.py` (SQLite WAL "grande", append-only, `instance_id` on every
  table/query) + `core/episode.py` (structured Episode from the first commit).
- **0.2** `core/step.py` + `core/watchlist.py` + `core/actions.py` — the
  deterministic do-and-sleep loop (zero-token watchlist → fixed action → episode);
  the `else` branch is the Phase-2 router seam.
- **0.4** `security/`: signed `Task`, `TrustLevel`/`Origin` (degrade=min, never
  promote), Ed25519 signing, deny-by-default allowlist (= §2.6) with HITL-per-flow.
- **0.3** `/checkin` + `/report` + `remote/local_agent.py` — check-in-not-push with
  desired_state reconciliation; the allowlist gate runs on the machine.
- **0.5** `core/heartbeat.py` + systemd `lhassa-heartbeat.{service,timer}` +
  parametrized identity (`Identity`/`load_identity`, org hook #2; directives loaded
  as identity; `PERSONA_BLOCK` kept stable) + the end-to-end DoD test.

**DoD met** (offline tests + a live in-process smoke with real Ed25519/SQLite/git):
timer pulses → check-in → reconcile → signed `sync_repo` → allowlist gate → action
runs → report → two episodes explain *why* → state persists across a DB reopen →
every row carries `instance_id`. 229 tests, ruff clean.

**Living-doc correction (§4/§7):** the brief lists `OpenCodeAdapter`/`ModelProvider`
under Phase 0, but Phase 0's loop is deterministic-only and never exercises them —
so they're **deferred to Phase 2 (ModelProvider, the router/think path) and Phase 4
(HarnessAdapter)**, where they're actually used. The three org hooks are in place.

Open Phase-0 polish (backlog, not blocking): `desired_state` should map repo names
→ real paths (the smoke's `sync_repo` failed gracefully because the objetivo wasn't
resolved to a path); single-writer staging buffer (§2.7) is a later refinement.

## 2026-06-29 — adopting the implementation brief

- **Brief adopted** as the plan: Lhassa becomes a persistent "brain" (JARVIS) with
  its own identity that orchestrates models/harnesses behind interfaces — "build
  the brain, rent the body." Execution model: *do-and-sleep* (`state + step()`
  woken by a systemd heartbeat / API). See `doc/lhassa-brief.md`.
- **Existing code is the skeleton — extend, don't rewrite.** What's already built
  maps onto the brief: `llm/gateway.py` (Phase 2 choke point), `run_ai_cli` +
  `claude_code` (→ HarnessAdapter, Phase 4), `tools/privileged.py` allowlist (→
  `security/allowlist.py` + Ed25519, Phase 0/3), `health`/`scheduler`/`state`/
  `api`/`cli`/`persona` (Phase 0/6). Net-new and FIRST: `core/step.py` +
  `watchlist.py` + structured episode + `instance_id` + check-in (Phase 0).
- **The old `doc/roadmap.md` is superseded** by the brief and re-sequenced; its
  A1 (tools-aware gateway) shipped as commit 290eac9 (a valid Phase-2 piece). The
  rest is re-baselined onto the brief's Phase 0→7.

### Decisions (this session)
1. **Host:** develop **locally first** (the brain runs on the laptop) with all
   seams ready (instance_id, check-in, systemd) to migrate to a VPS without
   surgery. VPS later.
2. **Heartbeat ModelProvider:** start with **opencode Zen** (free/cheap), with
   pseudonymisation as a mandatory hook while the heartbeat uses free models;
   migrate to a local ModelProvider later (Phase 6).
3. **Repo folder:** renamed `~/Code/potts` → `~/Code/lhassa` (the package was
   already `lhassa`).
4. **Governance/pace:** I drive **Phase 0 autonomously** (subagents + gates +
   commits), checking in with the owner at each phase's **DoD** before advancing.

### Open / pending (asked, to confirm when their phase arrives)
- Harness subscriptions available (Claude Code / Codex / opencode).
- Machine network: WireGuard vs Tailscale.
- Telegram notifications (needs a bot token).
- GitHub account/org + a **new** token (old one revoked) for tool versioning (Phase 5).
- Adopt **ccusage** for cross-harness accounting.
- Seed facts (Appendix B) — confirm they're the owner's; `instance_id = inst_personal`.
- Second typed task (Phase 4): calendar_event / agendar_fechas / generar_documentacion /
  tracking_postulaciones.
- `[SUPUESTO]` items to validate with live smoke runs (Zen training on free data;
  Managed Agent Harness as sandbox; heartbeat provider ToS/rate-limits).
