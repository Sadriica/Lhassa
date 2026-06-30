# Lhassa — decisions log

Running log of the architectural/operational decisions for the Lhassa "brain"
build. The source of truth for the architecture is `doc/lhassa-brief.md` (a
living doc — correct it after each phase per §4). This file records the choices
made along the way. Newest first.

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
