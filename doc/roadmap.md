# Lhassa implementation plan

Roadmap turning the research findings into work. Ordered by leverage and
dependency. Each item has: **Goal**, **Approach**, **Tasks** (with file
touchpoints), **Acceptance**, **Effort** (S ≈ ½ day, M ≈ 1–2 days, L ≈ 3–5 days),
**Risk**.

Design stance (from the research, high-confidence): a single ReAct tool-loop is
the engine — don't build a graph framework. The real differentiator is an
**external verification gate** (models can't grade themselves). Multi-agent
fan-out, ToT, best-of-N are ~4–15× tokens and opt-in only. MCP is the durable
integration standard. Keep everything cost-minimizing and local-first.

What already exists to build on: `agents/base.py` (BaseAgent tool loop) +
code/research/system/claude_code/local_cli agents; `llm/gateway.py`
(cache+budget+ledger, `estimate_cost`); `llm/provider.py`; `persona.py`;
`tools/registry.py` (+ guardrails, monitor, vision, privileged); `memory/`
(store + session); `health/`; `scheduler.py`; `state.py` (agent_runs, metrics,
audit, WebSocket broadcast); `api/server.py` (REST + WS + `/api/llm/ledger`);
`cli/` (one-shot + REPL); dashboard panels.

---

## Phase A — Cost & correctness core (do first; highest leverage)

### A1. Route the agent tool-loop through the gateway
- **Goal:** every cloud-LLM call (voice, agents, CLI) goes through one choke point
  so cache + daily budget + cost ledger apply everywhere — not just memory
  extraction. Makes "minimize AI" real and measurable.
- **Approach:** extend the gateway with a tools-aware path. Add
  `gateway.complete_with_tools(messages, tools, *, config, ...) -> ToolTurnResult`
  that wraps one provider round-trip (Anthropic `messages.create` /
  OpenAI-compat `chat.completions.create`), returns text + tool-calls + usage,
  updates `state.metrics`/ledger, and honors the budget. The agent/CLI loops call
  this per iteration instead of the raw client. Cache only no-tool turns (tool
  results are non-deterministic) — key on (provider, model, messages, tools).
- **Tasks:**
  - `llm/gateway.py`: add `complete_with_tools()` + `ToolTurnResult` (text,
    tool_calls, usage, cost, cache_hit); reuse pricing/budget/ledger plumbing.
  - `agents/base.py`: replace the direct `client.*.create` calls (~lines 248/307)
    with `gateway.complete_with_tools(...)`; keep the two-branch shape.
  - `cli/agent.py`: route `_run_openai`/`_run_anthropic` through the same call;
    drop the local usage tallying (gateway owns it) but keep the cost line.
  - Tests: `tests/test_gateway.py` add tool-turn cache/budget/ledger cases
    (offline, fake completion fn).
- **Acceptance:** an agent/CLI run increments `llm_calls`/tokens/`llm_cost_usd`
  in the ledger and the AI-Usage dashboard panel; a repeated no-tool prompt is a
  cache hit; over-budget raises `BudgetExceeded` and the loop degrades gracefully.
- **Effort:** M. **Risk:** medium — touches the hot agent path; cover with tests
  and a live smoke run before committing.

### A2. Cheap-model routing
- **Goal:** spend the expensive model only on ambiguous/complex turns; use a
  small/cheap model (or local Ollama) for classification, routing, and simple
  replies.
- **Approach:** a `llm/router.py` with `pick_model(prompt, context) -> model` —
  rules first (regex/intent, length, "needs reasoning" heuristics), optional tiny
  local model as a fallback classifier later. Config: `LHASSA_ROUTER=off|rules`,
  `LHASSA_MODEL_CHEAP` / `LHASSA_MODEL_SMART`. The gateway/agent asks the router
  for the model per turn.
- **Tasks:** `llm/router.py` (+ tests); `config.py` knobs; wire into A1's
  `complete_with_tools`. `cascade`: run cheap first, escalate if a confidence/
  quality check fails (ties into A3).
- **Acceptance:** routine prompts resolve on the cheap model; a flagged-complex
  prompt escalates; ledger shows the split. Router decisions are logged.
- **Effort:** M. **Risk:** low (rules are deterministic; default off).

### A3. External verification gate for agent tasks
- **Goal:** the single highest value-per-token lever. When an agent does work,
  run an objective check and re-prompt on failure — never let the model grade
  its own reasoning.
- **Approach:** a `agents/verify.py` defining a `Verifier` protocol +
  built-ins: `command` (run a shell check, pass = exit 0), `tests` (project test
  cmd), `lint` (ruff), `health` (reuse `health.run_all`), `regex` (output must
  match). An agent task may carry a `verify` spec; after the agent produces a
  result, run the verifier; on fail, append the failure output and re-prompt up
  to N times (default 2), then report honestly.
- **Tasks:** `agents/verify.py` (+ tests with fake checks); hook into
  `agents/base.py` run() (optional `verify=` arg) and the `delegate_to_agent` /
  `run_ai_cli` tools (optional `verify` param in `registry.py`); surface
  pass/fail + attempts in `state.agent_runs` and the dashboard.
- **Acceptance:** a code task with a failing test iterates and either passes or
  reports the failure with the check output; verifier never calls an LLM.
- **Effort:** M. **Risk:** low–medium (re-prompts cost tokens — cap iterations,
  gate behind the budget).

---

## Phase B — Durability, memory, orchestration UX

### B1. Durable, resumable agent runs + human-in-the-loop
- **Goal:** agent runs survive restarts and can pause for approval/steering.
- **Approach:** borrow LangGraph's pattern *as a small in-house mechanism* (no
  heavy dependency): checkpoint each agent turn (messages, step, status) to
  SQLite in `~/.lhassa/`; an `interrupt`/`resume` API so a run can pause on a
  high-risk tool (e.g. privileged exec) until approved. Reuse the existing
  `state.agent_runs` + audit log + privileged allowlist.
- **Tasks:** `agents/checkpoint.py` (SQLite save/load of run state);
  `agents/base.py` checkpoint after each turn + resume-from-checkpoint;
  `state.py` add `paused`/`awaiting_approval` statuses + broadcast; approval
  events over the API/WS. Tests for save/resume/approve.
- **Acceptance:** kill mid-run, restart, `resume` continues; a privileged step
  pauses until approved/denied (decision audited).
- **Effort:** L. **Risk:** medium (state modeling); keep the schema small.

### B2. Context compaction + filesystem memory + todo recitation
- **Goal:** longer agent runs without token blow-up; net token *saver*.
- **Approach:** when a run's context nears a threshold, summarize older turns
  (one cheap-model call via the router) and reinitialize with summary + recent
  turns; offload bulk artifacts to files under `~/.lhassa/runs/<id>/` with
  restorable pointers; maintain a `todo.md` the agent rewrites each step
  (recitation) to fight goal drift on multi-step tasks.
- **Tasks:** `agents/context.py` (compaction + token estimate via gateway
  pricing/`count_tokens`); `agents/base.py` integrate; a `todo` scratchpad tool
  in `registry.py`. Tests for compaction trigger + pointer restore.
- **Acceptance:** a long run compacts instead of overflowing; token use per step
  stays bounded; the todo list reflects progress.
- **Effort:** M. **Risk:** medium (summary quality — use it only for old turns).

### B3. Fleet view + attach / interrupt (Oz-style)
- **Goal:** see and steer every background agent in one place.
- **Approach:** reuse `state.agent_runs` + WebSocket. Dashboard: an Agents/Fleet
  panel listing source · status · duration · **cost-so-far** with cancel and
  jump-to-output; CLI: `lhassa agents` (list) and `lhassa agents attach <id>`
  (stream + interrupt). Builds on B1's pause/interrupt.
- **Tasks:** `api/server.py` endpoints (`/api/agents`, cancel, attach stream);
  dashboard `FleetPanel.jsx`; `cli/` `agents` subcommand. Tests for the
  endpoints.
- **Acceptance:** launch two background agents, watch both with live status +
  cost, cancel one, attach to the other.
- **Effort:** M. **Risk:** low.

---

## Phase C — Integration & extensibility

### C1. MCP support (client + server)
- **Goal:** consume external MCP tools without bespoke glue, and expose Lhassa's
  registry as an MCP server. MCP is now a Linux Foundation standard.
- **Approach:** add an MCP client that loads servers from
  `~/.config/lhassa/mcp.json` and registers their tools into the registry at
  startup (namespaced `mcp__server__tool`); optionally an `lhassa mcp serve` that
  exposes the registry over MCP. Use the official `mcp` Python SDK.
- **Tasks:** `tools/mcp_client.py` (connect, list tools, adapt to registry
  handlers); `registry.py` dynamic registration; `config.py` + `mcp.json`
  loader; `cli/` `mcp` subcommand. Tests with a stub MCP server.
- **Acceptance:** a configured MCP server's tools appear and are callable from
  voice/CLI; Lhassa's tools are reachable from an external MCP client.
- **Effort:** L. **Risk:** medium (transport/auth; start with stdio servers).

### C2. CLI Phase 2 — power features
- **Goal:** recipes, session log, slash/custom commands, approval tier.
- **Tasks:**
  - Recipes: `~/.config/lhassa/recipes/*.yaml` (persona+tools+model preset),
    `lhassa -t <recipe>` (`cli/recipes.py`).
  - SQLite session log in `~/.lhassa/` + `lhassa logs` + `--resume`/`-c`
    (`cli/session.py`); shared with voice/dashboard memory.
  - Markdown slash + custom commands (`.lhassa/commands/*.md`, `$ARGUMENTS`,
    `@file`, `!shell`) in the REPL.
  - Approval tier: `--approval on-request|never` reusing `tools/privileged.py`
    allowlist + per-tool gates.
- **Acceptance:** `lhassa -t triage`, `lhassa logs`/resume, a custom command, and
  an approval prompt on a privileged tool all work.
- **Effort:** L. **Risk:** low.

### C3. Orchestration (opt-in, cost-capped)
- **Goal:** orchestrator-workers and multi-harness — but only where it pays.
- **Approach:** a lead agent that fans out 3–5 ephemeral, read-only subagents for
  genuinely independent breadth-first tasks, behind a **hard cost cap** via the
  gateway; default to sequential chaining/routing (multi-agent is ~15× tokens).
  Cron triggers via the existing `scheduler.py`. Multi-harness = orchestrate the
  `run_ai_cli` backends (claude/codex/opencode).
- **Tasks:** `agents/orchestrator.py` (fan-out + aggregate + cost cap);
  scheduler hooks for recurring agent runs; `run_ai_cli` orchestration option.
  Tests for the cost cap and aggregation.
- **Acceptance:** a breadth-first task fans out under a cost ceiling and
  aggregates; exceeding the cap stops cleanly; a cron-scheduled agent runs.
- **Effort:** L. **Risk:** medium — keep it opt-in; lean on A1's budget.

---

## Phase D — Optional / decision required

### D1. Local voice (replace paid cloud STT/TTS)
- **Goal:** drop the recurring Deepgram (STT) + Cartesia (TTS) cost and keep
  voice fully private/offline. Research says this is genuinely *solved*.
- **Approach:** faster-whisper (STT) + Kokoro-82M or Piper (TTS), wired into the
  pipecat pipeline as local services; cloud remains a fallback. **Decision
  needed:** this contradicts the earlier "no local models" call — but STT/TTS are
  the one place local clearly wins (cost + privacy + latency, CPU-friendly).
- **Tasks:** `voice/pipeline.py` local STT/TTS providers; config toggles; bench
  on the Meteor Lake CPU.
- **Acceptance:** wake → transcribe → reply → speak with zero cloud STT/TTS calls.
- **Effort:** L. **Risk:** medium (audio plumbing); gated on your decision.

---

## Suggested sequence

1. **A1 → A2 → A3** (cost choke point, routing, verification) — foundational,
   highest leverage, and they reinforce each other.
2. **B3 fleet view** early (cheap, high visibility) once A1's ledger is unified.
3. **B1 durability + HITL**, then **B2 compaction/memory**.
4. **C1 MCP**, then **C2 CLI power features**, then **C3 orchestration**.
5. **D1 local voice** whenever you decide on the local-models question.

## Cross-cutting guardrails
- No AI/Claude attribution in code/commits/PRs (standing rule).
- Every phase: keep `make test` green (currently 154) and `ruff` clean; commit in
  the `feat:`/`refactor:` style; mirror any system-level change into `arch-setup`.
- Everything cost-aware: new LLM paths go through the gateway; default to the
  cheapest thing that works; verification before trust.

## Avoid (research consensus)
Multi-agent swarms by default · Tree-of-Thoughts / best-of-N / self-consistency ·
elaborate "memory engines" (start with summary + retrieval) · NPU hardware for
LLM inference · autonomous browser-use on untrusted pages · long-horizon
unattended autonomy (agents fail >4h tasks — design for short, verifiable steps).
