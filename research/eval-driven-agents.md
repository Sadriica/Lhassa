# Eval-driven agents: the production discipline behind bounded autonomy

Raw source: `ai-engineering-from-scratch` (rohitg00), a 503-lesson / 20-phase MIT
curriculum, WebFetched 2026-07-30. Phase 13 ("Tools & Protocols", 23 lessons)
covers function calling, MCP (transports, resources, prompts, sampling, OAuth
2.1, gateways) and the A2A protocol. Phase 14 ("Agent Engineering", 42 lessons)
covers agent loops, memory systems, planning, production hardening, named eval
frameworks and benchmarks, and multi-agent patterns. This note distills the
production-agent discipline the curriculum names as a checklist — not a review
of the curriculum itself — and checks it against what Lhassa already has built.

## The idea in one sentence

An agent earns the right to act autonomously only inside a bounded, verified,
budgeted envelope — never by trusting the model's own judgment of its work.

## Mechanics — down to the base

The curriculum's Phase 14 names six production concepts as the load-bearing
ones, each with a specific mechanism:

- **Bounded autonomy with verification gates + scope contracts.** An agent's
  action space is declared up front (what it may touch, what it may not), and
  its output is checked by something other than itself before it is trusted.
  This is the same principle Loop Engineering states as a "golden rule": *the
  doer never judges* — Anthropic's own examples are `/goal` in Claude Code (an
  independent model judges a measurable condition) and Outcomes rubrics in
  Claude Managed Agents (a corrector subagent evaluates criteria each
  iteration; the agent cannot stop until all pass).
- **Durable execution & checkpointing.** Long-running agent work survives
  process death — state is persisted at each step boundary, not held only in
  a live conversation, so a crashed or resumed run picks up where it left off
  instead of re-doing work or corrupting state.
- **Cost & action budgets** — enforced with concrete circuit-breaker
  mechanisms: cost governors (spend caps that degrade behavior before a hard
  stop), kill switches (a manual, immediate halt independent of any budget
  math), circuit breakers (auto-trip on repeated failure), and HITL
  propose-then-commit (the agent drafts an action, a human approves it before
  it executes, rather than the agent executing and asking forgiveness).
- **Multi-layer memory** — vector + graph + KV + durable storage, each layer
  answering a different question (semantic similarity, relationship
  structure, fast key lookup, survives-restart persistence) rather than one
  store trying to do all four jobs.
- **Eval-driven development** — the curriculum's central claim: build the
  evaluation harness *before* the production agent, not after. It names
  concrete frameworks (RAGAS, DeepEval, G-Eval) and benchmarks (SWE-bench,
  GAIA, AgentBench, WebArena, OSWorld) as the instruments that make "better"
  a measured claim instead of an impression.
- **Skill libraries as composable, versioned units** — reusable capability
  packages an agent loads rather than re-deriving from a general prompt each
  time, versioned so a regression in one skill doesn't silently break every
  agent that uses it.
- **MCP as extensibility, A2A for cross-agent communication** — Phase 13
  treats MCP as the standard connector for tools/resources (with its own
  security lessons: tool poisoning, OAuth 2.1, gateway auth) and Phase 14
  adds multi-agent debate — running the same question through independent
  agents and reconciling disagreement — as the mechanism for high-stakes
  decisions where a single model's answer is not enough to trust.

The pedagogy itself is a method worth naming: every lesson is Motto → Problem
→ Concept → **Build It** (raw implementation, no framework) → **Use It** (the
same thing via the standard framework) → **Ship It** (a reusable artifact —
prompt, skill, agent, or MCP server). The claim is that understanding a
mechanism by building it from scratch is what makes the framework version
legible later, rather than a black box you copy calls from.

## Evidence the authors present

The repository's own evidence is structural, not empirical: 503 lessons, 20
phases, ~320 hours, spanning Python/TypeScript/Rust/Julia, MIT-licensed
(verified via WebFetch of the repo, 2026-07-30). No benchmark numbers are
claimed by the curriculum itself for its own teaching method — the benchmarks
it *names* (SWE-bench, GAIA, AgentBench, WebArena, OSWorld) are the standard
external instruments the field uses to score agents, not results the
curriculum produced. The stated philosophy, quoted directly: "You don't just
learn AI. You build it. End-to-end. By hand." — positioned as "the spine"
connecting scattered research, arguing that calling APIs without building the
mechanism from scratch first leaves a gap in understanding.

Two adjacent sources in the same capture batch corroborate specific pieces of
this list with actual numbers, though neither is peer-reviewed and both
should be read as vendor/practitioner claims, not independent measurement:

- Anthropic's Parameter Golf experiment (via a Loop Engineering thread,
  secondhand): an agent given DISCOVER → PLAN → EXECUTE → VERIFY → ITERATE
  with an independent verifier improved a training pipeline roughly 6× more
  than a baseline that lacked the verify step and iterated on its own
  judgment.
- A Continual Learning Bench citing 0.839 vs 0.700 vs 0.364 across three
  models on sequential SQL tasks with a shared memory file, attributed to how
  much of a five-level memory-use ladder (fail → investigate → verify →
  distill to rule → consult the rule) each model actually completes — the
  claim being that verification (level 3) is the hinge step, not memory
  volume.

Both are single-source practitioner claims (X/Twitter threads) without
published methodology; treat as directional, not conclusive.

## Sources

- `ai-engineering-from-scratch` repository, WebFetched 2026-07-30:
  https://github.com/rohitg00/ai-engineering-from-scratch (also
  aiengineeringfromscratch.com)
- Loop Engineering thread (angeldot_, X, 2026-06-11, 2.7M views) — captured in
  `scratchpad/sources-pack.md` S2, secondhand summary of Anthropic's Parameter
  Golf experiment and the Continual Learning Bench numbers; primary source not
  independently verified.
- Mixture-of-agents thread (iamlukethedev, X, 2026-07-28) — HermesBench numbers
  (MoA 0.8202 vs a single model 0.7607/0.7412) as one instance of multi-agent
  debate/aggregation beating a single model on a stated benchmark; same
  single-source caveat.

## Mapping to Lhassa

Lhassa already implements a working superset of several items on this list,
independently arrived at (`doc/decisions.md` is the build log; dates predate
this capture). Verified directly against code, 2026-07-30:

- **Verifier-locked eval harness — LhassaBench.** `lhassa/bench/runner.py`
  scores every fixture with `lhassa.agents.verify` — "the SAME objective
  verifier the rest of Lhassa trusts... never an LLM judge, per the standing
  invariant" (module docstring). Results append to the `bench_runs` table
  (`lhassa/core/db.py`) so history survives without re-running. This is
  eval-driven development already wired in as a first-class instrument, not
  bolted on after the fact — matching the curriculum's central claim (harness
  before production) but with a house rule the curriculum doesn't state:
  the verifier is never a model, closing the "doer judges itself" gap
  directly rather than relying on an independent judge model.
- **Bounded autonomy with verification gates — the four gates.**
  `lhassa/agents/gates.py` implements segura (runs in quarantine) → correcta
  (a test/command verifier exits 0) → mejor (beats a concrete baseline on a
  concrete metric) → retiro (continuous: a graduated tool that stops earning
  its keep is demoted back to quarantine). Deny-by-default: "a candidate is
  rejected unless every gate explicitly passes," and gates 2/3 reuse the same
  `agents.verify` objective layer LhassaBench uses — one verification
  discipline for both scoring and graduation.
- **Sandbox as gate 1.** `lhassa/security/quarantine.py` runs candidate code
  with no network, a scrubbed env, and a temp cwd, preferring `unshare -rn` >
  `firejail --net=none` > `bwrap --unshare-net`, degrading to a
  best-effort subprocess sandbox and reporting which isolation level it
  actually achieved — the module's own docstring flags Anthropic's Managed
  Agent Harness as a future higher-isolation option that would slot in
  without changing the contract.
- **Cost & action budgets.** `lhassa/llm/gateway.py` has a `PRICING` table,
  raises `BudgetExceeded` on a spent daily budget, and a separate
  `KillSwitchActive(BudgetExceeded)` for a manual, budget-independent hard
  stop (`LHASSA_KILL_SWITCH`) — refused before any provider work runs. This
  matches "cost governors, kill switches" directly; circuit-breaker-style
  auto-degradation is the router's fallback chain (`llm/router.py`) rather
  than a named breaker class.
- **HITL propose-then-commit.** `security/approval.py` parks pending
  approvals in the `approvals` table; `security/autonomy.py` is the streak
  knob that only lowers friction after repeated human approval of the *same*
  action by the *same* actor, never promotes trust globally, and always
  requires approval for a fixed high-risk action set
  (`always_approve_actions`) regardless of streak. This is a stricter version
  of propose-then-commit — per-flow, not per-agent.
- **Durable execution & checkpointing.** `lhassa/flows/durable.py` binds the
  flow engine to `FlowRuntimeRepository`: "snapshots are frozen before
  execution, every cursor becomes a checkpoint, and terminal/waiting states
  are committed with the worker's fencing token" — a fencing token is a
  stronger primitive than the curriculum's generic "checkpointing," guarding
  specifically against two workers resuming the same run.
- **Skills as composable, versioned units.** `lhassa/skills/` implements the
  3-layer skills convention (name on a brain → `SKILL.md` in
  `~/.lhassa/skills/` → loaded at think time), with `skills/curator.py` doing
  usage-telemetry-driven lifecycle (active → stale → archived, tar.gz backup
  before archiving, never deleted).
- **MCP is not a gap — it exists.** `lhassa/tools/mcp_client.py` already
  loads server definitions from `~/.config/lhassa/mcp.json`, connects, and
  namespaces discovered tools as `mcp__<server>__<tool>` ("plug in, don't
  program" — the module's own framing), defensive-by-construction (missing
  SDK/config/unreachable server all degrade to zero servers, never raise).
  `tests/test_mcp_client.py` exists. This corrects an assumption in the task
  brief: Lhassa is not missing an MCP client.
- **What Lhassa genuinely lacks:** an MCP *server* (Lhassa exposing its own
  tools to other agents over MCP — only the client direction is built), the
  A2A protocol (no reference to it anywhere in `lhassa/` or `doc/`, confirmed
  by grep), and multi-agent debate/aggregation as a general pattern (the org
  layer's `coordinator.run_goal` does deterministic fan-out-and-merge across
  departments, not independent-answer-then-reconcile across models on one
  question — closer to Mixture-of-Agents' shape than to today's org routing).

## Not yet verified by us — and the experiment that would

- **The Parameter Golf 6× figure and the Continual Learning Bench numbers**
  are secondhand (a thread summarizing an experiment we did not run and
  cannot inspect the methodology of). The Lhassa-native equivalent
  experiment: pick one `bench_runs` fixture family, run it twice — once with
  `agents.verify` gating retries, once with a fixed number of ungated
  attempts — and compare pass rate and token spend from the ledger. That
  would produce a Lhassa-measured verifier-vs-no-verifier delta instead of
  citing someone else's number.
- **Whether LhassaBench's verifier-locked scoring generalizes past
  short-answer fixtures** (the current fixture set is factual/extraction/
  codegen/instruction-following, all short-answer by design — see
  `DEFAULT_MAX_TOKENS = 256` in `bench/runner.py`) to longer agentic runs is
  untested; the curriculum's named benchmarks (SWE-bench, GAIA, AgentBench)
  are long-horizon and would stress a different part of the harness.
- **Multi-agent debate's actual cost/benefit on Lhassa's own workload** is
  unmeasured — the MoA numbers cited above are a different codebase
  (HermesBench) on an unstated task distribution.

## Open questions

- Is an MCP *server* (exposing Lhassa's own actions/skills to other agents)
  worth building given the org layer already does inter-brain coordination
  natively, or does it only matter once a second, non-Lhassa agent needs to
  call in?
- Does the four-gate pipeline's "mejor" (beats a baseline) generalize outside
  the `optimization` domain (`_LOWER_IS_BETTER = False` and the tokens/
  tiempo/pasos framing in `gates.py` are optimization-specific) to a
  correctness-only domain where "better" isn't a scalar metric?
- Where would multi-agent debate actually pay for Lhassa specifically — a
  personal brain rarely faces the "high-stakes decision under disagreement"
  scenario the pattern is built for; is there a real trigger condition, or is
  this an org-layer feature waiting for a 1..N brain topology to need it?
- Is Lhassa's fencing-token checkpoint (`flows/durable.py`) strictly stronger
  than the curriculum's generic "durable execution," or does the curriculum's
  broader framing (surviving across different failure classes: process
  death, network partition, host migration) cover cases the flow runtime
  doesn't yet handle?
