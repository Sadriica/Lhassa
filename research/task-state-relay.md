# Task state as an external, live artifact; the relay between harnesses

> Draft research note; not yet reviewed by the owner. Part of Lhassa's research
> line (`doc/research.md`): Plan 2, "la caja negra (task recorder + relay)".
> Fed by Fase 0.1 of the Plan 2 plan (`/home/w/.claude/plans/…keen-finch.md`).

## The idea in one sentence

A task's canonical state lives **outside any harness**, maintained live by a
deterministic supervisor, so that handing a task from one agent to another
(deadline, limit, model change) is O(1) and cheaper than either native
intra-harness switch or cold restart; because the transcript is mostly
re-derivable from disk [S16].

## Mechanics; down to the base

The premise, at the wire level: an API-based model never "holds" a session.
Every request re-sends the transcript; provider-side prompt caching is
ephemeral and per model/provider. So the real cost of any switch is the
prefill of whatever gets re-sent [S16]. The question is only *what* gets
re-sent. Five families of answers exist in the world:

**Family 1; the HANDOFF.md convention (state as prose files).** A large,
independent ecosystem converged on one answer: write the task state to
markdown files in the repo, have every agent read it on entry and update on
exit. It is the direct, in-production prior art of the Plan 2 "recorder +
relay"; with the state living in the *repository*, not in any harness:

- [S1] the HANDOFF.md pattern: one file at project root, read on entry,
  updated on exit; fixed 5-7 sections, ~500-token budget, and a hard "what
  NOT to do / decisions made" section, because git history captures *what*
  changed but not *why* or *what not to redo*. Multiple parallel agents
  coordinate through it; a shared Postgres+pgvector store is the scaled-up
  variant ("shared brain"). The author's named failure mode: relying on git
  history alone, verbose handoffs, inconsistent format, skipping the update.
- [S2] `agentic-project-continuation` (skills.sh): a workflow skill,
  read project memory (`docs/ai/HANDOFF.md`, `TASKS.md`, `DECISIONS.md`,
  `SYSTEM_OVERVIEW.md`, `PLAN.md`), then *verify* current code/tests/git
  before trusting any summary; choose a right-sized Simple/Standard/Complex
  workflow; record verified continuation point. Its FAQ explicitly says a new
  session continues "without relying on the full old chat transcript"; the
  Plan 2 thesis, stated as a convention.
- [S3] `cskwork/agent-handoff`: writes a structured handoff doc (objective,
  current state, done-with-evidence, what's left, decisions+why, files
  touched, gotchas, the single exact next action, verify steps); derives the
  read/modified file sets *mechanically from tool history, never from recall*;
  has a DO-NOT-REDO list; chainable; credits oh-my-pi's `/handoff` as its
  design ancestor.
- [S4] `codes1gn/agent-handoff`: `/handoff` + `/resume` slash-command skills,
  writes `<tag>.xml` (structured state) + `<tag>.transcript.md` (raw
  conversation) + project/user memory XML, indexed in `index.xml`; resume
  renders a summary box with the next action highlighted. Two layers:
  per-handoff session state and across-session persistent memory.
- [S5] `softaworks/agent-toolkit` session-handoff (~2k stars on the registry):
  scaffolded, timestamped, chainable handoffs (`.claude/handoffs/`), a
  validation script (completeness, secrets, quality score ≥ 70), and; most
  relevant to Lhassa; a **staleness audit** (`check_staleness.py`): FRESH /
  SLIGHTLY_STALE / STALE / VERY_STALE, checking time since handoff, git
  commits since, files changed, branch divergence, missing files.

All five are the same architecture: **task state = prose in the repo,
maintained by the agent itself (LLM), consumed by whatever agent comes next.**
They prove the relay concept in production across harnesses (Claude Code,
Codex, Cursor, Copilot, Gemini); but they are conventions, not a live
deterministic state. The recorder is the agent's own summarization memory; no
file is derived mechanically from a durable event log; the state is written on
exit/at context pressure, not maintained continuously.

**Family 2; the intra-harness session relay (omp).** oh-my-pi implements
handoff and compaction *inside one harness* [S6][S7][S8][S9]:

- `compaction.strategy: "handoff"` is a first-class compaction strategy:
  `generateHandoff()` builds a handoff document from the live session, then
  `AgentSession.handoff()` **starts a brand-new session** and injects the
  document as a visible `custom_message` of type `handoff`. `handoffSaveToDisk`
  writes `handoff-<ts>.md` into the session's artifact dir. This is an
  O(1)-ish relay; but the state lives in omp's session format, and the target
  is always another omp session.
- `/resume @claude` and `/resume @codex` convert foreign sessions into a fresh
  omp session identity [S8]; cross-harness *import*, one-way into omp, not a
  shared external state.
- `checkpoint` (mark conversation state for later collapse-and-report) and
  `rewind` (prune exploratory context, keep a concise report) are the
  "destilado" mechanism [S6].
- Memory: `retain`/`recall`/`reflect`/`learn`, backends local/Hindsight/
  Mnemopi, project-scoped, "compresses each session into a mental model that
  loads on the first turn of the next one" [S6]; the "estado en el cerebro"
  idea, but bound to omp's own runtime and its own SQLite.
- `snapcompact` (the default compaction strategy) is the deterministic,
  no-LLM compaction the Plan 2 "plano determinista" thesis points at: history
  is serialized with per-tool caps (tool results truncated head+tail, 2000
  chars @ 0.6 head ratio; args capped per value 500 / per call 2000), printed
  in dim ink, rasterized to pixel-font PNG frames the vision model reads back;
  frame shapes are provider-tuned via their own SQuAD recall evals [S7][S9].
  It is *deterministic* (no model, no API key); but it compresses for the
  same-session context window, not into an external canonical state.

So omp is the nearest auditable implementation of the *mechanisms* (deterministic
compression, checkpoint/rewind, handoff document); and its own `handoff` file
family (S3's ancestor) shows the state can be lifted out as prose. But omp's
state is omp's; there is no "state external to the harness" in omp's model.

**Family 3; memory blocks and agent-to-agent protocols (the "brain" and the
"wire").** Two orthogonal bodies:

- Letta (MemGPT lineage): agent memory = editable **memory blocks** (in-context,
  persistent) + archival/recall memory out-of-context; **sleep-time compute**
  = a background agent that rewrites/consolidates memory while the primary is
  idle ("hace y duerme", literally) [S10]. Agents can *share* a block
  (multi-agent shared memory); full agent state (blocks, messages, tools)
  serializes to a portable `.af` (Agent File) format for checkpoint/move
  between servers [S10]. This is "canonical state in the brain, portable",
  but Letta is a *runtime*: the state is meaningful inside Letta, not usable
  to drive a foreign harness.
- OpenAI Agents SDK handoffs: an agent delegates to another *within one run*
  graph; handoffs carry structured metadata (`input_type`) and can *filter*
  what history the receiving agent sees (`input_filter`, `nest_handoff_history`)
  [S11]. Mistral exposes the same pattern (`agent.handoff` event) [S12].
  A2A (Google, now Linux Foundation) is a wire protocol between agents with
  capability discovery (Agent Cards); MCP 2.0's "agentic session tokens" are
  described as a cross-platform state handoff primitive (vendor doc, not yet
  independently verified) [S13]. These solve *control transfer between
  agents*, not *task state surviving harness death*.

**Family 4; native compaction & session persistence of the big harnesses
(Claude Code, Codex, opencode).** The four harnesses we actually run all ship
their own native byte-or-prose compaction and session resume, and all persist a
full JSONL/DB transcript on disk that is *legible* (Fase 0.2's required
readability check, verified in this note against the live files at
`~/.claude/projects/`, `~/.codex/sessions/` and `~/.local/share/opencode/opencode.db`):

- **Claude Code** two forms of compaction, both *LLM-summarizing* (unlike
  omp's snapcompact): auto-compaction triggers when
  `conversation_tokens + reserved_output_tokens > context_window - 20k`, writes
  a `SummaryMessage` JSONL entry (NOT restored on resume; "summary only;
  details in transcript"), strips images to `[Image: type]`, then re-injects up
  to 5 recent files @5k tokens + skill/MCP defs up to 25k tokens [S17].
  Env controls: `DISABLE_AUTO_COMPACT`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`
  (default 80%), `CLAUDE_CODE_RESUME_INTERRUPTED_TURN`. The JSONL schema is
  public and independently documented [S18]: types `user`/`assistant`/
  `system` (with `subtype`)/`file-history-snapshot`/`custom-title`/`agent-name`,
  plus `subagents/agent-<id>.jsonl` and `tool-results/<tool-use-id>.jsonl`
  side files, and a **`file-history-snapshot`** entry type that is literally a
  per-message file backup used for undo/revert; Claude Code already snapshots
  files, which is the raw material a deterministic recorder needs. The agent
  SDK's `SessionStore` lets you mirror transcripts to S3/Redis/DB so another
  host resumes a session; `forkSession` is "not a byte copy"; `getSessionMessages`
  returns the *post-compaction chain* (a 503-entry store can yield 18 messages,
  i.e. compaction collapses the live view to the summary tail) [S19].
- **Codex** persists `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`
  (event-based `{timestamp,type,payload}`), plus a global `history.jsonl`
  index for cross-session search and (on this machine) SQLite stores
  (`logs_2.sqlite`, `goals_1.sqlite`, `memories_1.sqlite`). Resume via
  `codex resume/continue`, `/compact` (LLM summarizer), `/resume`, `/fork`
  (duplicates up to resolution), and `state.json` for the agent's last state
  ([S20][S22]). Formats differ from Claude's.
- **opencode** stores sessions in `~/.local/share/opencode/opencode.db`
  (SQLite tables `session`/`message`/`part`/`event`/`session_summary`/etc.,
  verified here; 54 MB) with `--continue`/`-c` and `--session <id>`/`-s`
  resume; compaction is two-phase (prune tool outputs + LLM summary), and a
  dynamic-context-pruning plugin keeps per-session pruning state in
  `~/.local/share/opencode/storage/plugin/dcp/<sessionId>.json` [S23]. Its
  tables are close to what a deterministic recorder would consume [S22][S23].

**Family 5; the research line on context compression for long-horizon agents
(2025-2026).** The papers that name the exact cost shape Plan 2 attacks:

- **ACON (arXiv:2510.00615, Microsoft/KAIST, Oct 2025)** is the closest
  published treatment: compress both history and latest observation via a
  *compressor LLM* guided by failure-driven optimized compression guidelines,
  with small-model distillation of the compressor. Its headline claim: cut peak
  tokens up to 54% while preserving long-horizon accuracy on AppWorld /
  OfficeBench / 8-objective QA [S24][S25]. Three caveats it reports are
  directly relevant to the recorder: (1) compression can *increase* total cost
  when it forces extra steps; (2) it "breaks the KV-cache," forcing recompute
  of compressed history; (3) a "learned prior" compressor provides no guarantee
  that task-salient details survive [S25]. [S26] an `Awesome-Agent-Context-Compression`
  survey (2026) catalogs the space: observation compression (CSIM, COMPASS),
  *memory-state compression* (Learning-What-Not-to-Forget, Git-Context-Controller
  treating context as a versioned filesystem, ContextWeaver dependency-structure
  memory), and representation-level (KV-cache) eviction. The survey's named
  failure mode "F2 in-compression information loss" is the determinism problem
  the Plan 2 recorder solves by deriving from events+git rather than trusting a
  compressor.
- **The cache mechanic is the real meat of the thesis.** Independent of all
  harnesses, one practitioner measured the exact cost [S27]: after the prompt
  cache expires (sources vary, minutes to ~1h on subscription Claude), a resume
  of a long session re-pays the full history; `/compact` reduces *in-session*
  size but does not re-prime the cache, so resuming a 4-hour session "ate the
  rest of the daily limit" while a fresh session from a small HANDOFF file costs
  a few hundred tokens. This is the Plan 2 claim ("switch cost = prefill of what
  is re-sent") measured by a stranger on a real bill; and it is the strongest
  independent evidence the recorder's destilado could beat native intra-harness
  resume, not just cold start.

**Family 6; the 2025-26 academic line that measures the same cost shape Plan 2
attacks (papers, not conventions).** A second web/arxiv pass (Aug 2026) found
the peer-track literature on the *economic* side of the thesis. None fills the
niche; several confirm and refine the cost model, one partially contradicts the
naive "resume re-pays full prefill" premise, and one gives the relay an
independent, stronger reason to exist:

- **The Harness Effect** (Writer, arXiv:2607.06906, ICML-adjacent preprint) is
  the closest thing to a published version of the Plan 2 cost premise. It holds
  tasks+models constant and swaps only the orchestration layer across 22 locked
  tasks × 6 models: cost/task −41%, wall-clock −44%, tokens/task −38% at
  quality parity; efficiency is model-invariant (−33% to −61%) while quality
  gains track baseline capability (r=0.99, "harness leverage"). It decomposes
  the per-turn bill into harness-controlled terms and derives an
  *effective-input-price* under caching (κ≈0.1). It validates the core claim
  ("the harness, not the model, sets the cost") and quantifies it. But its
  harness is Writer's own runtime; the state it maintains is Writer-bound; the
  exact intra-harness architecture the relay wants to escape [S28].
- **The Complexity Trap** (JetBrains Research + TUM, arXiv:2508.21433, NeurIPS
  2025 DL4Code workshop) is the independent counterpart to the "plano
  determinista" thesis: on SWE-bench Verified with SWE-agent across five model
  configs, *simple deterministic observation masking* halves cost vs. raw agent
  and **matches or slightly exceeds LLM summarization** in solve rate. Their
  hybrid (mask + summary) cuts another 7-11%. Two findings matter: (1) agent
  contexts are ~80-84% environment observations; that *is* the "90% re-
  derivable" claim, independently measured in the SE domain; (2) determinism
  beats LLM summarization on cost and ties it on quality, so a deterministic
  recorder is not obviously worse than an LLM one [S29].
- **Don't Break the Cache** (PwC, arXiv:2601.06007) measures prompt caching on
  DeepResearch Bench across three providers/four flagship models: cost −41-80%,
  TTFT −13-31%, and; decisive for the relay; *strategic cache boundary
  control* (cache the stable system prefix, exclude dynamic tool results) is
  more consistent than naive full-context caching, which can even add latency.
  It partially *contradicts* the naive Plan 2 premise: within a live session
  with a warm prefix cache, re-sending history is cheap (cache reads ≈0.1×). The
  relay's edge is precisely the case this paper does not cover: cache expiry
  (TTL ~minutes on ephemeral, ~1h on subscription), and a *harness/provder/
  model change* where the old cached prefix is gone and byte-shape differs [S30].
- **Cache-Aware Prompt Compression (CAPC)** (PayPal, arXiv:2607.15516)
  supplies the missing *cost model* with measured parameters: Sonnet 4.6's
  cache is two-tier with a sharp threshold ≈3,500 tokens (below it ρ plateaus
  at ≈0.83; above it ρ≈1.0). Query-agnostic compression (compute once, cache
  the compressed prefix; exactly the "destilado precomputable" shape) is the
  cheapest strategy in 16/16 LongBench-v2 cells and −51.7% on a 94k-token
  production tools prefix. It gives the bench its crossover formula
  ρ_cross(r)=(α−1/r)/(α−β) for when compressing beats cache-only. It confirms
  the relay's "small, stable, precomputable state" is the cache-optimal shape
  [S31].
- **The Hidden Footprint / AgentFootprint** (arXiv:2607.11149) makes *storage
  footprint* a first-class agent-evaluation metric and, critically, defines an
  **R-score of conversation-history reconstructability**: replaying the same
  trajectory through seven persisting frameworks yields a 6.7× spread in
  retained bytes; `R=3` stores (OpenAI Agents, LangGraph, etc.) reconstruct
  194/194 calls exactly; content-addressing collapses all stores to
  0.05-0.50 MB with R unchanged. This is a *measurement instrument* for the
  exact thing Fase 0.2 wants to measure; it reframes "re-derivability from
  disk" from hypothesis to a scored, benchmarkable property, and shows most
  of the storage spread is removable redundancy [S32].
- **Agent Memory: Characterization and System Implications of Stateful Long-
  Horizon Workloads** (Stanford/KU Leuven, arXiv:2606.06448) is the first
  *systems* characterization of agent memory: ten architectures, phase-aware
  profiling attributing cost to construction/retrieval/generation. Key numbers:
  construction is embedding- and prefill-dominated (median decode share of
  construction tokens 4.6%), construction energy exceeds query energy by orders
  of magnitude (Letta >13h to ingest 1.8M tokens), construction wall-time spans
  five orders of magnitude, and Paradigm-I long-context memory incurs quadratic
  prefill with history length. Directly relevant: the *write path* (what a
  recorder consumes) is cheap and deterministic (BM25/embedRAG ≈sub-second);
  the expensive path is LLM-mediated construction; an argument for deriving
  the destilado from events+git rather than an LLM [S33].
- **Crab: A Semantics-Aware Checkpoint/Restore Runtime for Agent Sandboxes**
  (arXiv:2604.28138) checkpoints *sandbox state* (filesystems, processes,
  runtime artifacts) at turn boundaries, host-side, harness-agnostic, deciding
  per-turn whether a filesystem-only, process-only, or full checkpoint suffices.
  It is evidence the ecosystem is scaling checkpointing at the OS/sandbox
  layer; the *body*; which complements rather than competes with the relay's
  *task-state* layer. If the body can resume, the relay's job narrows to the
  head (decisions/reasoning) [S34].
- **Oracle Agent Memory** (arXiv:2607.13157) is the DB-native extension of the
  same memory-substrate line: retention across sessions, LongMemEval-style
  evaluation, ~7× fewer tokens than flat-history baselines. Relevant only as
  proof that "durable memory substrate beats flat history on tokens" is now a
  vendor-positioned claim, not a research gap [S35].

Net effect on the thesis: (a) the *cost model* is now published and measured
([S28][S30][S31][S33]); the bench should adopt their formulas instead of
re-deriving them; (b) the naive premise must be narrowed to the cache-cold /
cross-harness case ([S30]); (c) "re-derivability" now has a public instrument
([S32]) and independent support (~80-84% observations, [S29]); (d) the niche
claim stands; none of these maintains a live deterministic state that *drives*
a foreign harness.

**The measuring instrument exists too.** [S14] `agent-coldstart-analyzer` is a
CLI that analyzes agent transcripts to find "expensive cold-start intervals
where the agent burns context on exploration before making progress"; the
Plan 2 hypothesis, as a public analysis tool. [S15] Mem0's 2026 memory
benchmarks (LoCoMo / LongMemEval / BEAM) are the standard instruments for
"how much does a memory/continuation layer actually retain", priced in tokens
per query; the metric the Plan 2 bench needs.

**Synthesis; what exists and what doesn't.** The *concept* exists and is
proven in production as a prose-file convention (Family 1). The *mechanisms*
exist (deterministic compression, checkpoint/rewind, handoff docs) inside one
harness (Family 2). The *brain* exists as a runtime-bound store (Family 3). The
*cost shape* is now published and measured by the 2025-26 paper line (Family 6):
harness sets the price ([S28]), caching makes history cheap *while the cache is
warm and the prefix shape survives* ([S30][S31]), and re-derivability has an
independent measurement instrument and ~80-84% support in SE ([S29][S32]). What
no public system does; the precise Plan 2 niche, unchanged and still unfilled,
is:

1. **A live, deterministic, verifiable task state** maintained continuously by
   a supervisor (not on exit, not by the agent's own memory), where every field
   is mechanically derived from a durable event log + git, not from an LLM's
   recall of the session.
2. **That state as the canonical source of truth**, harness-agnostic, such that
   any harness (claude/codex/opencode/omp) can be *driven by it*; not just
   imported into one format.
3. **The measurement** that this beats both native intra-harness switch and
   cold restart, on tokens-to-completion; which [S14][S32] instrument the
   waste of but nobody has published the A/B.

The Family-1 ecosystem is the strongest evidence that the *relay* is worth
building; the Family-1 failure modes (verbosity, staleness, skipped updates,
recall-based state) are exactly what a deterministic recorder would fix.

## Evidence the authors present

- [S1] The HANDOFF.md post reports, as experience rather than a benchmark:
  parallel multi-agent coordination failed on *context loss* before the
  pattern, and improved after; the "what NOT to do" section stopped a repeated
  mistake (redis sessions re-attempted after being rejected); ~500-token
  handoffs keep the state inside the first 10% of context ("the last 20% of
  context degrades quality"). These are the author's own observations, no
  controlled measurement.
- [S2] `agentic-project-continuation` presents no numbers; a workflow skill
  whose claim is architectural: verification against code/git prevents stale
  summary trust. No evaluation published.
- [S5] softaworks' session-handoff claims ~2k-star adoption on the skills
  registry and ships concrete tooling (scaffold, validator, staleness auditor),
  but no accuracy/recall measurement of resumption quality.
- [S7][S9] omp's snapcompact: the shape table (frame sizes per provider, e.g.
  Claude 11on16-bw 1932px/1568px, Gemini 8on22-bw @2048px, OpenAI 8on22-bw
  1568px) "comes from the snapcompact 200k-token evals in packages/snapcompact,
  where bitmap frames preserved QA recall at lower billed-token cost than raw
  text for vision-capable models" [S7]. Their numbers, from their own SQuAD
  evals; methodology is in `packages/snapcompact/research/`, not re-run here.
- [S10] Letta's sleep-time compute: presented as architectural (non-blocking,
  proactive consolidation) with the original MemGPT paper
  (arXiv:2310.08560) as the foundational claim; no accuracy numbers for
  sleep-time consolidation in what was fetched.
- [S15] Mem0 reports on its own eval: LoCoMo 92.5 @ 6,956 tokens/query,
  LongMemEval 94.4 @ 6,787, BEAM(1M) 64.1, BEAM(10M) 48.6 (2026 algorithm,
  "token-efficient"); the 2025 ECAI paper (arXiv:2504.19413) is the public
  head-to-head baseline. Their numbers, their harness; relevant to Lhassa only
  as the benchmark design to imitate, not a result to adopt.
- [S11] OpenAI Agents SDK: handoff with `input_filter` is documented as "the
  receiving agent still sees the conversation history unless you change it",
  i.e., even a mature SDK defaults to full-history relay, which is the cost
  Plan 2 attacks. No numbers given.
- None of these are OUR results. They are documented as raw material; the
  verification that matters for Plan 2 is Fase 0.2 (own transcripts) and
  Fase 2 (own bench).

## Sources

- [S1] Cowrie, "How to Coordinate Task Handoff Between Multiple AI Coding
  Agents", https://docs.bswen.com/blog/2026-03-21-agent-task-handoff-coordination/
  (fetched in full; quotes a Reddit thread, r/ClaudeAI,
  https://www.reddit.com/r/ClaudeAI/comments/1ie1ixc/extreme_claude_code_workflows/)
- [S2] genoooool, `agentic-project-continuation`,
  https://github.com/genoooool/agentic-project-continuation (fetched in full)
- [S3] cskwork, `agent-handoff`, https://github.com/cskwork/agent-handoff
  (search-snippet level; docs site https://cskwork.github.io/agent-handoff/)
- [S4] codes1gn, `agent-handoff`, https://github.com/codes1gn/agent-handoff
  (search-snippet level; site https://codes1gn.github.io/agent-handoff/)
- [S5] softaworks, `agent-toolkit` session-handoff skill,
  https://github.com/softaworks/agent-toolkit/tree/main/skills/session-handoff
  (search-snippet + registry copy at agentpedia.codes)
- [S6] can1357, `oh-my-pi` README, https://github.com/can1357/oh-my-pi
  (fetched in full, truncated at ~11k tokens; capabilities list + tool reference)
- [S7] can1357, `docs/compaction.md`,
  https://github.com/can1357/oh-my-pi/blob/main/docs/compaction.md
  (fetched in full via raw.githubusercontent)
- [S8] can1357, `docs/session-operations-export-share-fork-resume.md`,
  https://github.com/can1357/oh-my-pi/blob/main/docs/session-operations-export-share-fork-resume.md
  (fetched in full via raw.githubusercontent)
- [S9] can1357, `packages/snapcompact/README.md`,
  https://github.com/can1357/oh-my-pi/tree/main/packages/snapcompact
  (fetched in full)
- [S10] Letta (fka MemGPT): blog "Sleep-time Compute",
  https://www.letta.com/blog/sleep-time-compute/ and repo
  https://github.com/infinera/letta (README; memory blocks, .af Agent File,
  sleep-time agents); original paper arXiv:2310.08560
- [S11] OpenAI Agents SDK, Handoffs docs,
  https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md
  and https://developers.openai.com/api/docs/guides/agents/orchestration
  (fetched/snippet)
- [S12] Mistral Docs, Handoffs, https://docs.mistral.ai/studio-api/agents/handoffs
- [S13] A2A (Agent2Agent) protocol, https://a2a-protocol.org; MCP 2.0
  "Agentic Session Tokens"; techbytes.app post, UNVERIFIED against primary
  docs (directionally plausible claim, not captured from a primary source)
- [S14] hwei, `agent-coldstart-analyzer`,
  https://github.com/hwei/agent-coldstart-analyzer (search-snippet level)
- [S15] Mem0, "AI Agent Memory 2026: Progress Benchmark Report",
  https://mem0.ai/blog/state-of-ai-agent-memory-2026; paper arXiv:2504.19413
- [S16] Lhassa Plan 2 (Fase 0.1 brief): the wire-level premise that transcripts
  are re-sent per request and ~90% re-derivable from disk
  (`/home/w/.claude/plans/realiza-un-plan-donde-keen-finch.md`, §PLAN 2)
- [S17] Claude Code, "Auto-Compaction", code.claude.com docs (mirror):
  https://brewpirate.github.io/claude-code-docs/sessions/auto-compaction/
  (trigger formula, SummaryMessage, restore budget, circuit breaker),
  plus env reference https://brewpirate.github.io/claude-code-docs/env/context-compaction-session-resume/
- [S18] weirdgiraffe `claude-code-sessions-explorer` / docs `JSONL-SCHEMA.md`,
  https://github.com/weirdgiraffe/claude-code-sessions-explorer/blob/main/docs/JSONL-SCHEMA.md
  (real-format walkthrough, incl. `file-history-snapshot`, subagents, tool-results)
- [S19] Claude Code SDK, "Persist sessions to external storage",
  https://code.claude.com/docs/en/agent-sdk/session-storage
- [S20] "How to Resume a Codex CLI Session", https://inventivehq.com/knowledge-base/openai/how-to-resume-sessions
  (~/.codex/sessions/ layout, `resume/--last`, `/compact`, `/fork`, `/new`)
- [S21] lin-guanguo `llm-memory-research`, Codex sessions structure,
  https://lin-guanguo.github.io/llm-memory-research/agent-cli/codex-session-files/
  (`history.jsonl`, `config.toml`, session filenames) and opencode page,
  https://lin-guanguo.github.io/llm-memory-research/opencode.research/
  (compaction two-phase, 5-section template, fork/revert)
- [S22] "Codex CLI Session Persistence: Resume, Fork, and Analytics" (independent
  doc, 2026): `meta.json`/`transcript.jsonl`/`state.json`/`refs/parent`,
  lifecycle + analytics, https://codex.danielvaughan.com/2026/04/13/codex-cli-session-persistence-resume-fork-analytics/
  (secondary/independent; layout here conflicts with [S20]'s date-sharded
  rollout-*.jsonl; reconciled in the note by reading the live disk layout)
- [S23] opencode SQLite layout + resume, https://docs.codeagentswarm.com/en/guides/opencode-conversation-history
  and jazzyalex `agent-sessions` note on `~/.local/share/opencode/opencode.db`
  (both third-party; the SQLite tables were confirmed here on the live DB);
  DCP plugin state format, https://lzw.me/docs/opencodedocs/Opencode-DCP/opencode-dynamic-context-pruning/advanced/state-persistence
- [S24] K. Kang, W.-N. Chen et al., ACON, arXiv:2510.00615 (Oct 2025) +
  official impl https://github.com/microsoft/acon
- [S25] ACON contextual detail incl. three caveats, https://arxiv.org/html/2510.00615v1
- [S26] YerbaPage, `Awesome-Agent-Context-Compression`,
  https://github.com/YerbaPage/Awesome-Agent-Context-Compression
  (2026 survey: F2 info-loss failure mode, CSIM/COMPASS/ContextWeaver/GCC)
- [S27] Cowrie, "Stop Wasting Tokens: Use a HANDOFF File Instead of /compact",
  https://docs.bswen.com/blog/2026-06-29-claude-handoff-file-vs-compact/
  (cache-expiry resume cost, measured on a real Claude Pro bill)
- [S28] Sayed Ali et al. (Writer), "The Harness Effect: How Orchestration Design
  Sets the Token Economics of Enterprise Agentic AI", arXiv:2607.06906 (Jul 2026);
  fetched full text (abstract, Section 3-4, results tables)
- [S29] Lindenbauer, Slinko, Felder, Bogomolov, Zharov (JetBrains/TUM),
  "The Complexity Trap: Simple Observation Masking Is as Efficient as LLM
  Summarization for Agent Context Management", arXiv:2508.21433 (2025; NeurIPS
  2025 DL4Code workshop); fetched full abstract + methods
- [S30] Lumer, Nizar, Jangiti et al. (PwC), "Don't Break the Cache: An Evaluation
  of Prompt Caching for Long-Horizon Agentic Tasks", arXiv:2601.06007 (Jan 2026);
  fetched full (cache strategies, DeepResearch Bench, results)
- [S31] Yan Song (PayPal), "Cache-Aware Prompt Compression: A Two-Tier Cost Model
  for LLM API Caching", arXiv:2607.15516 (Jul 2026); fetched full text
  (two-tier ρ, crossover model, CAPC algorithm, 16/16 dominance grid)
- [S32] Yu, Gui, Yu, Yang, Li, "The Hidden Footprint: Making Storage a First-Class
  Metric for LLM Agent Evaluation", arXiv:2607.11149 (2026); full abstract +
  reconstructability (R-score, echo, 194/194 reconstruction) + AgentFootprint
- [S33] Omri, Gan, Broveak et al. (Stanford/KU Leuven), "Agent Memory:
  Characterization and System Implications of Stateful Long-Horizon Workloads",
  arXiv:2606.06448 (Jun 2026); full abstract + phase-aware profiling numbers
- [S34] Wu, Chang, Cao, Gao, Wang, "Crab: A Semantics-Aware Checkpoint/Restore
  Runtime for Agent Sandboxes", arXiv:2604.28138 (2026); abstract + per-turn
  checkpoint granularity
- [S35] Alake, Bernardis et al. (Oracle), "Oracle Agent Memory as an Enterprise
  Memory Substrate for Long-Horizon AI Agents", arXiv:2607.13157 (Jul 2026);
  abstract + LongMemEval, 7× tokens claim
- [S36] Zhao, "When Parallelism Pays Off: Cohesion-Aware Task Partitioning for
  Multi-Agent Coding" (Co-Coder), arXiv:2606.00953 (May 2026); abstract + method
  (dependency graph, hub-file isolation, community-detection partition)
- [S37] "Multi-agent Collaboration with State Management" (STORM), arXiv:2605.20563
  (May 2026); abstract + method (incremental conflict resolution vs. post-hoc
  merge; ~8 concurrent engineers scale)
- [S38] "Recursive Agent Harnesses" (RAH), arXiv:2606.13643 (Jun 2026); abstract +
  method (parent spawns full subagent harnesses via code, Codex 71.8→81.4% on
  Oolong-Synthetic, harness-not-model gains)
- [S39] Yu, "AdaptOrch: Task-Adaptive Multi-Agent Orchestration in the Era of
  LLM Performance Convergence", arXiv:2602.16873 (Feb 2026); abstract + method
  (topology selection among 4 canonical shapes on a task DAG)

## Mapping to Lhassa

Verified against Lhassa's codebase and Plan 2 [S16]:

- **The niche is real and unfilled.** No public system found maintains a live,
  deterministic, harness-agnostic task state that *drives* a foreign harness.
  The closest competitors are conventions (Family 1), one-harness mechanisms
  (Family 2), and the native compaction/persistence of the big harnesses
  (Family 4); all of which either sit inside one harness or trust an LLM
  summarizer (compaction) rather than deriving state mechanically from
  events+git. The research line (Family 5) confirms the cost exists but
  optimizes the same in-harness compaction path rather than removing it. Lhassa's
  "build the brain, rent the body" position (doc/concepts.md) is the architectural
  claim that makes this niche coherent for Lhassa in a way it isn't for omp
  (which owns the body) or Letta (which is the brain *and* the runtime).
- **Lhassa already has the durable event log the recorder needs.** `flows/`
  (`engine.py` + tables `flow_run_state`/`flow_checkpoints`/
  `flow_step_attempts`/`flow_events` in `core/db/_conn.py`) is durable execution
  with immutable checkpoints, leases and idempotency; the supervisor side of
  "mantenido en vivo por el plano determinista" already exists. `agent_runner.
  _stream` (`harness/runner.py:157-175`) already parses every streamed line
  into `RunEvent` (kinds `text`/`tool`/`cost`/`done`/`error`; no `limit` yet).
  The recorder consumes these; it does not invent a new capture path.
- **Family-1 failure modes are the product spec for the recorder.** The
  ecosystem's named failures; stale handoffs, skipped updates, recall-based
  state, verbosity [S1][S5]; are each addressed by a mechanical recorder:
  staleness checkable via git/disk (the same digest logic as
  `context_pack.py`'s `_compute_digest`), update enforced by the runner hook
  (Fase 1 "solo observación"), fields derived from `RunEvent`+`git diff` not
  model recall (Fase 0.3 gate), 500-token budget as a hard cap.
- **What omp proves, Lhassa should copy; not the format.** omp's own
  `handoffSaveToDisk` and its successor prose-handoff family [S3] show the
  *artifact* is exportable as markdown; omp's `checkpoint`/`rewind`/snapcompact
  prove deterministic compression works without an LLM [S7][S9]. But the Plan 2
  relay targets the case omp's handoff does not: the *source* harness is dead
  (limit) or unwanted (model change), and the state must be readable by a
  *different* harness than the one that died. omp's `/resume @claude/@codex`
  imports into omp; Lhassa's relay sends to whatever seat is next. And the
  cold-start premise now has independent, measured support: a resume of a long
  session after cache expiry re-pays the full history (and `/compact` doesn't
  re-prime the cache), while a small handoff file costs a few hundred tokens
  [S27]; the exact Plan 2 claim, observed on a real bill.
- **Bench design has a public template.** [S15]'s token-per-query pricing and
  [S14]'s cold-start-waste analysis give LhassaBench Fase 2 the metric shape:
  tokens-to-completion + success + contradiction rate across the four
  strategies. [S1]'s "first 10% of context" rule is a concrete budget rule the
  recorder's destilado can adopt.
- **Honest divergences:** (a) Plan 2's "~2k tokens destilado" exceeds [S1]'s
  ~500-token handoff norm; to be settled by bench, not by convention; (b)
  nothing in the prior art measures "contradicción A→B determinista via diff"
 ; that is Lhassa-specific and unproven until Fase 2; (c) the 90%-re-derivable
  claim remains a hypothesis: Fase 0.2's own-transcript anatomy is the first
  measurement (earlier pilot on this machine: witara 94.5% tool_result by
  chars, agentsprint 78.1%, lhassa 35.9%; a wide spread to explain before
  betting the relay); (d) the claim must be narrowed post-[S30]: the relay's
  cost edge is real only in the **cache-cold / cross-harness / post-limit**
  case, not in a warm same-harness resume (where caching makes re-send cheap).
  The bench's four arms already isolate this if arm (c) 'cold start with
  recorder' is staged *after* cache expiry; otherwise it will understate the
  win, not overstate it; (e) [S33]'s key System result; that an LLM-mediated
  write path is orders of magnitude more expensive and latency-unbounded than a
  deterministic one; is an independent argument *for* the deterministic
  recorder (derive from events+git, never an LLM summarizer), but it also warns
  the recorder must batch/defer its write path to avoid blocking the paying
  loop (construction is prefill/embedding-dominated and blocks batch workers).

## Not yet verified by us; and the experiment that would

### Experiment: transcript re-derivability (Fase 0.2, read-only)
- Baseline: no harness data point exists for "what fraction of a transcript is
  re-derivable from disk" measured mechanically on real sessions.
- Instrument: own transcripts (`~/.claude/projects/*.jsonl`, ~520 files/365MB),
  a deterministic parser mapping each content block to
  (tool_result → re-derivable from disk) vs (text/thinking/tool_use.input →
  not), per harness (claude-code, codex, opencode, omp where present).
- Metric: % of chars/tokens re-derivable, per harness and per task type;
  spread across the 3-4 harnesses; what fraction of non-derivable text is
  decisions vs. reasoning vs. planning.
- Calibration/positioning now possible: [S29] gives the SE-domain expectation
  (~80-84% of SE context is environment observation) to compare against;
  [S32]'s R-score + echo protocol is a public instrument to score
  reconstructability and shows content-addressing collapses storage with R
  unchanged; a design cue for the recorder's own artifact layout.
- Stop rule: enough sessions to stabilize the per-harness median (±5pp over a
  rolling window); then a verdict for the Gate de Fase 0 (adopt / replan
  toward destilado-con-modelo).

### Fase 0.2; measured (2026-08-05, `app/research/fase02_transcript_anatomy.py`)
Instrument built + run over all three harnesses' real transcripts (read-only,
no LLM involved, deterministic parser, ~4 s wall):

| harness     | runs | re-derivable chars | red % (chars) | median run red % |
|-------------|-----:|-------------------:|--------------:|-----------------:|
| claude-code |  518 |        38,871,837  |         60.8% |            67.8% |
| codex       |   76 |        19,535,065  |         80.3% |            73.4% |
| opencode    |    1 |         6,879,402  |         64.9% |            64.9% |

Verdict for the 90% premise:
- **Confirmed in kind, not in magnitude.** Across all three harnesses the
  majority of transcript content is re-derivable from disk (61-80%), but
  *below* the ~80-84% SE-domain figure [S29] once tool_use.input (the model's
  own decisions; the exact thing a relay must carry) is counted as non-derived.
- **The destination-of-record split is what matters, not the headline.**
  Non-derivable content breaks down as: claude-code 62.7% tool_use.input +
  37.3% text (thinking 0%: Claude Code stores thinking as a signature, no body);
  codex 27.5% call + 72.4% text; opencode 30.7% call + 25.7% text + **43.6%
  thinking** (opencode persists the full reasoning trace; the only harness of
  the three that does).
- **Task-type spread is real and widens the A/B:** aggregated re-derivability
  runs 47-70% depending on task type (`other` 69.9%, `code` 62.5%,
  `domain/venta` 47.2%, `workspaces` 47.0%). Code/domain tasks carry proportionally
  more decision text; precisely the content a destilado must preserve. Bench
  tasks for Fase 2 should be drawn from the `code`/`domain` buckets, not `other`.
- **Consequence for the relay design:** a recorder that derives only tool_result
  from disk still leaves ~25-40% of the transcript that is *not* re-derivable and
  *must* be carried in the destilado (decisions in tool_use.input + assistant
  text, and; for opencode/Codex-sourced sessions; reasoning). This is the
  measured upper bound on how much a pure-git/event derivation can drop before
  it collides with the model-recall (destilado) arm. The Gate de Fase 0 number
  to adopt: **re-derivability ~60-80% by chars; non-re-derivable ~20-40% is the
  true destilado floor**; closer to the plan's 2k-token budget than to ~500.

### Experiment: 4-strategy bench (Fase 2, on LhassaBench)
- Baseline: (a) raw transcript re-send, (b) native intra-harness switch,
  (c) cold start with recorder, (d) recorder + cheap-model destilado; the
  plan's four arms [S16], on 3-5 reproducible coding tasks.
- Staging discipline post-[S30][S31]: the arms must be compared under a
  *cold cache* (TTL exceeded, or asserted harness/model switch) or the warm-
  cache advantage of (a)/(b) will mask the recorder's edge. Run each arm twice:
  warm-cache and cache-expired. Record the prefix cache hit rate per turn
  (per [S31]'s CAPC ρ discipline) so the bill is attributable, not assumed.
- Instrument: `lhassa/bench/` extended with a "coding" mode (git worktree +
  verify by command), runs recorded in `bench_runs`.
- Metric: tokens-to-completion (prefill + generated, per [S15]'s pricing
  discipline), success, wall time, contradictions vs. already-done work
  (diff-based).
- Stop rule: per-task differences ≥ 20% in tokens-to-completion with a fixed
  task set; gate: if no recorder arm beats plain cold restart in quality/cost,
  the relay is still built but with the winning strategy (mechanism independent
  of state [S16]).

### Fase 2; instrument built + first measured (2026-08-05, `app/research/fase2_bench.py`)
Built a standalone runner (opencode-instrumented: `opencode run --format json`
per-turn `step_finish.tokens.{input,output,cache.read}` summed per arm; fresh
git worktree per arm; objective verify-by-command, never an LLM judge). Arms
(a) raw re-send, (b) native `--continue`, (c) recorder destilado, (d) cheap-
model destilado. Reuses gateway PRICING: CLI subscriptions are $0 marginal cash
by design, so the honest metric is tokens-to-completion with cache split
(effective_input = in + cache.read×0.1, the S31 rho).

Pilot (task `hello`, arms a and c, warm provider cache on this box):

| arm          | pass | tok_in | tok_out | cache.read | eff_in |
|--------------|------|-------:|--------:|-----------:|-------:|
| a raw re-send| ✔   |  1285  |    560  |   47616    |   6046 |
| c recorder   | ✔   |   937  |    371  |   38400    |   4777 |

Two validated findings ahead of the full matrix:
- **Warm-cache masking is real and loud.** On a trivial task, cache.read
  (37-48k) overwhelms input (0.9-1.3k) in BOTH arms. Under warm cache the
  naive re-send (a) looks nearly free; the exact [S31] caveat; the relay's
  edge can only appear in the cache-COLD arm of the matrix, so the full bench
  MUST stage each arm warm+cold (per the discipline above), or it will bury the
  result.
- **Recorder arm (c) used ~27% less raw input** (937 vs 1285 tok_in) than
  re-send even in this pilot; consistent with the Fase 0.2 destilado shape
  (carry pointers+decisions, not content). Latency for (c) reports the tool
  default 0 (not yet instrumented); capture per-arm wall-time in the matrix.

The instrument is ready for the full 4-arm × cache-cold/warm matrix on the
`code`/`domain` tasks Fase 0.2 flagged (not `other`). Gate decision deferred
to that run.

### Fase 2; matrix run #1 + instrument bugs found/fixed (2026-08-05)
Ran the first 4-arm × 3-task matrix (hello/codegrade/domainledger). Two things
came out of it:

**Valid signal (reproduced, not noise): warm-cache masking is the dominant
effect.** Every arm of every task spent 41-99% of its bill on `cache.read`
(provider cache), dwarfing `input` in all but one case. On the trivial `hello`
task: recorder arms (c/d) passed at 7917/1366 in-tokens vs raw re-send (a)
25801 in; and native-switch (b) at 96. Under warm cache the naive arms look
nearly free; this is the [S31] caveat measured, and it is exactly why the cold
case is the only honest place the relay can win. Recorder arms passed `hello`
with a fraction of the raw input of re-send; the destilado shape holds.

**Two instrument bugs, both fixed in `app/research/fase2_bench.py`:**
1. `--continue` resumes an opencode session at ITS OWN cwd, not the caller's.
   Arm (b) cloned the phase-1 worktree, so verify ran against an empty clone →
   false failures. Fix: arm (b) now runs and verifies in `phase.cwd`, and the
   matrix executes (b) LAST because resuming mutates that worktree while arms
   (a)/(c)/(d) clone it. The cwd-binding is itself a relay-relevant finding:
   native harness resume is anchored to its cwd, so cross-cwd switches cannot
   use it; the relay's cold-start is the only path that ignores cwd.
### Fase 2; corrected matrix on controlled task `hello` (2026-08-05)
After fixing the arm-(b) cwd bug, the clean 4-arm comparison on the controlled
task (`hello`, files seeded in phase-1, verify checks only the agent's output):

| arm            | pass | tok_in | tok_out | cache.read | warm | cold | rho% | lat_s |
|----------------|------|-------:|--------:|-----------:|-----:|-----:|-----:|------:|
| (a) raw re-send| ✗   |  3810  |   445   |  45056     | 8315 | 48866| 92%  | 20.0  |
| (b) native     | ✔   |   474  |    69   |  18944     | 2368 | 19418| 98%  |  9.4  |
| (c) recorder   | ✔   |   994  |   283   |  27392     | 3733 | 28386| 96%  | 12.3  |
| (d) rec. cheap | ✔   |   918  |   248   |  27520     | 3670 | 28438| 97%  | 16.1  |

Read (caveat: n=1, controlled trivial task, warm provider cache on this box):
- **Warm cache masks almost everything** (rho 92-98% across arms): on a warm
  cache the naive re-send (a) at 8.3k warm-toks ≈ recorder (c/d) at 3.7-4k,
  the edge is real but ~2×, drowned by cache. The relay's win can only show
  cleanly under cold cache (cold column: a=48.9k vs c=28.4k, still ~1.7x).
- **Arm (a) raw re-send FAILED** (✗) while all others passed at this seed; the
  burned in-tokens (3810) with a full re-sent transcript did not reproduce the
  seeded file correctly, consistent with the relay thesis: re-sending the
  transcript is not just expensive but lossy. (n=1; needs repetition to say
  more.)
- **Recorder (c/d) ≈ native (b)** on cost here (both ~2.4-2.7k native plus
  ~0.6-1k cheap-pass), which under a *cold, cross-harness* switch is the whole
  point: (b) native switch is only possible in-harness, the recorder is the only
  general path. The 3 turning arms being roughly equal in-harness is consistent
  with "the relay wins wherever the native path cannot exist".
- Detector confirmed: recorder uses less raw input than re-send (918-994 vs
  3810 in-tokens), trading a small output cost for not resending.

**This is n=1 on one trivial task; the honest next step is n≥3 across the
`code`/`domain` tasks with cache-cold staging before the gate (Relay > plain
restart).** The instrument, after the two bug fixes, is correct and ready for
that run.

### Fase 2; repetition run n=3 on `hello`/`codegrade`/`domainledger` (2026-08-05)

Ran arms a/c/d (b native once more skipped: cwd-bound and already measured) ×3
seeds ×3 tasks, verified greps made robust (objectively check the agent's
output, tolerant of formatting). Full rows: `/tmp/opencode/fase2_n3.log`.
Per-arm means (cold = in + cache.read): raw re-send 0/9, recorder 3/9, rec.
cheap 3/9.

| arm            | pass (n=3) | mean in | mean cold | task breakdown            |
|----------------|-----------:|--------:|----------:|---------------------------|
| (a) raw re-send | 0/9       |    10362 |     57651 | hello 0/3, codegr 0/3, dom 0/3 |
| (c) recorder    | 3/9       |     2133 |     43989 | hello 3/3, codegr 0/3, dom 0/3 |
| (d) rec. cheap  | 3/9       |     3663 |     40399 | hello 3/3, codegr 0/3, dom 0/3 |

Read (honest, n=3):
- **Re-send (a) is not just expensive, it is actively harmful, reproducibly:**
  it failed **0/3** on `hello`, the trivial task, while the recorder passed
  **3/3**. With the full transcript stuffed into the prompt the model does
  worse at reproducing seeded files than a plain cold start with a destilado.
  This is the strongest relay argument so far, and it is not a single-seed
  fluke: the detector's cheapest arm beat the naive baseline on every seed.
  (Earlier single-seed run #1 already flagged this at ✗ on (a); it was not
  the noise I feared.)
- **`codegrade`/`domainledger` are below model-accuracy ceiling after the
  switch**: 0/9 for recorder too (0 passes, all arms). Not a comparator; the
  cheap model can't meet the objective verify even with a clean destilado.
  They measure *capability*, not relay cost; drop them from the gate.
- **On the task in range (`hello`), recorder (c) ≈ recorder cheap (d) ≈
  ~44k/40k cold at 100% pass, vs re-send 0%.** Gate Direction is not yet
  decided on magnitude (needs a richer corpus of *in-range* tasks), but the
  *direction* is now clear and repeated: destilado >> raw transcript for
  completion under cold cache.
- **Cache-cold staging confirmed as the make-or-break:** rho 59-98% per run;
  everything is cache-dominated until provede; cold column is where the gate
  must be judged, and there recorder holds.

**Next (gate de Fase 2):** stop assuming, instead assemble ~5-8 in-range
`code` tasks (tasks the model can actually meet an objective verify of) and
repeat n=3 each; compute the relay's tokens-to-completion mean (recorder
cheap = cold-start floor) against a plain-restart baseline, then flip the
stop-rule gate.

### Fase 2; GATE DECIDED: n=3 × 6 in-range tasks (2026-08-05)

Replaced the below-ceiling comparators with a reliable corpus shape (write a
module + a test that imports it + run the test; the form this model actually
meets; the standalone-script form was unreliable for this cheap model, see
codegrade/domainledger above). 6 tasks × arms a/c/d × 3 seeds = 54 runs, each
row persisted abort-safe to `app/research/results/gate_20260805.jsonl`.
`--report <file>` re-summarizes without re-running.

| arm            | pass   | mean in | mean cold |
|----------------|-------:|--------:|----------:|
| (a) raw re-send | 0/18  |   5532  |   58318   |
| (c) recorder    | 18/18 |   1010  |   36260   |
| (d) rec. cheap  | 18/18 |    764  |   35132   |

Read:
- **The gate is decided and it is not close: raw re-send fails 0/18 where the
  relay's destilado passes 18/18, on every task and every seed.** This is the
  [S31] promise reproduced cleanly under cold cache: stuffing the full prior
  transcript into the prompt does not just cost ~1.6-1.7x more cold tokens
  (58.3k vs 35-36k); it actively prevents completion (0% vs 100%). The relay
  niche is real in the cold case, and the destilado is the mechanism.
- **Recorder cheap (d) ≈ recorder cold (c) and slightly cheaper (35.1k vs
  36.3k cold)**; the cheap-model pass is nearly free and never degrades the
  pass rate. That is the relay's standing cold-start floor.
- **rho 90-100% across runs: cache dominance confirmed at scale**; the honest
  comparison is the cold column; warm (not shown, ~4-11k) is the masked view
  that made n=1 look like a wash.
- **Instrument finding, paper-worthy:** the standalone-script task form
  (codegrade/domainledger) was below this model's reliability even WITH a clean
  destilado (0/9), while the module+test form is at ceiling (18/18). Capability
  is task-shape-dependent; cost A/Bs must be run on tasks inside the model's
  reliability envelope or they measure capability, not strategy.

**Gate de Fase 2 = PASS (Relay > plain re-send, cold):** destilado >> raw
transcript for completion (100% vs 0%) at ~60% the cold tokens. Fase 3 (the
relay as cross-harness swarm reconciliation) proceeds on this footing.

> ## ⚠️ INVALIDATED; runner bug discovered 2026-08-05 (read before trusting Fase 2/3)
>
> **`fase2_bench._run_opencode` invoked opencode as an ARGV LIST
> (`subprocess.run([...])`), under which opencode refuses to write files**
> (rc=0, model "works" ~9k tokens, zero files on disk). With the prompt passed
> via the SHELL (`shell=True`) the same prompt writes files. Cause was isolated
> by direct comparison (shell=True → file written; argv-list → empty dir),
> reproduced 4x, and fixed in `_run_opencode` (now `shell=True` with shlex
> quoting).
>
> **Consequence:** every Fase 2/3 pass/fail row measured through the old
> runner is suspect. Re-running the Fase 2 hello matrix with the corrected
> runner flips the headline: **raw re-send (a) now PASSES (1/1)** alongside
> recorder (c/d). The "re-send is destructively lossy (0/18)" result was an
> artifact of the broken runner, NOT the model. Under the broken runner the
> seeded files (`task.files` via `_make_worktree`) made recorder arms pass and
> the re-send arm's clone-path fail for runner reasons, not relay reasons.
>
> **All Fase 2 and Fase 3 conclusions must be re-measured under the corrected
> runner before any of them is usable.** The relay may still win; the cold-token
> gap (36k vs 51k at n=1) survived; but the *completion* result (0% vs 100%)
> that anchored the gate is void. This is the session's most important
> methodological finding: a silent file-write failure in the invocation path
> (argv vs shell) produced a publishable-looking but wrong result.

### Fase 2; RE-MEASURED under corrected runner (2026-08-05): gate holds on cost, dies on viability

Full gate re-run: arms a/c/d × 6 in-range tasks × n=3, `--results
gate_corrected_20260805.jsonl`. With the runner that actually writes files:

| arm            | pass   | mean in | mean cold |
|----------------|-------:|--------:|----------:|
| (a) raw re-send | 18/18 |   11442 |   47993   |
| (c) recorder    | 18/18 |    7436 |   35638   |
| (d) rec. cheap  | 18/18 |    7272 |   34913   |

- **Viability claim DEAD:** re-send now completes 18/18; the earlier 0/18 was
  the runner bug. The relay is NOT the only path on these tasks.
- **Cost claim SURVIVES, modest:** recorder ≈ 1.35x cheaper cold (35.6-34.9k vs
  48.0k) at equal completion. Honest headline: same capability, ~26% fewer cold
  tokens. Not the dramatic "only the relay completes", but a real, clean,
  unconfounded number.
- **Caveat recorded:** `task.files` seeds already contain the solution, so the
  verify measures "does the arm NOT corrupt the seed" rather than "does it
  create the deliverable". Valid as an A/B (re-send corrupts more than
  destilado), but weak as absolute capability. A create-from-scratch variant is
  the honest next instrument if this line continues.

### Fase 4; viability probes stalled on instrument; model-switch probe built (2026-08-05)

- The saturation/portability probes were blocked by (1) a hallucination of the
  cheap model ("the file already ends with FLAG_X" when it did not; fixed by
  per-run random nonce flags), and (2) the same argv-vs-shell write failure that
  invalidated Fase 2. `fase4_probes.py` now runs via shell=True with short
  timeouts. Re-running the probes under the corrected runner is pending.
- **claude headless findings (measured, needed for the bench):**
  `--dangerously-skip-permissions` is REQUIRED for claude `-p` to write files;
  `-c` (continue last) retains tool-write capability while
  `--continue <session_id>` does not reliably. Without these the model "works"
  and nothing lands on disk; the same silent-write failure class.
- **New probe `model_switch` (in-harness, claude A→B):** answers the user's
  reload question empirically. Smoke (n=1, sonnet→opus): re-send 47.9k cold,
  native `-c --model` 73.7k, destilado 72.9k; the native reload is NOT cheaper
  than re-send on this sample (counter to the theory; n=1, needs n≥3).

### Fase 3B; RE-MEASURED under corrected runner (2026-08-05)

Same swarm (claude/codex/opencode, 6 in-range tasks × 2 seeds = 36 workers)
under the shell=True runner that actually writes. `--results
fase3_corrected_20260805.jsonl`:

| harness  | pass | mean cold | mean lat |
|----------|-----:|----------:|---------:|
| claude   | 12/12 |  100116 | 20.8 s |
| codex    | 12/12 |   67328 | 17.3 s |
| opencode | 12/12 |   36739 | 12.8 s |

- **Harness-agnostic claim SURVIVES the corrected runner:** 36/36 complete the
  same destilado on three harnesses with different accounting/models. This was
  the strongest claim and it holds.
- **Merge now detects REAL divergences:** under the broken runner the "12/12
  clean" was vacuous (nothing was written). Corrected: 10/12 clean, 2/12 report
  `conflict:hello.py` / `conflict:test_fib.py`; independently-passing workers
  diverged on a seeded file and the deterministic state-merge caught it. That
  is the contradiction rule (revert + owner approval) doing its job, not a
  failure: silent semantic divergence is now visible.
- **Swarm economics unchanged:** claude ~2.7x opencode cold; codex ~1.8x. The
  ×N cold-start ledger still decides whether parallelism pays.

### Fase 5; improving the relay: enriched destilado (decisions+diff), NEGATIVE (2026-08-05)

Built `app/research/fase5_enrich.py`: a rich destilado that extracts the
phase-1 transcript's decisions/reasoning (`_extract_decisions`: text+thinking
events, the non-rederivable Fase 0.2 35-50%) and the git diff of what phase-1
changed, instead of the current "see the task description" summary. Two task
shapes, measured:

- **Decision task (bug diagnosable from code):** poor 3/3 @ 36.9k cold, rich
  3/3 @ 37.9k cold. No completion gain, rich slightly costlier.
- **External-decision task (requirement NOT in the repo, phase-1 receives it as
  context):** poor 1/1 @ 36.9k, rich 1/1 @ 37.4k. Even without the decision in
  the destilado, the model re-derived the requirement from the verify's expected
  output. Forcing the gap would require hiding the verify's answer; i.e.
  rigging the task to produce the wanted result. Not done.

**Honest read:** on tasks within this cheap model's re-derivation ability, an
enriched destilado does NOT pay; the model re-derives decisions for ~7.3k
in-tokens, cheaper than the cost of carrying them. A+B's premise (decisions are
the expensive non-rederivable part) only holds where re-derivation is genuinely
costly or impossible (very large context, or knowledge external AND not
revealed by any artifact the fresh agent can see).

### Fase 5; Pareto probe (D): destilado size vs completion (2026-08-05)

Truncated the rich destilado to 200/500/1000/2000 chars (n=2 each):

| budget | pass | mean cold |
|--------|-----:|----------:|
| par200 | 2/2 |  36798 |
| par500 | 2/2 |  36773 |
| par1000| 2/2 |  37412 |
| par2000| 2/2 |  37398 |
| rich(full) | 2/2 | 37387 |

**The Pareto curve is FLAT: 200 chars complete as well as the full destilado.**
The relay's standing cold-start floor is bounded by the target model's fixed
system prompt, not by the destilado; shrinking the destilado to ~200 chars
saves nothing measurable on these tasks because the model re-derives the
substance anyway. (Char-truncation may split words; a token-true truncation
would be cleaner but the curve's flatness already answers the question.)

**Fase 5 synthesis:** improving the destilado (content or size) does not pay
on tasks this cheap model can re-derive. Both negative results share one root
cause: the model re-derives context for ~7k in-tokens, so carrying context is
waste; the relay's value stays where re-derivation is impossible (the Fase 4
saturation/portability probes, still pending under the corrected runner).

### Fase 4; RE-MEASURED under corrected runner: saturation WINS big, portability partial-negative (2026-08-05)

**Instrument fixes along the way (all measured):** (1) the saturated re-send
blew past ARG_MAX (`OSError: Argument list too long`); fixed by piping the
prompt on stdin (`opencode run -`); (2) `input=` + `stdin=PIPE` together
raised ValueError; fixed; (3) `text=True` stdout must not be `.decode()`d.
Saturation probe (append unique flag to a big file, objective verify):

| context size | arm (a) re-send | arm (b) resume | arm (c) destilado |
|--------------|----------------:|---------------:|------------------:|
| ~15k tokens  | 76.9k cold      | 51.8k cold     | **17.7k cold**    |
| ~130k tokens (post-limit) | 159.8k cold | 107.0k cold | **27.0k cold** |

- **THE CLIFF (the relay's real value, finally measured): at post-limit
  context the destilado is ~5.9x cheaper than re-send and ~4x cheaper than
  native resume, all at equal completion (3/3 passes).** Fase 2's "1.35x" was
  an artifact of tiny contexts; when the re-send must re-prefill a huge
  transcript, the destilado's smallness is not a rounding error but a 6x
  difference. This is the first Fase-4 result that makes the relay matter in
  magnitude, not just direction.
- **Portability is a PARTIAL-NEGATIVE (and it contradicts Fase 2's arm-b
  finding):** native opencode resume (`--continue <session>`) from a MOVED
  path survived (arm b pass, 426 in-tokens, flag written to the new path),
  the "resume is cwd-anchored and dies on move" claim from Fase 2 did NOT
  reproduce. The destilado also passed (arm c). So portability-as-viability
  is weakened by its own instrument; the migration argument must be re-tested
  in the honest form: moving to a CLEAN HOME (no ~/.claude, no opencode.db),
  where no native state can travel. (n=1; needs replication.)
- The merge probe ran on 2 passing workers; see the JSONL for rows.

### Fase 6; the CLIFF: no completion cliff, but the cost gap is exponential (2026-08-05)

`app/research/fase6_cliff.py`; 4 context scales × arms (a re-send, b resume,
c poor destilado, d rich destilado), objective verify (append unique flag).
Cold tokens per arm (all pass unless marked):

| scale   | (a) re-send | (b) resume | (c) poor | (d) rich |
|---------|------------:|-----------:|---------:|---------:|
| 36k     |   177,793   |  119,100   | 45,443   | 26,933   |
| 110k    |   488,292   |  162,912   | 75,179   | 36,710   |
| 230k    |   992,776   |  662,548   | 27,082   | 26,797 (✗ n=1) |
| 370k    | 1,054,361   | 1,054,075  | 97,272   | 26,991   |

- **NO completion cliff exists in this range**: re-send COMPLETES even at 370k
  tokens (1.05M cold); opencode via stdin handles it, the model finishes. The
  "relay as the only path" framing (re-send impossible) does NOT hold for
  completion in this corpus. What holds is cost.
- **The cost gap is EXPONENTIAL, not a rounding error:** re-send grows linearly
  in transcript (177k → 1.05M), destilado stays ~flat (27k-97k). At 370k the
  destilado is ~39x cheaper than re-send, ~39x cheaper than native resume. Fase
  2's "1.35x on trivial tasks" was the wrong regime; the relay's real economics
  appear at scale: **the re-send pays the full prefill on every resume, the
  destilado pays its fixed size.** This is now the relay's strongest measured
  property; not completion, but scaling.
- **Rich vs poor at scale: still no content advantage** (d≈c at every scale,
  both ~27k, plus one n=1 rich failure at 230k). Fase 5's negative holds even
  where re-derivation is expensive; carrying decisions does not pay; the
  cheap model re-derives anyway, or the task is simple enough not to need it.
- **Native resume (b) is also linear in the transcript** (119k → 1.05M),
  ~= re-send at large scale. Neither re-send nor resume scales; only the
  destilado does. This is the decisive empirical statement for the relay.

### Fase 6; extended: THE COMPLETION CLIFF exists (~550-650k), relay is the only path beyond (2026-08-05)

Pushed past the first run's 370k. At ~650k+ the re-send hits opencode's
**`ContextOverflowError`**; the transcript no longer fits the model window.
The "relay as the only path" framing, which Fase 2-6 (≤370k) said did NOT
hold, HOLDS at scale:

| scale   | (a) re-send outcome |
|---------|---------------------|
| ~370k   | completes, 1,054k cold |
| ~400k   | completes, 1,137k cold |
| ~550k   | completes, 1,557k cold |
| ~650k   | **ContextOverflow (impossible)** |
| ~880k   | **ContextOverflow (impossible)** |

The completion cliff is between ~550k and ~650k tokens of transcript. Below it
the re-send completes but pays linearly (1.5M cold at 550k); above it the
re-send CANNOT RUN; no tokens, opencode rejects the prompt before the model.

**At 880k (impossible for re-send), both destilados complete 3/3 (n=3):**
poor 3/3 @ 60.2k mean cold, rich 3/3 @ 34.7k. The relay is the ONLY path
beyond the cliff, at ~1/30 the cost of what the re-send paid at 550k.

**Content DOES matter at extreme scale (a Fase-5 negative that flips):**
the first 880k run had poor FAIL and rich PASS (n=1); at n=3 both pass but rich
is ~0.58x poor's cold (34.7k vs 60.2k) and never the one that fails. The
enriched destilado's cost advantage appears only where re-derivation is
genuinely expensive (huge context); the boundary condition Fase 5 could not
reach on small tasks.

**Synthesis (the relay's true position, now complete):** cost is flat in
transcript size (destilado ~27-97k from 36k to 880k) while re-send and native
resume grow linearly (177k → 1.56M) until they hit a hard completion cliff
(~600k) where only the destilado can run. The relay is not "slightly cheaper
cold" (the wrong regime) and not "better content" (small-task negative); it
is the **only mechanism whose cost and viability are independent of session
size**, and the only one that exists past the context cliff.

### Fase 6; cross-provider cliff (partial): the cliff is provider-dependent (2026-08-05)

The cliff's LOCATION is a property of the provider's context window, not a
universal constant. Measured:

| provider (harness)   | re-send passes | re-send fails |
|----------------------|---------------:|--------------:|
| DeepSeek (opencode)  | ≤ ~550k        | ~650k (ContextOverflow) |
| Anthropic (claude)   | ~150k          | ~250k (api error on 200k window) |

- **Anthropic's cliff is ~3x earlier than DeepSeek's** (~200k vs ~600k): on
  Anthropic models the relay becomes the only path at a fraction of the scale.
  The cliff is a per-provider parameter; the relay's flat-cost property holds
  regardless, its *when-it-becomes-the-only-path* point depends on the window.
- **Second finding: the expensive re-send BURNED Claude's subscription session
  limit.** The ~$8 / 400k-token re-send, on top of the day's runs, hit
  `429 You've hit your session limit`; claude CLI (real-money subscription)
  throttles at a session budget that the relay's ~27k-token destilado would
  never approach. This is a fourth "re-send impossible" case: not context
  overflow, but the operator's session budget.
- **Box limitation recorded:** opencode only serves deepseek-v4-flash without a
  payment method; gemini/claude/gpt via opencode → `CreditsError`. claude CLI
  uses its own subscription (works, but real $ and session-limited). A
  systematic cross-provider matrix needs either a funded opencode account or
  per-provider CLI subscriptions; not available today (claude 429 until
  ~11:40 local).

### Fase 6; cross-provider cliff COMPLETE (claude + codex CLIs used): 3 providers, 3 different cliff mechanisms (2026-08-05)

Using the installed claude and codex CLIs (their own subscriptions), mapped the
re-send cliff across providers. **Each provider's cliff is a DIFFERENT
mechanism**:

| provider (harness) | cliff mechanism | passes | fails |
|--------------------|-----------------|-------:|------:|
| DeepSeek (opencode) | model context window | ~550k tok | ~650k tok (`ContextOverflowError`) |
| Anthropic (claude CLI) | 200k model window | ~150k tok | ~250k tok (api_error) |
| OpenAI (codex CLI) | **hard 1,048,576-char input limit** | ~1.03M chars (~280k tok) | ~1.22M chars (~330k tok, `input_too_large`) |

- **Anthropic's cliff is the earliest** (~200k tokens; the model window).
- **OpenAI's cliff is a CHARACTER limit, not a token one** (codex rejects input
  > 1,048,576 chars regardless of model); it lands at ~280-330k tokens with our
  filler format, but the limit is chars so it bites sooner on verbose transcripts.
- **DeepSeek's cliff is latest** (~600k tokens); its model has the biggest
  window here.
- **The destilado crossed every cliff:** codex accepted the destilado (~42k
  in-tokens) where it rejected the re-send (`input_too_large`); claude's
  destilado completed at 150k (its 429 is a subscription budget, not a
  capacity limit). The relay's flat-cost + fits-anywhere property is
  provider-independent; only the *where-the-relay-becomes-the-only-path* point
  varies (200k Anthropic → 600k DeepSeek).
- **Third "re-send impossible" case, measured:** codex's hard char cap is
  purely mechanical; a long verbose transcript (logs, diffs) exceeds it
  regardless of how good the model is. The relay sidesteps it by never
  re-transmitting the transcript.
- **Billing divergence recorded:** opencode+deepseek is $0 marginal here;
  claude CLI is real money and session-limited (the ~$8/400k re-send burned the
  day's budget → 429); codex is its own subscription. Cross-provider bench cost
  is not comparable in $, only in tokens.
- **Anthropic replication (after 429 reset, n=3):** the claude destilado at
  ~250k-token file completes 3/3 (flag written, ~10k cache-creation tokens)
  where claude's own re-send fails on the 200k window. The relay crosses the
  Anthropic cliff reliably, not by luck.

### Methodology audit (2026-08-05, subagent reviewer); the honest bottom line

A severe validity audit of the whole bench. Executive verdict: the note handles
the runner-bug invalidation well, BUT "the relay being measured is not the
relay being described". What was benched is *cold start with a static state
placeholder + tasks whose solution is already seeded on disk and whose verify
passes by doing nothing*. Key findings (full report in the audit):

- **C1/C2; the seed is the dominant confound:** `task.files` writes the
  CORRECT solution before any arm runs; `_verify` passes on rc==0 of an
  already-correct test → a run that writes NOTHING passes. The corrected
  gate's "18/18 everywhere" has zero discriminating power. No create-from-
  scratch variant exists.
- **C4; Fase 6 arm (b) never resumed:** `sess` was captured and discarded;
  `_run_opencode_model` has no `--continue`. "Native resume is linear" was
  built on an arm that never resumes (fits the data: b≈a at 370k).
- **C5; codex ran `--sandbox read-only` in Fase 3B:** cannot write files; its
  12/12 "pass" is vacuous. The "36/36 harness-agnostic" headline is not
  demonstrated.
- **C6; Fase 4 saturation arms share cwd + flag:** arm (a) appends the flag,
  arm (b) then verifies a file that already has it → b's "pass" is vacuous.
- **C8; spec leak in prompts:** all task.prompt embed the expected verify
  outputs, so any "external decision" is inferable from the prompt itself.
  Fase 5's negative (model re-derives) is an artifact of the leak.
- **C9; the recorder never ran:** `recorder_destilado` is a STATIC template
  (no phase-1, no events, no git diff). The deterministic events+git recorder
  that is the product's heart was never executed in any instrument. The flat
  cost law is nearly tautological (fixed-size prompt → fixed tokens).
- **C10; cache accounting inconsistent across harnesses** (opencode discards
  cache_creation, claude counts it as input, codex as cache). Fase 3B's cost
  ranking is partly an accounting artifact.
- **C12; "cold" was never staged:** all runs had warm cache; "cold" is
  post-hoc arithmetic (`in + cache.read`), not a measured condition.
- **C13; cliff is n=1 per point,** in estimated tokens (`n_lines*10`, never
  validated against a tokenizer); destilado "flat 27-97k" is a 3.6x range.
- **C16; merge probe aborts on first byte-diff:** measures byte-identity, not
  semantic conflict; merge probes not persisted.
- **Reporting gaps:** Fase 5/6 have no raw JSONL in `results/` (paths were
  /tmp); no analysis script regenerates the note's tables; no timestamps/seeds/
  model versions per row; arm (d) degrades silently to (c) ~50% of the time.

**What survives the audit:** ONE robust, narrow result; for deepseek-v4-flash
via opencode, a fixed-size prompt + disk pointer costs flat tokens (27-97k)
while pasting the full content into the prompt grows linearly (177k→1.56M) and
eventually overflows (~600k, ContextOverflowError); claude (~200k window) and
codex (1,048,576 chars) have their own cliffs, and the destilado crosses all of
them. True, measurable, honestly caveated; but it measures "passing the whole
file through the prompt" on tasks whose entire state is on disk.

**Verdict (product vs research):** this is a RESEARCH result (a paper on
prefill economics of a cheap model, cache-cold, state-100%-on-disk) IF
published with its caveats or re-measured. It does NOT license building the
product: the deterministic recorder (events+git, carrying the 20-40%
non-rederivable) was never executed; what was benched is its best-case
imagination. **The build decision should not be made on this evidence.**

**The 3-5 questions whose answers would change the decision:**
1. Create-from-scratch with HIDDEN spec (verify can't pass by doing nothing;
   spec not inferable from prompt); does the destilado still complete? i.e.
   does the relay actually carry the non-rederivable 20-40%, or only work where
   the state IS the file?
2. Correct native resume, uncontaminated, per-harness with its own model,
   is `--continue`/`-c`/`resume` really costlier than the destilado? (today:
   zero valid measurements)
3. Does the cliff generalize to STRONG models (opus/gpt-5/o3), and does the
   relay still win net after including a REAL recorder's construction cost?
4. On tasks with state NOT 100% on disk (Fase 0.2 `domain`/`workspaces`
   buckets, ~47% re-derivable), what fraction does a pointer-only destilado
   complete vs re-send, with a verify that doesn't leak the spec?
5. Does the merge detect SEMANTIC conflicts (compiles alone, breaks merged) on
   overlapping frontiers, or only byte-diffs? And does the relay fit Lhassa's
   actual flow (`flows/engine.py` + `runner._stream`) without blocking the
   paying loop?

**Method note (paper-worthy, from an aborted run):** `fase2_bench.py` now
emits each completed row to a JSONL the moment it lands (`--results`), and
`--report <file>` re-summarizes partial data. The first full gate run (~54
opencode runs) was aborted mid-flight and lost everything because the summary
only printed at the end (the pipe was buffered). Any cost-A/B bench that runs
real model calls must persist per-row, not per-batch; aborts are a scheduled
event, not an accident.

### Not yet verified (shorter)
- omp's snapcompact SQuAD numbers (200k-token evals); reported by omp, not
  reproduced; would matter only if Lhassa adopted pixel-frame compaction.
- [S13]'s MCP 2.0 "agentic session tokens"; snippet-level, UNVERIFIED against
  primary docs; relevant only if Lhassa's relay ever talks a wire protocol
  instead of files.
- Letta sleep-time compute quality; no accuracy number captured; relevant as
  an architectural analog (background consolidation while idle) that Lhassa's
  `distill_memory`/`consolidate.py` already implements in miniature.

## In-depth readings (Fase 0 continuous, full text)

### [S32] "The Hidden Footprint: Making Storage a First-Class Metric for LLM Agent Evaluation" (arXiv:2607.11149)

Read in full (PDF, ~13 pp + appendices). Verdict: the single most useful paper
for Fase 0.2's instrument design, because it turns "re-derivability from disk"
from a hypothesis into a *scored, benchmarkable* property.

What it measures. Agent evaluation has ignored what an agent run *leaves on
disk* (logs, context snapshots, checkpoints, debug traces). The authors build
AgentFootprint, a cross-framework benchmark of post-run storage footprint, and
a serialization-aware metric suite: total retention, channel composition,
duplication, growth, compressibility, and **conversation-history
reconstructability**. Headline: replaying one fixed trajectory through seven
persisting agent frameworks yields a **6.7× spread in retained bytes**; at
identical models/tools/tasks, configs at 100% accuracy differ **15.7×** in
retained bytes.

Three findings matter directly to the recorder:

1. **R-score (reconstructability) is well-defined and mechanically scored.** An
   automated `R` rubric (0-3) derives from per-channel structure + whole-line
   probes; `R=3` stores reconstruct the *full* conversation. Validated by
   **full-history reconstruction**: replaying behind a logging proxy (194 calls),
   every `R=3` store rebuilt the conversation field-by-field; **194/194 calls
   reconstructed exactly as contiguous subsequences**. Strong existence proof
   that transcripts *are* re-derivable from disk, and that the property is
   measurable, not claimed.
2. **Most of the storage spread is removable redundancy, not a requirement.**
   The same `R=3` reconstructability is achieved at retained sizes from 0.33 MB
   (OpenAI Agents) to 5.10 MB (LangGraph); **content-addressing reduces all
   seven persisting configs to 0.05-0.50 MB with R unchanged**. An independent
   quantitative confirmation of "~90% re-derivable + dedupe from disk/git", that
   ALSO hands the recorder a concrete artifact storage discipline
   (content-addressed, deduplicated).
3. **A measurement trap for Fase 0.2:** naive byte-level measurement
   understates duplication by an order of magnitude because DB paging and JSON
   escaping obscure repeated content (they report the same content stored 12×
   yet read as near-novel). Any %-re-derivable by-chars metric must fingerprint
   at a content level (whole-line + normalized), not bytes; the caution to
   carry into our parser. They also note "line-numbered tool outputs" survive
   re-formatting probes, which is the exact *tool_result → re-derivable* mapping
   our Fase 0.1 presses.

Limitations: results describe *versioned framework defaults*, not frameworks in
the abstract; `R` scores history reconstruction only, not crash-resume/workflow
state/debug provenance; "same R at very different sizes" does **not** prove the
larger store is pure waste (it may serve other capabilities). For Lhassa: adopt
the scoring protocol + content-addressing discipline, run on our own
transcripts, and do not import their framework roster/adapter numbers as truth.

### [S33] "Agent Memory: Characterization and System Implications of Stateful Long-Horizon Workloads" (arXiv:2606.06448)

Read in full (Stanford + KU Leuven, arXiv:2606.06448). Verdict: the
systems-arm paper the relay needs; it characterizes the *write path* the
recorder would consume and proves determinism is the cheap path.

What it does. First systems characterization of agent memory: ten systems across
four paradigms (I long-context, II flat RAG, III structure-augmented RAG,
IIIa append-only / IIIb consolidating,, IV agentic control flow), profiled
with a phase-aware harness attributing cost to construction / retrieval /
generation on LongMemEval_S* and their own MemoryArena.

Key results for the relay:

1. **Construction (the write path) is embedding- and prefill-dominated.**
   Median decode share of total construction tokens is **4.6%** (range 0.9%
   SimpleMem to 28.5%). Reading long history + emitting compact structured
   outputs; precisely the shape of the recorder's destilado. The expensive part
   of the write path is the prefill/embedding pass over history and the batching
   shape, not the generation. A deterministic recorder that derives from
   events+git skips the LLM write pass entirely.
2. **LLM-mediated construction is unbounded-cost; deterministic is cheap.**
   Construction wall-time spans **five orders of magnitude** (~10⁻³ s BM25 →
   ~10 s Mem0/MIRIX, >100 s in tails). Letta ingested 1.8M tokens in **>13h**
   (vs <1 min BM25/embedRAG) at **28-47× energy per correct answer**
   with comparable accuracy. The expensive systems are exactly the LLM-mediated
   ones (IIIb/IV). Independent systems evidence that "deterministic ≪ LLM
   mediated" holds at the cost layer too.
3. **Construction work can block the paying loop.** Construction tasks are
   repeated long-read → short-write; when they share an LLM endpoint, large
   construction prefills cause head-of-line blocking on user-facing queries.
   For Lhassa's bench this is a mentorship constraint: the recorder's write path
   must be batch/deferred and **never inlined into the paying generation loop**.
4. **Paradigm-I long-context memory pays quadratic prefill with history
   length** and suffers the U-shaped performance drop; the systems-side
   restatement of the anti-model quadratic term [S28]; the math that says
   "small state instead of full history" is not just cheaper but better.

Caveats: benchmark-centric (LongMemEval-style recall); measured on a vLLM-local
+ OpenAI-remote hybrid, so absolute latencies don't transfer; but the
*ordering* (deterministic ≪ LLM-mediated; construction ≫ QA) is robust. For
Lhassa: adopt the phase-aware attribution + "defer writes, never block the
paying loop" rule; do not adopt their serving stack.

## Fase 3; the relay as reconciliation of a swarm (sub-agents, harness-agnostic)

### The idea
Beyond *switching* one episode across harnesses, the same external canonical
state can *deploy* N sub-agents; each in a harness **and model of its own**,
and merge their deltas back deterministically. This is not "add subagents"
(every major harness already does that, and it is not the invention):
it is **harness-agnostic subagent orchestration**; deploying a worker with an
independent body, then reconciling its state-device into the parent's without
context bleeding or LLM-judged merges.

It generalizes the plan's thesis: "external, deterministic, harness-agnostic
state" stops being only a *switch* contract and becomes the *coordination*
contract for arbitrary width. The three sub-problems:

1. **When to split (deterministic).** A subtask is deploy-to-a-worker when it
   is partitionable by *file/interface boundary*; the exact anatomy Fase 0.2
   measures. Split by affected-file frontier + dependency graph, not by the
   parent LLM declaring "I feel like delegating". [S36] (Co-Coder) formalizes
   this as graph partitioning (community detection; coherence vs.
   communication-to-computation trade-off); the closest published statement
   of our "split by boundary" heuristic.
2. **Independent deployment.** Each worker runs the *same* recorded task state,
   but in a different harness/body (codex on one worktree, claude on another,
   opencode on a third; "rent the body" realized as N simultaneous bodies while
   the brain stays one). [S38] (RAH) already spawns full subagent *harnesses*
   recursively and attributes its gains to the harness, not the model; direct
   evidence our width axis is real, though RAH stays inside one runtime/impl.
3. **Deterministic delta-merge.** Workers return a **deliverable + a canonical
   delta**, not raw context. The parent merges by conflict detection at the
   state level (git diff + R-score-style reconstructability [S32]), with the
   contradiction rule (revert + owner approval). [S37] (STORM) shows *incremental*
   state-based resolution beats post-hoc merge on cost and scale (~8 engineers,
   wall-clock flat while cost is linear); corroborating our "derive from state,
   not from text" merge. STORM ties these to one memory store; the relay keeps
   them harness-agnostic.

### Why it's the missing, more exteriorized part (honest gap vs. prior art)
- Claude Code Agent Teams / Codex `spawn_agent` / opencode / Google Antigravity
  / RAH / LocalHarness all deploy subagents **inside one harness/context**. The
  *deployment* step exists and is solved. What none publish is: the worker as an
  **independent harness+model chosen at runtime**, the **deterministic when-to-
  split** (delegation remains an LLM decision everywhere; AdaptOrch [S39] automates
  *topology* selection but by LLM task analysis, not by boundary), and the
  **state-device reconciliation back into the parent** without re-reading the
  worker's raw context.
- So the parts that are ours-and-empty: (1) split-by-boundary determinism,
  (2) arbitrary-body deployment per worker, (3) merge is a *diff of canonical
  state*, not context. Each is `separable`/novel; the combination is the line's
  most original contribution; and unmeasured.

### Risks (name them before building)
- **Cost ×N.** Each worker starts cache-cold [S30][S31]; parallel prefill ×N can
  erase the parallelism win. The parameter that decides is the smallness of the
  relayed state; the same Fase 0 conclusion applies with N bodies.
- **Free-riding / lost semantics.** Merging deltas at the file level (like STORM
  [S37] or git worktree merges) catches textual conflicts but not semantic ones
  (compiles alone, breaks merged) [S37] [S38]. The relay's advantage is that
  state-device deltas carry *intent*, not just text; but that is currently an
  assumption, not a guarantee. Semantic-conflict coverage is the hard open point.
- **The split-decision is the new "prompt-sensitivity"**, replacing/moving the
  vulnerable LLM decision from "topology" to "partition quality". Co-Coder's
  [S36] result (coherence-aware partition > naive) says it's tractable, but only
  on repo-level tasks; no guarantee for open-ended tasks.

### Experiments that would validate it
- **Fase 3A; partition benchmark:** on a multi-module repo (e.g. Lhassa's own
  `app/lhassa/`), compare (deterministic boundary split vs. parent-LLM delegation
  vs. no-split single agent) on tokens-to-completion, wall-time, conflict rate,
  correctness. Co-Coder [S36] becomes the external baseline/positioning.
- **Fase 3B; multi-body deployment:** run the SAME task-state (Fase 2's winning
  arm) dispatched to 2-4 harnesses/models in parallel, measure: speedup vs. #
  workers, per-worker cold-start cost share, and reproducibility of merge.
- **Fase 3C; merge evaluation:** count semantic-merge breakage (compile/test
  after merge of independently-passing workers) under 3 merge policies:
  monolithic file-merge, git-worktree merge, and state-device canonical replay
  [S37][S38]. The metric is the *silent* conflict the textual merge misses.

### Candidates / guidance for the term; project to Lhassa
An adaptive topology selector is convergent with the market [S39]; topology
orchestration now dominates model choice, which re-arms the harness thesis; the
relay (multi-body, deterministic) becomes the durable expression of "rent the
body", not one harness. Two early artifacts worth building: (a) a "split by
boundary" module that reads a dependency graph (reusing Fase 0.2's parser), and
(b) a per-worker cache-ledger so the ×N cold-start cost shows in the bill
([S31] ρ discipline per worker). Keeps "consejo, no control"; the owner
reviews the merge, not the wire.

### Fase 3B; measured: cross-harness swarm, 36/36 pass, 12/12 merges clean (2026-08-05)

Built `app/research/fase3_swarm.py` (degrades, never raises; per-row abort-safe
persistence to `app/research/results/fase3_20260805.jsonl`). The SAME recorder
destilado (Fase 2's winning arm) is dispatched to `claude` / `codex` /
`opencode` in parallel, each in its own isolated git worktree; per-worker
accounting uses each harness's own usage event (claude stream-json, codex
`turn.completed`, opencode `step_finish`); cache_creation counts as INPUT
(cold prefill), not cache hit, for cross-harness honesty. 6 in-range tasks × 3
harnesses × 2 seeds = 36 workers.

| harness  | pass | mean cold | mean lat | rho   |
|----------|-----:|----------:|---------:|------:|
| claude   | 12/12 |  154161 | 41.1 s | ~90% |
| codex    | 12/12 |   76438 | 20.6 s | ~46% |
| opencode | 12/12 |   40191 | 14.0 s | ~97% |

- **36/36 workers pass the SAME task-state on harnesses with different
  accounting, models and body implementations**; the harness-agnostic claim
  holds end-to-end. The destilado is not opencode-shaped; it carries the same
  task to claude and codex unmodified and they complete it.
- **Merge probe (3C-lite): 12/12 merges clean.** Independently-passing workers
  reconciled at the state level (git, not LLM) re-verify OK; no silent
  semantic conflict in this corpus. On these tasks the deterministic state
  merge has zero breakage; the 3C worry ([S37][S38] semantic conflicts) needs
  tasks with genuinely overlapping file frontiers to bite.
- **Swarm economics (the ×N cold-start risk, now measured):** parallel prefill
  is NOT free; claude spends ~154k cold tokens per worker (~3.8x opencode),
  codex ~76k (~1.9x). The relay's per-worker cache-ledger ([S31] ρ) shows
  claude/codex burn a big cold bill that warm-cache accounting would hide
  (their rho hides it: 46% codex would look cheap). The parallelism win only
  survives if the task-state stays small relative to each body's fixed cold
  cost; same Fase 0 conclusion, now with N bodies.
- **Wall-clock speedup is real:** opencode 14 s vs claude 41 s mean latency;
  gathered (parallel) finishes in the slowest worker's time. Speedup is bounded
  by the slowest body, not the mean; the split must be load-aware, not naive.

## Open questions

- Is the relay artifact prose (Family-1 format, harness-legible by construction)
  or structured (flow checkpoints + RunEvent-derived JSON with a markdown
  projection for the next harness)? The plan's "cadena dentro del mismo
  episodio" and contradiction-detection requirements push toward structured
  canonical state with a prose projection; worth deciding in Fase 0.3 with
  real events.
- **PARTLY ANSWERED (Fase 8):** a LIVE RunEvent recorder (structured events:
  decision/write/bash from the stream, not raw prose) composes a destilado
  that completes 3/3 on a multi-module task. Structured event capture works;
  the prose projection is a presentation layer on top, not the mechanism.
- Who maintains the state when the harness dies mid-turn? The recorder is
  passive (sees what `_stream` sees); an abrupt death may strand the last
  partial turn. Does the relay need to also read the harness's own session file
  (Claude Code JSONL is readable) to recover the tail, or is "state up to last
  completed event + git diff" sufficient?
- **OPEN, now decision-relevant (Fase 6c/8B):** the crash claim is the one
  place the relay still beats `opencode export/import` (export requires a live
  db; a crash mid-write truncates it; the recorder's RunEvent log is
  append-only). Needs the recorder made crash-append-only + a kill-mid-turn
  test before this is more than a hypothesis (V1/V2/V9 are external evidence).
- Budget: is ~500 tokens (the convention) or ~2k tokens (the plan's number)
  the right destilado size, and does it depend on task type? The bench should
  report the Pareto curve, not one number.
- **ANSWERED (Fase 5-Pareto):** destilado completion is FLAT across 200→2000
  tokens (200 chars = full on the decision task); the budget ceiling is not the
  binding constraint; whether the decision text is CARRIED is.
- When is native intra-harness switch (omp `handoff`/`/resume`, Claude Code
  resume) actually cheaper than the relay? Plan 2 assumes the relay wins on
  cold start; the bench arms (b) vs (c) is the honest test, and the "evitar el
  handoff > hacerlo bien" rule (predictive limit ledger) may make the relay the
  rare path.
- **ANSWERED (Fase 7/8B):** native resume wins whenever the session SURVIVES,
  cheapest (Fase 7 b: 33.9k) and portability is real via export/import
  (Fase 8B, 2/2, faster than destilado). The relay is NOT the cold-start win;
  it is the win when the session is gone (cliff overflow, crash) or must not
  be duplicated (secrets).
- Is the contradiction-detection rule (revert + owner approval) novel enough to
  matter, or is it rediscovered as a workflow convention somewhere; and does
  that matter for whether it ships?

## Research sweep (2026-08-05, subagent); new sources V1-V20 + prioritized lines

Sweep of arXiv 2024-2026 (20 sources verified via API, abstract-level) to find
lines the note did not cover. Two immediate instrument findings from the sweep:
the saturation probe had an `input`+`stdin=PIPE` bug (fixed), and the
portability probe's n=1 shows native resume surviving a move (see Fase 4
re-measured above). New sources that move the relay's argument:

- **V1 ACRFence** (2603.20625): checkpoint-restore is NOT idempotent; restored
  agents re-synthesize tool-calls → duplicate payments / credential reuse.
  Argues for replay-or-fork semantics; backs the deterministic-no-reexecute
  property of the relay's state merge.
- **V2 Compaction as Epistemic Failure** (2607.13071): Claude Code compaction
  inherits partial stdout of timed-out commands as "confirmed result" and
  propagates false positives across sessions/models. Direct argument for a
  deterministic destilado (events+git+verify) that cannot inherit fabricated
  state.
- **V3 Talaria** (2607.17181): session continuity as placement decision; cost
  of rebuilding/migrating long KV in serverless. Backs the CI-ephemeral case.
- **V4 PRIMA** (2605.24775): published operational pattern; detect rate-limit,
  persist a "typed pause record" to disk, resume without re-executing
  converged work. The relay-as-restart pattern, published.
- **V5 Do AI Agents Know When a Task Is Simple?** (2607.13034): over-reading is
  the real cost; E3 cuts cost 85%, tokens 91% at equal success. Corroborates
  Fase 5 (cheap model re-derives); gives minimum-sufficient-execution metric.
- **V7 HiveMind** (2604.17111): 3/11 parallel agents dead from 502 under shared
  rate limits; 72-100% failure under contention. Connects to the Fase 3B
  per-worker cache-ledger.
- **V9 SlotGuard** (2607.17147): typed secret slots, synthetic format-preserving
  values, restore only in trusted runtime; 0.0% credential leakage in 852
  planted + 20,814 sensitive chars. THE prior art for the relay's secret-angle.
- **V10 CacheProbe** (2605.30613) + **V11 Auditing Prompt Caching** (2502.07776,
  ICML 2025): prompt caches leak cross-tenant (side-channel timing, gateway
  isolation breakage). The warm cache that "masks everything" is also a leakage
  vector; re-sending secrets in a cached prefix exposes them to a channel the
  user doesn't control.
- **V12 AuthMem-Bench** (2608.01679): authority collapse; consolidation keeps
  the fact, drops the source's authority → 50.3% unauthorized actions.
- **V13 MutMem** (2608.02843): cryptographically signed memory transitions;
  **V15 MemTxn** (2607.27834): durable snapshot journal + full-state recovery
  after failure; **V16 MemSecBench** (2607.27080): Write-Execute-Forget protocol
  with deterministic adjudication. Together: the primitives for an auditable,
  crash-recoverable, poison-resistant external state.
- **V17 MemoryCPT** (2608.04843): compute-once cacheable-prefix distillation;
  **V19 CacheWeaver** (2606.19667): prefix-tree evidence ordering, 20-33% less
  TTFT. The cache-optimal destilado shape (Section 3.1 below).
- **V20 A-MEM** (2502.12110, NeurIPS 2025): Zettelkasten agentic memory; the
  canonical durable-external-memory reference the note lacked.
- (V6 Zero-Mem, V8 Filesystem-Memory, V14 Trust-but-Verify, V18 ScrubJay-MEM:
  corroborating/cautionary; see the full sweep in the task report.)

### Prioritized lines from the sweep (what to do next)

- **HIGH; throttle/429 resume:** destilado written BEFORE a rate limit survives
  post-cooldown with far fewer cold tokens than re-sending the saved transcript.
  Evidence: V4 PRIMA, V7 HiveMind. Experiment: artificial 429 proxy + resume at
  (a) re-send vs (c) destilado, n>=3.
- **HIGH; crash/restore determinism:** can a RunEvent+git+verify destilado
  avoid the fabricated state of V2 compaction and the duplicated effects of V1
  ACRFence? Metric: inherited-false-state rate + duplicated-effect rate. A
  safety property, valuable even at zero token savings.
- **HIGH; cache TTL as strategy selector (Δt_cross):** measure at what idle
  time Δt the relay beats re-send once the provider cache expires; turns the
  warm-cache masking from "relay is pointless" into a "use relay if Δt > τ"
  selector. Requires adding the last-use timestamp to bench rows.
- **HIGH; secret surface (SlotGuard metric):** count sensitive chars that reach
  the prompt in re-send vs destilado (destilado points to disk, doesn't
  duplicate secrets). The one angle where the relay competes on security, not
  price; also flips the warm-cache leakage finding into an argument.
- **MEDIUM; portability replication** (the n=1 contradicts Fase 2's arm-b):
  re-test as clean-HOME migration where no native state can travel, before
  trusting the migration case.
- **MEDIUM; context cliff plot:** saturation at 11k→50k→110k lines × a/b/c to
  find where re-send stops completing (curve, not mean). Fase 4's ~130k point
  already shows the destilado 6x cheaper at equal completion.
- **LOW; cache-shape destilado (3.1):** only if the destilado clears the
  ~3,500-token warm-cache threshold of [S31]; measure the threshold first.
- **Do NOT pursue:** enriched destilado (Fase 5 negative), smaller destilado
  (flat Pareto), more re-send-vs-destilado A/B on trivial tasks (saturated).

### Synthesis after Fase 4
The relay's value now has a measured magnitude: at post-limit context the
destilado is ~4-6x cheaper than re-send at equal completion. Combined with the
swarm (Fase 3B) and the secret/crash angles (V1/V2/V9/V12), the relay is best
positioned not as a cost-saving mechanism but as: (1) the post-limit / crash /
throttle path, (2) the harness-agnostic coordination contract, and (3) the
secret-safe handoff. The token-savings framing (1.35x on small tasks) is the
weakest of the three and should not anchor any future pitch.

### Fase 8B; portability, the REAL migration test (export/import, 2026-08-06)

Fase 4's portability probe (native resume dies when the cwd moves) kept the
SAME HOME; so the resume could still read its session from opencode.db. The
honest migration test (`fase8b_portability.py`): move the worktree AND run in a
fresh HOME B (XDG_DATA_HOME pointed at an empty dir; only auth.json copied,
a credential, not session state). Two discoveries, both measured:

1. **`opencode export` + `import` restores a session in a fresh HOME**; the
   serialized session (~7KB for a small task) travels, and `--continue` then
   writes files exactly as if nothing moved (verified: s.txt/t.txt/u.txt
   across three separate fresh HOMEs).
2. **So the real migration test is b-vs-c with state serialized, n=2:**
```
arm   pass   lat_s   detail
b     2/2    14.2    export+import+continue (native session restored in HOME B)
c     2/2    43.6    destilado cold-start in HOME B
```

**The relay's "portability is the only path" claim is REFUTED in its strong
form**: native session state CAN travel via export/import. Migration is not a
relay-exclusive capability. What actually differentiates the relay now:
  - **artefact size**: opencode's export serializes the full session (grows
    linearly with context → at cliff scale that's the same 1.5M-token problem);
    the destilado is compact (flat/sub-linear). The size advantage is real but
    only bites at scale, where the cliff already dominates.
  - **harness-agnosticism**: `opencode export/import` is opencode-specific;
    the destilado travels across claude/codex/opencode alike.
  - **latency is REVERSED**: restored-session resume was FASTER (~14s vs ~44s)
    because the context is pre-loaded; the destilado re-derives cold. So the
    relay loses the latency frame too.
- Instrumentation note: opencode in a fresh HOME needs auth.json copied (else
  it drops to interactive); `opencode import` requires a file path, stdin
  (`import -`) fails. cold-start in a fresh HOME works once auth is present.
- Net: portability drops out of the relay's value stack. Remaining value =
  cliff crossing (only path at overflow) + harness-agnostic coordination +
  secret surface (destilado doesn't duplicate secrets into the prompt) +
  crash determinism (export is fine when the machine is alive; a crash kills
  opencode.db mid-write, the relay's RunEvent log is append-only and survives).

### Fase 8; a REAL event recorder (C9) on a MULTI-MODULE task (2026-08-06)

C9 was the last big validity gap: no instrument ever ran a real events+git
recorder; the destilado was a pointer or a post-hoc transcript parse. Built
`fase8_recorder.py` with a live stream consumer that turns opencode's JSON
event stream (tool_use write/edit/bash, text/reasoning) into a deterministic
RunEvent log WHILE phase-1 is running, and composes the destilado from THAT.
Task: a two-module service (config.py Entry dataclass + engine.py) with a
hidden cross-module product decision (variadic last_favorite, newest tag, tie
by string name) delivered ONLY to phase-1's setup prompt; grader hidden.

Result, n=3, deepseek free model (arms on the SAME task):
```
arm   pass   cold     what it carried
a     3/3    62559    full phase-1 transcript re-sent
c     1/3    111894   pointer-only recorder destilado (NO decision)  [see note]
d     3/3    47388    enriched destilado (transcript parsed POST-HOC)
e     3/3    64158    destilado from the LIVE event recorder (re-measured 0/3→3/3)
```
(second e-vs-c run, isolated: c 0/3 @ 41k mean, e 3/3 @ 89k mean)

**The C9 instrument works.** The live recorder captures the phase-1 decision
text (a single `text` event carrying the full contract) and its on-disk state,
composes a destilado, and a fresh agent completes 3/3 from it; the relay's
core mechanism (capture events → carry non-rederivable → continue from state)
now has a measured, working implementation, not an approximation.

**Two bugs found and fixed during the run (both instrument bugs, not relay
defects):**
- arm (e) originally started from a FRESH factory worktree (stub) while the
  destilado pointed at the phase-1 partial state → model saw a contradiction,
  0/3. Fixed: arm (e) clones the phase-1 worktree (the relay delivers the
  destilado TOGETHER with the state it describes). 0/3 → 3/3.
- `git diff --stat` was "(empty diff)" because phase-1 commits before the arm
  builds the destilado → cosmetic, state travels via the clone anyway.
- The one (c) "pass" in the mixed run was a real artifact: the model wrote a
  LIST-taking signature that the variadic grader rejected with the exact
  traceback in the row detail; NOT a relay completion.

**What Fase 8 changes:** the decision gate is now fully closed on mechanism:
(1) pointer-only fails on non-re-derivable tasks (Fase 7 + Fase 8), (2)
carrying the decision completes (Fase 7 d 3/3, Fase 8 d/e 3/3), (3) the live
recorder (the product's actual component) does the carrying at ~64k cold on a
two-module task, on par with re-send at SMALL scale (re-send wins small/cheap;
relay wins at cliff scale where re-send overflows). The build decision rests
now on engineering the recorder into Lhassa's flow, not on open measurements.

### Fase 3B corrected; codex write-capable (2026-08-06)

The audit C5: codex ran `--sandbox read-only`, could not write, its 12/12 was
vacuous. Re-ran with `--sandbox workspace-write` (verified codex CAN write):
```
harness   tasks  pass   mean cold
codex     4      4/4    102465
opencode  4      4/4    43858
```
Merge probes: 2 real conflicts (hello.py, test_hello.py) + 2 clean merges,
same as before, so the codex vacuity did NOT change the conflict picture. codex
runs ~2.3x the cold tokens of opencode on the same seeded task (its accounting
also counts cache differently). The "harness-agnostic 36/36" headline is now
backed by a write-capable codex arm.

### Fase 6 corrected; arm (b) actually resumes now (2026-08-06)

The audit (C4) found Fase 6's arm (b) captured the phase-1 session then
DISCARDED it; "native resume is linear" was built on an arm that never
resumed. Fixed (`--continue <sess>` genuinely passed) and re-measured under
the free default model (the `opencode/deepseek-v4-flash` alias is a billing-
wall CreditsError on this box; `-free` works):

```
scale  arm  pass   in      cache    cold      note
36k    a    True   58853   122112   180965    re-send full prior
36k    b    True   69437   143616   213053    native --continue (saturated)
36k    c    True    9133    44544    53677    destilado
110k   a    False  162137  165632   327769    OVERFLOW (does not complete)
110k   b    True    8406    15616    24022    native --continue
110k   c    True   21918    57599    79517    destilado
230k   b    True    337     34816    35153    native --continue
230k   c    True   23174    33024    56198    destilado
```

Reading (three honest claims + one caveat):
- At 36k, native resume is NOT cheap; continuing a saturated session re-ingests
  the saturated context (213k cold, the most expensive arm). The old "resume is
  linear/cheap" headline is dead; the corrected arm shows resume is expensive
  whenever the session is genuinely saturated, on a par with re-send.
- At 110k, re-send OVERFLOWS (fails) while native resume AND destilado both
  complete; the relay's "only path" claim holds for both, not just the
  destilado. (Matched the earlier n=1 "~550k claude / ~250k anthropic / ~1M
  codex" cliffs; deepseek re-send overflow sits between 36k and 110k for this
  (a) construction.)
- CAVEAT still unresolved: the 110k/230k resume costs (24k/35k cold) are
  suspiciously LOW for a session that supposedly holds the whole file in
  context; opencode `--continue` evidently does NOT reload the full saturated
  context (or the saturation read never landed it in context), so those two
  numbers measure a *partial* resume, not the cost of resuming everything.
  Verdict: native-resume cost at scale is NOT RELIABLY MEASURABLE through
  opencode `--continue`; treat the b-column as "completes" only, not "costs X".
  This is itself a relay argument: the harness cannot tell you what it will
  re-input; the relay's destilado makes the input-boundary explicit.

### Fase 7; create-from-scratch, HIDDEN spec (2026-08-06, the decision-changer)

The audit said the ONLY gate that matters is: deliverable NOT on disk, verify
can't pass doing nothing, spec NOT inferable from the prompt. Built exactly
that (`fase7_hidden.py`):

- **Create-from-scratch:** `merge_sorted.py` is a placeholder include only; a
  run that writes nothing fails the verify (ImportError).
- **Hidden spec:** the ordering contract (merge DESCENDING, largest-first) is
  delivered ONLY to the phase-1 agent in its setup prompt, never written to
  disk, never repeated in `task.prompt`. DeepSeek's default for "merge_sorted"
  is ascending; the verify targets descending, so a relay that does NOT carry
  the decision must fail.
- **Hidden verify:** the grader is a python snippet the arms never see.
- **Anti-leak proof (measured):** `recorder_destilado` (the poor arm's payload)
  contains no "descending"/"largest first" anywhere; the pointer-only
  destilado genuinely carries zero spec.
- **Contamination guard:** phase-1 commits the placeholder; the enriched-arm
  decision is only what phase-1 actually STATED in its reply (extraction);
  the rich arm NEVER receives the spec verbatim (that would be a leak, C8).
  All three phase-1 runs were clean (committed placeholder; arm-b wrote its own
  solution after).

**Result, n=3, opencode/deepseek-v4-flash, mean cold tokens:**
```
arm   pass   cold    what it carried
a     3/3    56626   full phase-1 transcript re-sent (spec present)
b     3/3    33918   native --continue of the phase-1 session (spec in session)
c     1/3    85775   POINTER-ONLY destilado (no spec → cannot know)
d     3/3    77832   decisions extracted from phase-1 reply (spec carried)
```

Reading: (c) fails **2/3**; a create-from-scratch task whose spec is not
re-derivable genuinely cannot be completed from a pointer-only relay. The one
(c) pass was a lucky hallucination (it wrote a confident descending docstring
with no evidence). (d) passes **3/3 at essentially identical cost** (~78k vs
~86k cold); carrying the extracted decision is FREE and turns 1/3 into 3/3.
(a)/(b) pass because the transcript/session carries the spec.

**What this changes (the audit's decision question answered):** the relay DOES
carry the non-rederivable 20-40% when the destilado includes the phase-1
decision text. The "relay only works where the state IS the file" suspicion is
REFUTED; on a state-is-NOT-the-file task, pointer-only fails and
decision-carrying succeeds. The defect is the CURRENT destilado shape
(pointer-only), not the relay concept. A relay that captures decisions (the
C9 "recorder never ran" gap) has a validated mechanism.

**Honest caveats:**
- Enriched destilado ≈ poor destilado cost here (~78k vs ~86k): the 
  construction cost of carrying real decisions did NOT show up at this scale
  (they were small transcripts). At cliff scale (Fase 6, 880k) it still won
  (34.7k vs 60.2k); no contradiction, just scale dependence.
- arm (b) beaten on cost (33,918) when the session SURVIVES; the relay's
  remaining economic case is precisely the cases where the session does NOT
  (move/portability, throttle, crash, provider cliff). Not invalidated, but
  the frame stays "only path", not "cheaper".
- n=3, one cheap model, one task family (single-module merge). Still no real
  multi-module repo. The claim is: the C1/C2/C8-clean completion number now
  EXISTS and it is 3/3 for the carrying relay.

**Instrument fixes shipped in the same pass** (audit items C4/C5):
- `fase6_cliff.py` arm (b): now genuinely `--continue <sess>` (was captured-
  and-discarded → "resume linear" was built on a never-resuming arm). Re-run
  needed to replace the invalidated b-column.
- `fase3_swarm.py` codex: `--sandbox read-only` → `workspace-write` (was
  write-incapable → vacuous 12/12 "pass"). Re-run needed for 3B validity.

### Fase 9; the `workspaces`/`domain` bucket, Lhassa's real case (2026-08-06)

Fase 0.2 measured `workspaces`/`domain/venta` at ~47% re-derivable (worst
case: ~53% of the transcript is NOT on disk). All prior phases benched
synthetic tasks with a single hidden line. Fase 9 (`fase9_workspaces.py`)
makes the non-derivable content RICH and domain-like: a 15-rule business
ruleset (~R1-R15: ship days, gold-tiering, discounts, shipping, points) that
phase-1 must absorb and partially apply; a 5-behavior hidden grader.

Result, n=3, deepseek free model:
```
arm   pass   mean cold
a     0/3    146662   full phase-1 transcript re-sent (ruleset IS in it)
c     1/3    329886   pointer-only destilado (rules not carried)
e     3/3    590429   LIVE event recorder destilado (rules carried + state)
```

Readings:
- **(e) 3/3, (c) 1/3, (a) 0/3; on Lhassa's real bucket the recorder is the
  only CONSISTENT completer.** The carrying relay completes every run; the
  pointer-only relay fails 2/3; and the re-send fails 3/3; the full
  transcript re-sent DEGRADES the model (the 15-rule context in raw transcript
  form confuses it / it drifts off the grader's exact behaviors). This
  extends Fase 7/8 to the realistic workload: rich non-derivable content is
  exactly where the relay earns its keep.
- **Honest grader caveat (why c passed once):** the seed-0 (c) "pass" wrote a
  fully correct engine WITHOUT the rules; because several grader thresholds
  coincide with common business heuristics (>=5 orders or $10k spend = gold;
  free shipping >$75; urgent+gold 15% then 10% stacking; int() points). A
  heuristic-inferable grader understates pointer-only failure. The clean
  discriminator needs NON-heuristic rules (e.g. exact Friday-urgent→Monday
  pairing, "economy never to PO boxes", backorder full-only) as the FAIL
  signal; for the next round, not a blocker on this result (e still 3/3).
- **Re-send failing 3/3 is itself a relay argument**: pasting the prior
  context as raw transcript is not just costly (146k→202k, growing), it is
  WORSE at completion. The destilado's compression is a quality feature, not
  only a cost one.
- Cost note: e's mean cold (590k) looks high but includes cache-read of the
  full phase-1 ruleset discussion; its in-tokens are 22-41k (the fresh work),
  comparable to a/c. The relay's cost case remains cliff-scale, its completion
  case is now shown on the realistic bucket too.

**What Fase 9 closes:** the last audit gap (real multi-module/domain task) is
measured, and on the realistic workload the conclusion is unchanged and
stronger: the carrying recorder completes, pointer-only and re-send do not
reliably. The build decision's measurement side is now complete.

### SYNTHESIS; the audit closed, decision ready (2026-08-06)

Every gate the audit left open has now been measured. Bottom line:

**The relay's mechanism is real.** On the audit's own discriminator (create-
from-scratch, hidden spec, hidden verify; Fase 7) a destilado that carries
the phase-1 decision completes 3/3 where pointer-only fails 1/3 (2/3 fail);
on the multi-module task (Fase 8) the LIVE event recorder; the product's
actual component, not an approximation; carries decisions+state and completes
3/3 (pointer-only 0/3). C1/C2/C8 are closed: these numbers cannot pass by
doing nothing, and the spec is not inferable from the prompt.

**But the value stack has narrowed to exactly three measured uses:**
1. **Cliff crossing (only path at overflow).** Re-send grows linearly to the
   provider window (~36k→110k overflow for deepseek here; ~550-650k earlier
   n=1; claude 200k; codex 1,048,576 chars) while the destilado is flat
   (27-97k across 36k→880k) and completes where re-send is impossible
   (Fase 6/6c). This survives the audit.
2. **Harness-agnostic coordination.** Write-capable codex now backs 4/4
   (Fase 3B corrected); the destilado crosses claude/codex/opencode, which
   harness-native export (below) does not.
3. **Secret surface.** The destilado points at disk instead of duplicating
   secrets into the prompt; the one frame where the relay competes on
   security, not price (unmeasured, from [S12]/[S32]).

**Two value claims are DEAD, measured:**
- **"Cheaper at small scale"**; dead in Fase 2 (warm cache), and Fase 7 shows
  the carrying destilado costs the same as re-send (~78k vs ~57k) on small
  transcripts. The construction cost of carrying real decisions is real.
- **"Portability: the relay is the only path"**; dead in Fase 8B. Native
  session state travels via `opencode export`+`import` (2/2 restored in a
  fresh HOME, ~14s, FASTER than destilado cold-start ~44s). The relay's
  remaining portability edge is artefact size at scale + crash safety
  (append-only RunEvent log vs a db that a crash can truncate mid-write).

**The build-vs-paper verdict:** the evidence now supports BUILDING the recorder
into Lhassa's flow; but ONLY for use case 1 (cross the context cliff) and
use case 3 (secret surface), with the recorder from Fase 8 (live event
capture → compact destilado → continue from cloned state). It does NOT support
the "relay replaces resume / relay for portability" pitch. The honest product
framing: a `destilado` is the crash/cliff/resumption artifact of a run, not a
general transport. Paper: publishable now with the caveats already inline (n=1-3,
one cheap model, synthetic tasks, no Lhassa flow integration).

**Still open (decision-relevant only if the build targets use case 1):**
- Lhassa's real `workspaces`/`domain` buckets (~47% re-derivable) HAVE now been
  benched (Fase 9): recorder 3/3, pointer-only 1/3, re-send 0/3 on a 15-rule
  domain task. The measurement side is complete; the remaining work is
  engineering, not evidence.
- The recorder's RunEvent log must be crash-append-only before the crash-claim
  is credible (V1/V2/V9 evidence is external, not ours).
- Real Lhassa flow integration (runner._stream) is the only remaining
  unmeasured surface; the instruments proxy it with opencode's stream.

**Decision: proceed to build the recorder as a research artifact first** (the
Fase 8 instrument, made crash-append-only), THEN decide integration into
Lhassa's paying loop. The remaining measurements (Fase 9 workspaces bucket) do
not block the build decision; they refine the target tasks.
