# Lhassa — decisions log

Running log of the architectural/operational decisions for the Lhassa "brain"
build. The source of truth for the architecture is `doc/lhassa-brief.md` (a
living doc — correct it after each phase per §4). This file records the choices
made along the way. Newest first.

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
