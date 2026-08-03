# Mixture of Agents

Research note, Lhassa's research line (`doc/research.md`). Field: safe
autonomy / deterministic evaluation — panel consultation as a deliberate,
opt-in spend for decisions where being wrong is expensive.

## The idea in one sentence

Fan the same prompt out to two or more different models in parallel, let each
answer independently, then spend one more (cheap) completion having an
aggregator compare the answers and synthesize — and name real disagreement
honestly instead of papering over it — because for architecture, debugging,
research, or any high-stakes call, a panel beats a single model's luck.

## Mechanics — down to the base

**The fan-out/aggregation shape.** A user question goes to N "reference"
models simultaneously (not sequentially — the point is independent answers,
not one model correcting another in turn). Each answers with no visibility
into the others. A designated "aggregator" model then receives the original
question plus all N labeled answers and produces one final answer: where they
agree, keep it; where they genuinely disagree, say so plainly rather than
averaging it away. [S9]

**Where it lives in the agent loop.** Hermes' `/moa` is explicit that this
runs *inside* the normal agent loop — same tools, same session, same
follow-ups, composable with `/goal` — and that after the consultation the
session returns to whatever model was active before. It is not a separate
mode; it is one turn of a session that happens to cost N+1 completions instead
of 1. Configuration is explicit and owner-driven: `hermes moa configure` picks
the reference models and the aggregator up front, rather than the system
guessing a panel per-question. [S9]

**When it pays — the source's own framing.** "Yes it uses more calls and
takes longer — but for architecture, debugging, research, or any decision
where being wrong is expensive, I'd rather have a panel than model luck." The
worked example given: a TS type error where one model diagnosed a recursive
loop, another diagnosed a constraint issue, and the aggregator — seeing both —
found the actual root cause and produced three working fixes. The implicit
claim is that disagreement between panelists is itself diagnostic signal (two
wrong-in-different-ways answers can triangulate a right one), not just noise
to average out. [S9]

**The academic base.** The pattern traces to Wang et al., "Mixture-of-Agents
Enhances Large Language Model Capabilities" (arXiv:2406.04692, June 2024): a
*layered* architecture where each layer holds multiple LLM agents, and each
agent in layer *i+1* takes all of layer *i*'s outputs as auxiliary context
before producing its own. Hermes' `/moa` is a single-layer instance of this —
one round of independent proposers plus one aggregation pass — rather than the
paper's multi-layer stack. [background, arxiv.org/abs/2406.04692]

**High-stakes multi-agent debate, the wider framing.** The same pattern —
independent models producing candidate answers that get compared rather than
trusted singly — is named in the broader curriculum as "multi-agent debate for
high-stakes decisions," alongside bounded autonomy, verification gates, and
eval-driven development as production concepts for agent systems. It is
grouped with Reflexion and Tree-of-Thoughts as a family of techniques that
trade extra inference cost for a harder-to-fool answer, distinct from
single-pass prompting. [S11]

## Evidence the authors present

All numbers below are the cited party's **own benchmarks**, not Lhassa's, and
are reported here with provenance, not as validated facts.

- **HermesBench** (cited by S9, Hermes' own benchmark): MoA panel = **0.8202**,
  vs. Opus 4.8 alone = **0.7607**, vs. GPT-5.5 alone = **0.7412**. No
  methodology, sample size, or task composition for HermesBench is given in
  the source thread — this is a single self-reported comparison, not a
  described experiment.
- **AlpacaEval 2.0** (Wang et al. 2024, the original MoA paper, cited via
  arXiv abstract): a layered, open-source-only MoA configuration scores
  **65.1%**, ahead of GPT-4 Omni's **57.5%** on the same benchmark — a 7.6
  point gap. The paper also claims MoA surpasses GPT-4 Omni on MT-Bench and
  FLASK, but exact scores for those two are not stated in the abstract as
  fetched; this note does not claim them.

Neither number is independently reproduced here. HermesBench in particular has
no public methodology in the pack — treat the 0.8202/0.7607/0.7412 figures as
a marketing-adjacent single data point, not a controlled study.

## Sources

- S9 — Hermes `/moa` announcement (iamlukethedev), the primary source:
  https://x.com/iamlukethedev/status/2081904729421226301
- S11 — ai-engineering-from-scratch, Phase 14, "multi-agent debate for
  high-stakes decisions" (grouped with Reflexion, ToT, bounded autonomy):
  https://github.com/rohitg00/ai-engineering-from-scratch
- Background (fetched for the academic base, not part of the captured pack):
  Wang et al., "Mixture-of-Agents Enhances Large Language Model Capabilities,"
  arXiv:2406.04692, https://arxiv.org/abs/2406.04692

## Mapping to Lhassa

Verified against the current tree (2026-07-30).

- **`lhassa/llm/moa.py`** is a direct, working implementation of the S9
  pattern, built the same day it was researched (`doc/decisions.md`,
  2026-07-28 tarde entry: "MoA (`633b111`): `/moa` fans the same question to
  claude+codex+deepseek..."). The module docstring (lines 1-23) states the
  design explicitly: "For decisions that are expensive to get wrong, one
  model's opinion isn't enough" — the same justification S9 gives.
  `_DEFAULT_PROVIDERS = "claude,codex,default"` (line 39) is Lhassa's default
  fleet — the two subscription CLIs plus the cost-gateway seat — an
  owner-decided choice (`doc/plan.md` Wave D per the docstring), functionally
  equivalent to `hermes moa configure`'s explicit-fleet-selection pattern but
  fixed in config rather than exposed as its own subcommand.
- **The aggregation call is deliberately cheap, not smart.** `moa()`
  (lines 152-239) picks the aggregator via `_router.pick_provider(sensitive=False,
  hard=False, ...)` — the routine/cheap path, never the hard model — with the
  reasoning stated inline (lines 207-209): "this is a comparison-and-synthesis
  task, not a hard judgment call." This mirrors the router's own cheap-path
  choice elsewhere (`router.cascade`). S9 does not specify the aggregator's
  cost tier; Lhassa's choice is a documented cost-discipline addition on top
  of the source pattern, in line with the house's $0-first stance.
  `_MIN_PANEL = 2` (line 43) enforces a real floor: fewer than 2 honest
  answers skips aggregation entirely and returns the single answer — no
  synthesis spend over one opinion, and no aggregation with a "panel" of one.
- **Degradation is exhaustive and never raises.** `_call_one` (lines 96-134)
  catches a missing CLI binary (`_binary_missing`, lines 83-93 — a dead CLI
  never even gets attempted, mirroring `router.fallback_chain`'s own
  skip-if-missing discipline), an unresolvable provider, a budget-exceeded
  provider, and an empty/malformed reply, turning each into one honest panel
  entry (`{"provider": name, "ok": False, "error": ...}`) rather than sinking
  the whole panel. The aggregator itself degrades to the first successful
  panel answer on failure (lines 230-239). S9's thread does not describe
  failure handling at all; this is a Lhassa-specific hardening, consistent
  with the codebase-wide rule that a heartbeat tick must never crash on a
  provider error.
- **Two callers, both explicitly opt-in — never wired into the automatic think
  path.** (1) The `/moa` chat command (per `doc/decisions.md`). (2)
  `lhassa/agents/author.py:propose_tool`'s `moa_verdict: bool = False` kwarg
  (line 278): when a candidate tool clears the objective gates AND
  `moa_verdict=True`, one extra `moa()` consultation reviews the authored code
  and attaches the synthesized opinion to the `tool_graduation` approval's
  payload as `moa_review` (lines 440-445). The docstring is explicit that
  "the panel is advisory color for the human — it NEVER overrides the
  objective gates, which remain the hard filter" (lines 306-320) — this is the
  clearest divergence from S9's pattern: Hermes' `/moa` panel *produces* the
  working answer for a debugging session, while Lhassa's forge-integration use
  keeps the panel strictly advisory alongside an already-deterministic gate
  system (`agents/gates.py`'s segura/correcta/mejor, none of which ever call
  an LLM). MoA augments Lhassa's deterministic verification; it does not
  replace or gate it.
- **What differs, named honestly:** (1) Hermes exposes `/moa` as a session-mode
  toggle with explicit fleet configuration (`hermes moa configure`); Lhassa
  exposes it as a stateless function with a config-default fleet plus two
  fixed call sites — there is no equivalent to "configure once, use across
  many future sessions" beyond editing `moa_providers` in config. (2) Lhassa's
  aggregator is pinned to the cheap/routine path by design; S9 does not state
  a cost tier for Hermes' aggregator. (3) Lhassa's forge integration makes the
  panel strictly advisory against a pre-existing deterministic gate system —
  S9's example (the TS error) has the panel *produce* the actionable fix
  directly, with no separate objective check described. (4) No `/rollback`
  analog (S9's same-thread checkpoint-undo feature) exists in this note's
  scope — out of scope for the MoA mapping specifically, named here only so
  it isn't silently conflated with `/moa` itself.

## Not yet verified by us — and the experiment that would

- **No panel-vs-single comparison exists on Lhassa's own data.** `llm/moa.py`
  records `panel` entries and `total_cost_usd` per call but nothing currently
  persists a `moa()` call's outcome against a corresponding single-model
  baseline, or scores which one was "more correct." The HermesBench-style
  claim (panel beats single-model average) is unverified for Lhassa's own
  fleet and prompts.
- **The experiment:** `lhassa/bench` is exactly the instrument for this — its
  fixtures are already scored by objective, deterministic verifiers
  (`bench_runs` table, migration 8, per `doc/decisions.md`: "10
  self-contained fixtures scored ONLY by the existing objective verifiers").
  Running the same fixture set once per single provider and once through
  `moa()` with the default fleet, scoring both with the fixture's existing
  verifier, and comparing pass rate against `total_cost_usd` would produce
  Lhassa's own panel-vs-single number — a direct instantiation of
  `doc/research.md`'s "the bench is the tribunal" rule, and currently a named
  gap ("Router consumption of this data is a named follow-up, not silent
  scope" — `doc/decisions.md`, 2026-07-28 tarde). No such run has been
  recorded in this tree as of this note.
- **The `moa_verdict` advisory review's actual influence on human approval
  decisions** is also unmeasured: `security/approval.py` records approve/deny
  but nothing currently correlates a `tool_graduation` approval's outcome with
  whether it carried a `moa_review` payload, so it's unknown whether the
  advisory panel changes owner behavior at all versus being unread color.

## Open questions

- Is a single-layer fan-out (Lhassa's current `moa()`) leaving meaningful
  quality on the table relative to Wang et al.'s multi-layer MoA (where
  layer *i+1* agents see layer *i*'s full outputs, not just a final
  aggregation pass)? The AlpacaEval gap (65.1% vs. 57.5%) is reported for the
  layered version specifically; a single round may capture less of it.
- Should `moa_providers` become fixture-aware (e.g. a cheaper 2-model fleet
  for routine graduation reviews, a wider fleet for `/moa`-invoked
  architecture questions) rather than one static default for both call sites?
- HermesBench has no published methodology in this pack — is it worth trying
  to find or request Hermes' benchmark composition before citing 0.8202 vs.
  0.7607 in any Lhassa-authored public note, given the house's "every public
  claim cites an instrument" rule?
- Given that `moa_verdict` never overrides the objective gates, is there a
  real decision class in Lhassa where a panel's *disagreement itself* (not its
  synthesized answer) should become an actionable signal — e.g. auto-escalate
  a `tool_graduation` approval's risk framing when the panel materially
  disagreed, the way S9's TS-debugging example treats disagreement as
  diagnostic rather than just noise to synthesize away?
