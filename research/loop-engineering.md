# Loop engineering

Research note, Lhassa's research line (`doc/research.md`). Field: verified loop
engineering ("bounded, verifier-terminated iteration where the doer never
judges; caps before work; feedback as the thread").

## The idea in one sentence

A prompt gives an agent instructions for one turn; a loop gives it a job —
DISCOVER → PLAN → EXECUTE → VERIFY → ITERATE repeated against a *verifiable*
goal, with an independent verifier deciding pass/fail (never the model that
did the work) and a turn/time cap enforced before any work starts.

## Mechanics — down to the base

**The five stages.** DISCOVER (understand the current state) → PLAN (decide
the next concrete step) → EXECUTE (do it) → VERIFY (an independent check, not
a self-report) → ITERATE (feed the verifier's detail into the next DISCOVER,
or stop). "Verifiable" is load-bearing: a goal has to reduce to something a
command or a rubric can check ("all tests in /auth pass and lint is clean"),
not a taste judgment ("make it pretty"). If you can't check it with a command,
it's a wish, not a goal. [S2]

**The golden rule: the doer never judges.** Models are unreliable critics of
their own output — self-assessment loops drift toward false confidence. The
fix is structural: an independent verifier, with its own context, decides
pass/fail, and only its verdict (never the doer's opinion of itself) can close
the loop. Anthropic ships two concrete forms of this: `/goal` in Claude Code
(a measurable condition; an independent model judges; on fail the next turn
auto-starts) and Outcomes rubrics in Claude Managed Agents (a list of criteria
a corrector subagent evaluates every iteration; the agent cannot stop until
all of them pass). [S2]

**Open vs. closed loops.** Open = a broad goal plus free exploration — powerful,
token-hungry, and without hard quality gates it degrades into a slop machine.
Closed = an owner-designed path with defined steps, per-step evaluation, and a
known stop point — reliable, and it fits a budget. The stated practice: start
closed, only open the exploration space once the quality gates are proven to
hold. [S2]

**The six blocks every good loop needs**, per the source's inventory:

1. **Automations** — prompt + cadence + goal; the heartbeat that fires the loop
   without a human re-typing it each time.
2. **Worktrees** — parallel agents in isolated directories/branches so
   concurrent loops don't collide.
3. **Skills** — project knowledge written once, read every cycle (VISION.md,
   ARCHITECTURE.md, RULES.md); without them the loop re-derives the project
   from scratch on every turn.
4. **Connectors/MCP** — a loop that only sees the filesystem is a small loop;
   real tool access is what makes the loop worth running unattended.
5. **Subagents** — the verifier is never the doer; this is the golden rule
   turned into an architectural block.
6. **Memory** — a simple markdown file *outside* the conversation (TRIED /
   VERIFIED / OPEN) so a loop running on day 47 knows what days 1–46 already
   tried, instead of re-discovering it. [S2]

**The memory ladder (five levels of using a failure).** The source frames
memory not as a store but as a *use discipline*, observed on the Continual
Learning Bench (see Evidence): (1) fail and document it; (2) investigate why;
(3) VERIFY the cause into a fact (not just a note — a checked claim); (4)
distill the verified fact into a general RULE; (5) consult the rule instead of
re-deriving the answer from scratch. Models differ in how far up the ladder
they climb, not in whether they take notes — the gap is *between noting a fact
and later trusting it enough to skip re-derivation.* "The model forgets between
sessions. The memory file does not." [S2]

**Practical discipline**, as the source states it: define "done" as a
verifiable condition before starting; hand the loop its context files before
launch, not mid-run; always set a turn/time cap ("your emergency brake"); and
review the *final* result, not each intermediate step — reviewing every step
by hand is just being the loop yourself with extra ceremony. [S2]

**Prompt engineer vs. loop engineer.** A prompt engineer writes better
instructions and reviews output by hand — they *are* the feedback loop. A loop
engineer designs feedback cycles where tests review automatically — the
*system* is the loop. The source's closing nuance: two people can run the same
loop for different reasons — one to go faster at work they already understand
deeply, the other to avoid understanding it at all — and its advice is to
"design loops as someone who intends to remain the engineer," i.e. build the
verification tight enough that speed doesn't cost you comprehension. [S2]

**Related framings from the broader curriculum** (not the same source, same
territory): Reflexion-style loops keep a *verbal* self-critique memory across
attempts rather than only a pass/fail signal; long-horizon autonomy work adds
cost governors, kill switches, circuit breakers, and HITL "propose-then-commit"
gates as the operational scaffolding around any unattended loop — a loop
without a budget or an owner checkpoint is a liability regardless of how good
its verifier is. [S11]

**`/goal` as a concrete instance of the whole pattern.** Hermes' `/moa` thread
notes in passing that its panel consultation "works inside the normal agent
loop (tools, sessions, follow-ups, `/goal`)" — i.e. `/goal` is treated as the
loop primitive other capabilities compose with, not a separate feature. [S9]

## Evidence the authors present

All numbers below are **the source's own benchmarks**, run by them, on their
setup — not Lhassa's. They are cited here as claims with stated provenance,
not as validated facts.

- **Parameter Golf** (Anthropic experiment, cited by S2): train the best model
  fitting in 16MB in under 10 minutes on 8×H100. An agent edits the training
  code, launches runs, reads logs, and decides the next experiment, alone for
  8 hours. Result: the agent (running as "Fable 5" in the source's telling)
  improved the pipeline **~6× more** than a same-family model without the loop
  discipline ("Opus 4.7"). The stated mechanism for *why*, not just *how much*:
  the weaker run found one small win and repeated the same template ~20 times;
  the stronger run made structural bets, survived a quantization regression,
  and turned that regression into its biggest win — i.e. it took risks and
  recovered *inside* the loop rather than converging on a safe local optimum.
  [S2]
- **Continual Learning Bench** (cited by S2): sequential SQL questions, a fresh
  session each time, one shared memory file across sessions. Scores: 0.839 vs.
  0.700 vs. 0.364 for three models. The source ties the gap directly to the
  memory ladder above: the lowest scorer stays at ladder level 1 (notes
  failures but never rereads them); the middle scorer reaches level 3
  (verifies the cause of a failure ~17% of the time); the top scorer completes
  the cycle (verifies 73% of its diagnoses and converts them into reusable
  rules). The bench is presented as measuring *memory discipline*, not raw
  model capability. [S2]

No independent replication of either number exists in this pack; both are
reported by the party that built the loop being measured.

## Sources

- S2 — Loop Engineering thread (angeldot_), the primary source for this note:
  https://x.com/angeldot_/status/2065145486890164524
- S3 — "Knowledge Graph Engineering for Multi-Agentic Systems: The Anthropic
  Playbook" (12pp, independently compiled from Anthropic's public Knowledge
  Graph Cookbook + "Building Effective AI Agents" + Claude API docs; not
  Anthropic-authored or endorsed despite S1/S4's attribution) — background on
  the orchestrator-workers pattern the loop's SKILLS/MEMORY blocks feed.
  Local capture: `scratchpad/graph-engineering.pdf`.
- S4 — nicos_ai's distillation of S3: https://x.com/nicos_ai/status/2081066452694556956
- S5 — RoundtableSpace repost misattributing S3 to Karpathy — documented here
  as a provenance flag, not used for content:
  https://x.com/roundtablespace/status/2081707421756457213
- S9 — Hermes `/moa` thread, cited only for its `/goal` mention (loop
  composability): https://x.com/iamlukethedev/status/2081904729421226301
- S11 — ai-engineering-from-scratch, Phase 14 (agent loops, memory systems,
  Reflexion, long-horizon autonomy, HITL propose-then-commit):
  https://github.com/rohitg00/ai-engineering-from-scratch

## Mapping to Lhassa

Verified against the current tree (2026-07-30), not inferred from the plan.

- **The loop primitive exists as real storage.** `lhassa/core/db.py` migration
  7 (`_migration_007_loops`, line ~970) creates a `loops` table: `id`,
  `instance_id`, `goal`, `kind` (`closed`/open, default `closed` — matches the
  source's "start closed" advice), `origin`, `status` (walks
  `plan|execute|verify|iterate|done|failed|capped` — a direct rendering of the
  five stages, condensed), `turn`, `max_turns` (default 3), `deadline_at`,
  `verifier_spec`, `last_verdict`, `task_id`. `insert_loop`/`get_loop`/
  `list_loops`/`advance_loop` (same file, ~2863-2967) are the CRUD; `advance_loop`
  only *records* state — the docstring is explicit that "the CALLER enforces
  caps before working," matching the source's "caps fire before any model
  call."
- **The forge iterates on this table for real.** `lhassa/agents/author.py:
  propose_tool` (lines 264-490) opens a `loops` row per candidate
  (`origin="forge"`, `verifier_spec={"type": "gates"}`), then loops
  `author_candidate → versioning.create_candidate → gates.promote` up to
  `max_turns`. A failing gate's **detail text** — never the model's own
  self-assessment — is threaded into the next turn's prompt as `feedback`
  (`_messages()`, lines 77-103: "The previous attempt failed an objective
  check. Fix it and resubmit"). This is the golden rule ("the doer never
  judges") implemented literally: `gates.py`'s three objective gates (segura /
  correcta / mejor, `GATE_SEGURA`/`GATE_CORRECTA`/`GATE_MEJOR` in
  `lhassa/agents/gates.py`) never call an LLM (module docstring, line 22: "the
  árbitro layer is objective and NEVER calls an LLM"). One deliberate
  divergence from the source: a `mejor`-gate failure with no baseline/metric_fn
  is explicitly **non-retryable** — "no feedback can conjure a baseline" — and
  stops the loop immediately rather than burning a turn on an unfixable
  config gap (`author.py` lines ~442-449).
- **`/goal --verify --turns` is the standing-goal form of the same primitive.**
  `lhassa/notify/commands.py` (`_cmd_goal`, ~line 806) parses
  `--verify command:<cmd>|regex:<pattern>` and `--turns N` and mints a
  `kind="closed"`, `origin="telegram-goal"` loop row with `max_turns` from
  `--turns` (default `_GOAL_DEFAULT_MAX_TURNS`). `lhassa/core/step.py`
  (~lines 147-197, 333-383) is where a scheduled think checks the cap *before*
  any model call (turn ≥ max_turns → `status="capped"`, owner notified once,
  recurrence stopped), then after the think runs the loop's `verifier_spec`
  through `agents/verify.py` (deterministic, $0, never an LLM) and either marks
  the loop `"done"` or writes the verdict's detail into
  `trigger.params["_loop_feedback"]` for the next recurrence — the same
  feedback-threading discipline as the forge, riding the existing recurrence
  trigger rather than a new minting path.
- **`agents/verify.py`'s verifier zoo is the independent-verifier block.**
  `CommandVerifier`/`TestsVerifier`/`LintVerifier`/`HealthVerifier`/
  `RegexVerifier`/`MetricVerifier`/`ContactPreservedVerifier` (lines 93-313);
  `make_verifier(spec)` builds one from a `verifier_spec` dict, `verify()` is
  the single entry point both the forge and `/goal` loops call. This is
  Lhassa's answer to Anthropic's `/goal` judge and Outcomes rubric — except
  every one of Lhassa's verifiers is a deterministic check (command exit code,
  regex, metric comparison, health probe), never a second model doing the
  judging. That is a *stronger* claim than the source's own pattern (which
  still uses "an independent model" as the judge in `/goal`) and matches the
  house's $0-first, deterministic-evaluation stance (`doc/research.md`).
- **Context packs are the SKILLS block, generated instead of hand-written.**
  `lhassa/harness/context_pack.py` renders `VISION.md`/`ARCHITECTURE.md`/
  `RULES.md`/`MEMORY.md` per project (module docstring, lines 1-25) — the exact
  four names the source calls out, but built $0 and deterministically from
  `core/projects`, `memory/facts`, and `core/db` rather than authored by hand.
  `MEMORY.md` is explicitly "the loop-engineering three: TRIED / VERIFIED /
  OPEN" (per `doc/decisions.md`, 2026-07-28 tarde entry) — a three-level
  compression of the source's five-level ladder, keeping levels 3 (verify) and
  4/5 (rule, consult) folded into "VERIFIED." Freshness is a digest over
  project/fact/run timestamps (`ensure()`, same file) so regeneration is a
  no-op when nothing changed — a mechanism the source does not specify.
- **What differs, named honestly:** (1) Lhassa's verifiers are code, not a
  second model's judgment — a deliberate divergence from `/goal`'s "independent
  model judges." (2) The five DISCOVER/PLAN/EXECUTE/VERIFY/ITERATE stages
  collapse into six `loops.status` values that merge DISCOVER+PLAN into
  `"plan"` and drop a discrete DISCOVER state — the table optimizes for what a
  db row needs to track, not for narrating the stage model 1:1. (3) The memory
  ladder's five levels compress to three file sections (TRIED/VERIFIED/OPEN)
  rather than five; level 4 (distill into a rule) and 5 (consult the rule) are
  implicit in how VERIFIED entries get read back in, not separately tracked.
  (4) `kind="open"` loops are defined in the schema but no code path in this
  tree currently mints one — every loop found (`forge`, `telegram-goal`) is
  `kind="closed"`; the source's "open the space only once quality gates work"
  reads as aspirational for Lhassa today, not yet exercised.

## Not yet verified by us — and the experiment that would

- **Convergence rate / turns-to-graduate for the forge.** `propose_tool` caps
  at `max_turns=3` by default and records every turn's verdict on the `loops`
  row, but no aggregate query over `loops`/`bench_runs` currently reports how
  often a candidate graduates on turn 1 vs. 2 vs. 3, or how often it caps. The
  data model supports it (`turn`, `status`, `last_verdict` are all there); the
  query and the report are not written. The experiment: run `lhassa/bench`
  fixtures through `propose_tool` N times, tabulate turns-to-graduate and cap
  rate, and publish it as the first real (not borrowed) loop-convergence
  number for the "the note queue" item in `doc/research.md`.
- **Whether the memory ladder's levels 3-5 actually fire in `/goal --verify`
  loops in practice** (do owners' standing goals ever verify-pass on turn 2+
  because feedback closed a real gap, vs. capping without improvement?) is
  unmeasured — no loop is old enough in this tree's git history to have
  accumulated a meaningful sample, and no report reads `last_verdict` across
  turns of the same loop id to check for improvement trend.
- **The Parameter Golf and Continual Learning Bench numbers are not
  reproducible here** — they describe a different agent, a different task
  (ML training / SQL QA), and a different harness. Lhassa's equivalent claim
  can only come from `lhassa/bench` fixtures scored by Lhassa's own verifiers
  (per `doc/research.md`'s "the bench is the tribunal" rule); no such fixture
  currently exercises the forge's turn-over-turn loop specifically (the 10
  fixtures under `lhassa/bench/fixtures/` are scored per-run, not
  loop-over-turns).

## Open questions

- Should `loops.status` grow a discrete `"discover"` value, or is folding
  DISCOVER into PLAN a permanent simplification worth documenting explicitly
  rather than leaving implicit in the migration's status enum?
- Is `kind="open"` worth building a real caller for, given the source's own
  caution that open loops "become a slop machine without standards" — does
  Lhassa need a standards mechanism (a rubric analog) before it opens that
  door, or does the closed-only current state simply reflect that no use case
  has needed it yet?
- Should the forge's per-turn feedback (`gate {gate} failed: {detail}`) be
  compared against the memory ladder's level 4 (distill into a rule) — i.e.
  should a gate failure that recurs across multiple `propose_tool` calls for
  *different* tools get promoted into a standing RULES.md entry via the
  context-pack MEMORY.md mechanism, closing the loop the source describes
  between "verify the cause" and "consult the rule instead of rederiving"?
- The source's "review the final result, not intermediate steps" practice —
  does any Lhassa surface (dashboard, `/goal` reply, digest) currently
  summarize a completed loop's *final* state only, or does the owner have to
  read every turn's episode to know how it went? (`doc/decisions.md` describes
  "every turn is an Episode" but not a final-state rollup view.)
