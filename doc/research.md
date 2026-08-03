# Lhassa's research line

Witara (the house) declares five research fields — privacy engineering, calm
computing, systems optimization, safe autonomy, deterministic systems
(`witara/docs/soul/03-investigacion.md`). Those are the HOUSE's. Each product
carries its **own line** underneath them. This file declares Lhassa's.

> Owner TODO (other repo): mirror the "one line per product" structure into
> `witara/docs/soul/03-investigacion.md`.

## The line

Lhassa researches **AI down to the base**: no technique is adopted by hearsay
or fashion — every mechanism is taken apart, measured and verified before it
earns a place; every process is optimized as a standing program, not a
one-time pass; and the operation itself must **generate knowledge** — public
technical notes born as documented byproducts of real work (the house's
guiding principle: never a vision page).

## Themes (born from what is built, not from wish)

- **Earned autonomy** — quarantine, objective gates, the approval streak knob,
  feedback-driven trust. The house names this "el diferencial real de Lhassa".
- **$0-first routing and the post-token-bubble economy** — reflexes before
  models, subscription CLIs before metered APIs, the ledger as the running
  proof (a full week of a live brain: cents).
- **Memory architectures on humble hardware** — layered context, a light
  knowledge graph with provenance, local embeddings, answer caching; what
  actually pays on a laptop or a $5 VPS.
- **Verified loop engineering** — bounded, verifier-terminated iteration where
  the doer never judges; caps before work; feedback as the thread.
- **Deterministic evaluation** — LhassaBench's verifier-locked scoring: model
  claims measured by code, never by another model's opinion (v1).

## The method — comprobación primero

1. **The bench is the tribunal.** A technique enters only after a measured
   comparison (`lhassa bench run`) or an equivalent instrumented trial; "it
   seems better" is not a result.
2. **Every public claim cites an instrument.** Numbers come from the ledger,
   `bench_runs`, or the episode log — with provenance — or they are not made.
3. **Reproducible on humble hardware.** If it needs a big GPU or an expensive
   cloud to demonstrate, it is not yet a Lhassa result (house limit #1).
4. **Bilingual on publication** — Spanish and English (the house's access
   doctrine: technical knowledge rarely arrives in Spanish, and that is an
   access debt).

## Instruments

| Instrument | Where | The question it answers |
|---|---|---|
| LhassaBench | `bench_runs` table, `lhassa bench` | Does X actually perform, at what cost? |
| Ledger | `llm_gateway.db`, `spend_summary` | What does a live brain really spend? |
| Episode log | `episodes` (+ FTS5 recall) | What happened, when, at what cost, decided by whom? |
| Decisions log | `doc/decisions.md` | Why is it built this way? |
| Loops table | `loops` | Do bounded iterations converge, and in how many turns? |
| Source custody | `doc/notes/drafts/idea-*.evidence.json` | What bytes did we actually inspect, from which URL/redirect chain, when, and under which hash? |

`/idea <text|link>` is the intake door. It persists a signed task before
acknowledging it and captures sources before prose. Before commissioning a full
corpus, it runs a theory-first assessment over nine explicit criteria:
strategic value, architectural fit, theoretical grounding, evidence quality,
testability, novelty, reusable knowledge, feasibility and reversibility. Each
criterion carries a 0–3 score, rationale and captured source ids (or an explicit
`UNVERIFIED`). The model proposes the rubric; deterministic code recomputes the
weighted result and assigns the investment budget.

| Investment | Disposition | What happens |
|---|---|---|
| `none` | `archive` | Retain the record; spend no more. |
| `exploration` | `watch` | Keep the theory/evidence brief; wait for a stronger signal. |
| `applied` | `research` | Commission the full corpus and applied experiment design. |
| `rigorous` | `experiment` | Commission the corpus with academic-grade experimental intent. |

Only the last two continue through collector → analyst → skeptic → synthesist
and write content-addressed evidence/draft pairs. The corpus gate checks method
shape and citation discipline; it does not pretend that a citation makes an
external claim true.

Every stage is durable. `idea_assessments` is the queryable current projection;
`idea_assessment_events` is the append-only audit stream. Assessment JSON,
custody and corpus are registered artifacts. Inspect them through:

```bash
lhassa research assessments
lhassa research assessment <idea-id>
```

or `GET /api/research/assessments[/{idea-id}]`.

## The note queue

1. **Earned autonomy / quarantine** — Lhassa's public debut piece (already
   assigned in the house's soul docs): the four gates, the streak knob, the
   feedback reset, with real graduation/approval data.
2. **"A day for a cent"** — the ledger as evidence for the anti-bubble
   thesis: one instrumented day of a live personal brain, tick by tick.
3. **Adaptive thresholds for young brains** — detection sensitivity that
   scales with data volume; when a 3-repeat pattern deserves attention.

Drafts are produced by the brain itself (`lhassa research draft <topic>` →
`doc/notes/drafts/`) from an evidence pack with per-datum provenance, and are
NEVER published without the owner's review — a draft header says so.
Instrument rows in that pack are topic-filtered, keep their row ids, and use a
real seven-day episode window; global spend/approval readings are labeled
operational context rather than evidence for the topic.

## The owner of this line: the geshe

The temple's scholar-monk (`geshe` — deliberately the one Tibetan id among
the monks: the geshe is the monk who earned the doctorate) OWNS this line's
method — how we write, draft, build and focus — codified in
[`../research/METHOD.md`](../research/METHOD.md) and carried as its
`research-method` skill so every research think applies it.
`draft_research_note` is its action.

## The corpus

The knowledge base behind this line lives in [`../research/`](../research/):
one document per studied idea/technology (loop engineering, graph engineering,
retrieval-as-code, memory sovereignty, mixture-of-agents, token-efficient
serialization, eval-driven agents, output quality), each taken down to the
base with sources, the honest mapping to what Lhassa built, and the experiment
that would verify it on our instruments.

## The principle

The research line becomes true as a **documented byproduct of real work**.
The instruments run every day; a note is written when the data already tells
the story.

## From research to a product change

A corpus draft is publishable knowledge, not permission to edit a product.
Adoption is a separate, two-approval protocol:

1. `lhassa research propose-change <project> <draft> --objective "..." --verify "..."`
   re-runs the objective corpus gate, locks the draft by SHA-256, records a
   `product_change` artifact, and parks an **experiment approval**.
2. After that approval, `lhassa research build-change <change-id>` creates a git
   worktree under `LHASSA_ADOPTION_DIR` (outside the product checkout) and launches
   the coding agent with that worktree as its workspace. The acceptance command
   runs through the existing secret-scrubbed, network-isolated quarantine. If
   enforceable isolation is not available, the gate fails closed.
3. A passing experiment becomes an immutable candidate commit and parks a
   separate **integration approval**. Nothing merges as a side effect of the
   experiment approval.
4. `lhassa research apply-change <change-id>` checks that the research, candidate,
   product base, and clean checkout are unchanged, then creates a merge commit.
   The adoption record stores the exact `git revert` rollback command.

This makes the direction one-way and explicit:

`world evidence → internal corpus → approved external experiment → objective gate → approved product change`

Research can recommend. It cannot authorize its own implementation or judge its
own result.
