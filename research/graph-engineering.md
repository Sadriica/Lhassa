# Knowledge graph engineering — the prompt-built pipeline that replaces trained NLP

> Draft research note — not yet reviewed by the owner. Part of Lhassa's research
> line (`doc/research.md`): "Memory architectures on humble hardware."

## The idea in one sentence

A knowledge-graph pipeline built entirely from four sequenced Claude API calls
— extraction, resolution, assembly, querying — replaces a classical NLP stack
(trained NER, trained relation classifier, hand-tuned entity-resolution
heuristics) with structured-output prompts, and gives multi-agent systems a
durable, provenance-carrying shared memory that survives any single agent's
context window [S3].

## Mechanics — down to the base

The pipeline (Fig. 1 of [S3]) has four stages, each a distinct Claude call
with a distinct model choice, and the choice of model per stage is itself an
engineering decision the note argues for explicitly (Table IV, [S3]):

1. **Extraction (Haiku).** One call per document. The prompt asks Claude to
   extract only entities *central* to the document (skip incidental mentions),
   write a one-sentence disambiguation description grounded in that document
   for each entity, use short verb-phrase predicates ("commanded," "launched
   from," "part of" — not vague ones like "was involved with"), and ensure
   every relation connects two entities actually extracted (no dangling
   references) [S3]. The output shape is a Pydantic schema
   (`EntityType = Literal["PERSON","ORGANIZATION","LOCATION","EVENT",
   "ARTIFACT"]`, `Entity{name, type, description}`, `Relation{source,
   predicate, target}`, `ExtractedGraph{entities, relations}`) passed to
   `client.messages.parse(..., output_format=ExtractedGraph)` — the note's
   central framing is that **the schema is the training data**: no labeled
   corpus, no domain-specific model, just a typed contract the API response
   is guaranteed to validate against, or an error, never silent corruption
   [S3]. Each of the four guidelines maps to one failure mode observed in
   earlier iterations: "central only" controls recall (precision-favoring on
   purpose), the description guideline supplies the disambiguation signal
   resolution needs, the short-verb-phrase constraint keeps predicates
   traversable, and "every relation connects two extracted entities" prevents
   orphaned edges [S3].
2. **Resolution (Sonnet).** Raw extraction produces overlapping surface
   forms of the same entity ("Neil Armstrong" / "Neil Alden Armstrong,"
   "Edwin Aldrin" / "Buzz Aldrin" — zero character overlap); string
   similarity plus blocking rules, the classical answer, fails on
   zero-overlap aliases [S3]. The resolution prompt instead clusters
   same-type entities using the *descriptions written during extraction* as
   disambiguation context, under three hard constraints: every input name
   must appear in exactly one cluster (unmatched → single-element cluster, so
   nothing is silently dropped), entities that merely share a name but are
   genuinely distinct get their own cluster (no over-merging), and the
   canonical name should be the most complete form [S3]. The note is
   explicit that this stage's power comes entirely from the descriptions: an
   extraction prompt that skips them produces a resolver that can only match
   by surface form — "exactly the string-similarity failure mode the whole
   approach is designed to avoid" [S3].
3. **Assembly (NetworkX, no model call).** Every relation endpoint is
   rewritten to canonical form and loaded into a `MultiDiGraph` (multiple
   distinct predicates between two nodes, direction matters). Each edge
   carries its predicate, source document, and mention count [S3]. Selective
   summarization runs only on hub nodes (degree ≥ 3): Sonnet pools every
   mention plus the node's graph neighborhood into an `EntityProfile` (2-3
   paragraph summary, 3-5 traceable atomic key facts, a `TimeRange`),
   instructed to resolve contradictions by preferring the more specific claim
   and never invent facts unsupported by the excerpts [S3].
4. **Querying (Sonnet).** A question seeds a subgraph via breadth-first
   traversal from a matched entity out to `k` hops (`serialize_subgraph`,
   Appendix E); the subgraph is serialized as triples
   (`(Armstrong) --[walked on]--> (Moon)`) and Sonnet is instructed to
   "Answer using ONLY the knowledge graph below" and cite the specific edges
   supporting each claim [S3]. `k=2` is the named sweet spot: `k=1` misses
   indirect connections, `k=3` may exceed the context window before
   filtering [S3].

**Why conservative is the right target, not a defect.** On a six-document
Apollo corpus (Wikipedia summaries of the Apollo program, Apollo 11, Neil
Armstrong, Saturn V, Buzz Aldrin, Kennedy Space Center), Haiku's raw
extraction scored precision 1.00 against recall 0.38-0.55 across the two
gold-scored documents (Table III, [S3]: Apollo 11 — F1 0.71, precision 1.00,
recall 0.55; Neil Armstrong — F1 0.55, precision 1.00, recall 0.38). The note
argues this asymmetry is deliberately the right regime for a graph, not a
tuning failure to fix blindly: "false entities spawn false relations... a
missing entity gives an incomplete-but-correct graph, a wrong one actively
misleads" — a false positive is harder to detect and more damaging than a
false negative because a wrong entity propagates wrong relations through
every downstream multi-hop query that touches it, whereas a missing entity
just leaves a gap [S3]. The missed entities in the gold set are traced to two
named causes, both prompt-tuning problems rather than model limitations:
peripheral mentions the "central only" instruction correctly filtered
("Purdue University" in the Armstrong article), and scope mismatches between
per-document extraction and a cross-document gold set ("Saturn V" appears
centrally in its own article but only peripherally in the Apollo 11 article
where it was scored) [S3]. Relation scoring itself only checks
`(source, target)` pairs, ignoring predicate wording, on the stated grounds
that predicate synonymy ("commanded" vs. "led" vs. "was commander of") is
less structurally important than getting the connections right [S3].

**Where the graph slots into each of Anthropic's five documented agent
patterns** (Table II/VII of [S3], attributed to Anthropic's "Building
Effective AI Agents" documentation [S3]):

| Pattern | KG role | How it helps |
|---|---|---|
| Augmented LLM | Retrieval source | Graph traversal replaces vector search for multi-hop questions; the LLM queries the graph as a tool. |
| Prompt chaining | Gate signal | Between chain steps, a graph query checks whether new entities conflict with existing nodes. |
| Routing | Classifier input | Entity type and degree from the graph route a query to the right specialist without an LLM call. |
| Orchestrator-workers | **Shared memory** | Workers read from and write to the graph directly; the orchestrator's context window stays small — this is the pattern the note develops most, with a worked five-worker competitive-intelligence example in Appendix B where the strategic synthesizer never re-reads the workers' raw documents, only queries the graph [S3]. |
| Evaluator-optimizer | **Grounding layer** | The evaluator checks the generator's claims against graph edges with explicit provenance, rather than judging plausibility from pretraining — turning the evaluator "from a reader to a fact-checker": "triple (X, works_at, Y) does not exist in the graph; the graph contains (X, left, Y) from document Z" [S3]. |

A sixth framing recurs across the note without a table row: **persistent world
model for overnight/looped agents** — "a self-improving loop needs memory
that survives context-window flushes. The graph is that memory... this is
the knowledge-graph equivalent of 'the agent forgets, the repo does not.'"
New documents are extracted, resolved against the *existing* canonical set
(never rebuilt from scratch), and a node is only re-summarized when its
source-document set changes materially [S3].

**Scaling guidance** (Section IX, [S3]) rests on four levers: (1) *blocking
before resolution* — feeding ten thousand PERSON entities to one Claude call
does not work; group candidates first by cheap deterministic signals (same
surname, overlapping tokens, embedding similarity, an inverted index, no
model call), then let Claude arbitrate only *within* blocks of 50-100 —
"keep the model for the parts that require judgment, and use deterministic
logic for everything else" [S3]; (2) *incremental updates* — resolve a new
document's entities against the existing canonical set, never against each
other, and re-summarize a node only when its source set changes; (3)
*storage tiers* — NetworkX in memory up to a few hundred thousand edges,
then a property graph (Neo4j/Neptune) or three plain Postgres tables
(`entities`, `relations`, `aliases`) with multi-hop traversal as recursive
CTEs — "the choice between them is an infrastructure decision, not a
pipeline decision" [S3]; (4) *chunking for long documents* at section
boundaries with paragraph overlap, since naive token-count chunking splits
entities from the relations that describe them [S3].

**Monitoring signals** (Section IX-F, [S3]): *extraction rate* (entities/
relations per document — a drop signals a domain shift, a spike signals
over-extraction); *resolution compression ratio* (raw surface forms ÷
canonical entities — near 1.0 means resolution is "doing nothing," above
2.0 means it is "earning its cost"); *graph connectivity* (connected
components — a growing disconnected count means missed cross-document
links); *query latency* (subgraph serialization pre-computed for
high-traffic seeds in real-time use). Paired with a production-readiness
checklist (Appendix D, Table VII): gold set, alias-map coverage, schema
versioning, a per-run extraction cap, provenance on every edge,
connectivity monitoring, and a human-sample discipline ("pick a random node
each day, read its profile, check its edges against the source documents" —
the graph analogue of reading a sample of a loop's PRs to guard against
comprehension rot).

**RAG vs. knowledge graph is framed as complementary, not competing**
(Section X-A and Appendix C, [S3]): RAG excels at single-hop questions where
the answer exists in one retrieved passage; it breaks down on multi-hop
questions requiring facts chained across passages that share no lexical or
semantic similarity with each other or with the query. A knowledge graph
bridges exactly that gap because the connecting entity is an explicit node
with edges to both source passages, discoverable by traversal regardless of
surface-form similarity between the passages themselves [S3]. The decision
table (Table VI, [S3]) resolves this into a rule of thumb: reach for a
knowledge graph specifically when agents need to **chain** facts across
sources, **share** structured state, or **ground** judgments in traceable
evidence — a single agent doing simple classification or single-document QA
needs neither RAG nor a graph, and multi-doc-single-hop needs RAG with
reranking, not a graph [S3].

## Evidence the authors present

- **Table III (Extraction quality against gold set):** Apollo 11 document —
  raw F1 0.71, precision 1.00, recall 0.55; Neil Armstrong document — raw F1
  0.55, precision 1.00, recall 0.38 (resolved-recall unchanged in both) [S3].
  This is the note's only quantitative evaluation, run on a **six-document
  corpus the note itself calls small** ("production knowledge graphs are
  built from thousands" [S3]) — explicitly flagged as a demo, not a
  production benchmark.
- **The Apollo graph's structural statistics** are reported as diagnostic,
  not evaluative: 22 nodes, 34 edges, 1 connected component (evidence
  resolution worked — fragmented islands would indicate unmerged variants);
  an edges/nodes ratio of 1.55, framed as "the healthy middle range" between
  1.0 (sparse) and 2.0+ (richly connected), with no independent basis cited
  beyond the author's own framing [S3].
- **No externally reproducible benchmark or ablation is presented.** The gold
  set is described but not published; there is no baseline comparison
  against a trained NER/relation-extraction system, no confidence interval,
  no repetition across corpora. The evaluation is a worked demonstration of
  the harness (change the prompt, rerun the scorer, watch F1 move —
  explicitly likened to closed-loop optimization) rather than a claim the
  specific numbers generalize.
- **Cost claims are qualitative, not measured**: "the extraction cost at
  Haiku rates is measured in single-digit dollars" for a hypothetical
  10,000-document corpus — no actual invoice or run log is shown [S3].
- **The competitive-intelligence worked example (Appendix B)** is explicitly
  labeled illustrative: "Code examples are adapted from the public cookbook
  for pedagogical clarity" (Acknowledgment & Sources, [S3]).

## Sources

- [S3] "Knowledge Graph Engineering for Multi-Agentic Systems: The Anthropic
  Playbook" — 12-page working note, July 2026, read directly from the local
  copy at `scratchpad/graph-engineering.pdf`. **Attribution chain, stated
  plainly**: the document's own title page and closing footer both say it is
  "an independent synthesis assembled for study. It is not a publication of,
  and is not affiliated with or endorsed by, Anthropic" [S3]. It names its
  real sources as Anthropic's public `claude-cookbooks` repository
  ("Knowledge Graph Construction with Claude"), Anthropic's "Building
  Effective AI Agents" engineering writing (E. Schluntz & B. Zhang, Dec.
  2024), and "Scaling Managed Agents" (L. Martin, G. Cemaj & M. Cohen, Apr.
  2026) [S3]. The tweets that circulated it, however, **over-attribute it**:
  [S1]/[S4] both describe it as a "PDF de 12 páginas de un ingeniero senior
  de Anthropic" ("a 12-page PDF from a senior Anthropic engineer") — a claim
  the document itself contradicts. [S5] goes further and misattributes the
  same PDF to Andrej Karpathy ("ANDREJ KARPATHY JUST RELEASED A 12-PAGE GUIDE
  ON BUILDING SELF-IMPROVING MULTI-AGENT GRAPHS"), with the tweet's own t.co
  link resolving back to itself rather than to any Karpathy-owned source —
  treated here as a low-quality, near-certainly-false attribution, cited
  only to document that the misattribution exists in circulation, not as a
  fact about the PDF's authorship.
- [S1] angeldot_, X post announcing a 15-agent deployment "using Graph
  Engineering," https://x.com/angeldot_/status/2081447553930006616 (Jul 26,
  2026). Links a login-walled article whose base source is [S3]; used only
  as a distribution/attribution data point, not evidence for a pipeline
  claim.
- [S4] nicos_ai, X post distilling [S3]'s five phases,
  https://x.com/nicos_ai/status/2081066452694556956 (Jul 25, 2026). Secondary
  paraphrase; contributes nothing beyond [S3]'s own text.
- [S5] roundtablespace, X post misattributing [S3] to Andrej Karpathy,
  https://x.com/roundtablespace/status/2081707421756457213 (Jul 27, 2026).
  Cited only for the misattribution-chain note above.
- [S7] leopardracer, X thread on retrieval-as-code,
  https://x.com/leopardracer/status/2082063601159004517 (Jul 28, 2026). Used
  only for its RAG-vs-graph framing, which corroborates [S3]'s own decision
  table (see `research/retrieval-as-code.md` for the fuller treatment).
- [S8] thisguyknowsai (quoted by roundtablespace), on the `jcode` project's
  semantic memory graph, https://x.com/thisguyknowsai/status/2081680037544427793
  (Jul 27, 2026); repo https://github.com/1jehuang/jcode. Used only for the
  "graph queried every turn, a sideagent verifies hits" pattern as a second
  independent data point beyond [S3] — not independently verified (repo not
  inspected for this note, taken from the sources-pack summary as-is).

## Mapping to Lhassa

Lhassa already runs a real, persisted knowledge graph — `lhassa/memory/kg.py`
(the domain module, validation discipline) + `lhassa/core/db.py` migration 6
(`kg_entities`/`kg_edges`, dumb storage) + `lhassa/memory/graph_retrieve.py`
(the read seam) — built independently of [S3] but converging on several of
the same structural choices, and diverging on several others worth naming
explicitly:

**Where it converges.**
- **Closed edge-kind vocabulary + provenance per edge.** `kg.py`'s
  `EDGE_KINDS = (co_occurs, mentions, derived_from, asserts)` is closed,
  explicitly modeled on `lhassa/org/relationships.py`'s own closed
  vocabulary and validation discipline (`RelationshipError`,
  `SYMMETRIC_KINDS`, canonical-sorted storage for symmetric edges) — that
  module predates the KG one and is named in `kg.py`'s docstring as the
  pattern it mirrors. Every `kg_edges` row carries a `source` column
  (`fact:<id>` / `episode:<id>`) — the same provenance discipline [S3]
  requires.
- **Blocking before expensive resolution.** `kg.py:resolve()`'s
  `_candidate_pairs()` groups same-`kind` entities into exact-normalized
  matches (auto-merge, no model) and fuzzy candidates (shared ≥3-char token
  or `difflib` ratio ≥0.6, capped) — structurally [S3]'s recommended
  two-tier pattern: "cheap deterministic blocking plus expensive LLM
  arbitration within blocks." Narrower blocking signals than [S3]'s (no
  embedding similarity), but the same shape — deterministic first pass,
  model only for the genuinely ambiguous remainder, never guessed.
- **Incremental, never-rebuild feeding.** `kg.py:sync()` walks facts/
  episodes *since* a `watch_state` marker, the same "resolve new documents
  against the existing canonical set, never rebuild" principle [S3] names.
- **The `asserts` edge kind is Lhassa's version of [S3]'s typed `Relation`
  triples** — opt-in (`kg_triples_enabled`/`LHASSA_KG_TRIPLES`, off by
  default), fed by a cheap-model pass mirroring `memory/consolidate.py`'s
  degrade-never-raise pattern.

**Where it diverges — named as candidate increments, not defects:**
- **No entity descriptions.** [S3]'s entire resolution stage draws its power
  from the one-sentence disambiguation description written at extraction
  time ("the disambiguation power... comes *entirely* from the one-line
  descriptions" [S3]). Lhassa's `kg_entities` schema has no description
  column; entities carry only `name` + `kind` (`tag`/`term`). Lhassa's
  fuzzy-resolution model call (`_resolve_messages`) therefore judges
  same-entity-hood from name strings alone — closer to the naive
  string-similarity approach [S3] built the description-based method to
  replace ("Edwin Aldrin"/"Buzz Aldrin," zero character overlap, is exactly
  the case [S3] says descriptions solve and string similarity cannot).
- **No entity typing beyond `tag`/`term`.** [S3]'s `EntityType` (PERSON,
  ORGANIZATION, LOCATION, EVENT, ARTIFACT) scopes blocking and resolution by
  type. Lhassa's `kind` column distinguishes only *provenance* (a fact's tag
  vs. a text-extracted term), not real-world category.
- **No profiles/summarization.** [S3]'s `EntityProfile` (hub-node synthesis,
  degree ≥ 3, 2-3 paragraph summary + traceable key facts + `TimeRange`) has
  no Lhassa equivalent — `kg_entities` rows never accumulate a synthesized
  summary beyond their bare name.
- **No k-hop subgraph serialization.** `graph_retrieve.py:retrieve()`
  expands exactly one hop and stops, deliberately, per its own docstring
  ("kept deliberately small"). [S3]'s `serialize_subgraph(center, hops=2)`
  generalizes to k-hop breadth-first traversal; Lhassa's version cannot
  answer a genuinely two-hop question the way [S3]'s Apollo example does
  ("Which locations are connected to people who flew on Apollo 11?").
- **No graph-grounded evaluator loop.** [S3]'s evaluator-optimizer
  integration (Section VII-B.1) — an evaluator that queries the graph for
  the specific triple a generator claims and flags unsupported claims with
  the contradicting edge — has no analogue in Lhassa. `agents/verify.py`
  verifiers are explicitly non-LLM and don't query `kg_edges` at all.
- **No monitoring signals computed.** [S3]'s four production metrics
  (extraction rate, resolution compression ratio, connected components,
  query latency) have no dashboard or trend in Lhassa; `sync()`/`resolve()`
  return raw counts per call but nothing accumulates them over time.
- **The `asserts` predicate lives in a string hack, not a column.** Because
  `kg_edges` has no predicate column, the predicate rides inside `source`
  (`"fact:<id>|pred:<verb>"`), split by a reader on `"|pred:"` — an honestly
  documented workaround, but a real divergence from [S3]'s `Relation.
  predicate` being a first-class typed field.

## Not yet verified by us — and the experiment that would

- **Lhassa has never measured precision/recall for its own extraction.**
  `kg.py:sync()`'s regex/tag term extraction has no gold set and no scored
  evaluation — unlike [S3]'s Table III. The experiment: hand-label 10-20 real
  Lhassa facts/episodes for the entities and co-occurrence relations a human
  judge would extract, run `sync()` against them, and score the output the
  same way Table III does — this would show whether Lhassa's $0 regex
  extractor sits in Haiku's precision-favoring 1.00/0.38-0.55 regime or
  somewhere worse (regex has no "central only" judgment at all, so recall
  could plausibly be higher and precision lower — genuinely open, not
  assumed).
- **The resolution compression ratio has never been computed for a live
  instance.** The experiment: run `resolve()` against a real instance's
  `kg_entities`, compute `candidates / merged` (matching [S3]'s ratio
  definition), and check whether it lands in [S3]'s "healthy" range or is
  dominated by trivial casefold variants ("budget"/"Budget") rather than the
  genuinely hard cases the description-based method targets.
- **Whether entity descriptions would improve `resolve()`'s fuzzy-match
  quality is untested.** The experiment: for a batch of fuzzy candidates,
  re-run the judgment call with one added context sentence per entity (the
  fact/episode text it came from, playing [S3]'s description role) and
  compare agreement against a small hand-labeled true/false set, with and
  without that sentence.
- **The RAG-vs-KG decision table's validity for Lhassa's own workload is
  unverified.** [S3]'s Table VI is a framework, not a measured result on
  Lhassa data; nothing compares `memory/load.py`'s semantic-only retrieval
  against `graph_retrieve.py`'s graph-augmented retrieval on the same
  multi-hop-shaped queries over Lhassa's own fact corpus.

## Open questions

- Is Lhassa's 1-hop-only `graph_retrieve.py` a permanent design choice
  (matching [S3]'s own framing that k=1 "misses indirect connections" but is
  fast and low-noise), or a first increment that should grow toward k=2 once
  a real multi-hop need appears — given the house's stated "1-hop is the
  increment that pays" decision (`doc/decisions.md`, 2026-07-28)?
- Should `kg_entities` grow a description column at all, given [S3]'s claim
  that resolution quality depends entirely on it — or does Lhassa's much
  smaller, single-owner entity vocabulary make the zero-overlap-alias
  problem rare enough in practice that the gap doesn't matter yet? The
  resolve() experiment above would partially answer this empirically.
- Does `agents/verify.py` have a natural, $0 extension point to become a
  graph-grounding fact-checker the way [S3]'s evaluator-optimizer pattern
  describes — a verifier kind that queries `kg_edges` for a claim's
  supporting triple — and would that fit "verifiers never call an LLM,"
  since the check itself is a deterministic graph query, not a judgment?
- [S3]'s orchestrator-workers example has each worker writing directly to a
  shared graph the orchestrator queries — no department worker in Lhassa
  currently writes to `kg_entities`/`kg_edges` while executing a delegated
  task (only `kg.py:sync()`'s background feeder does). Would wiring
  department results into the graph be a faithful implementation of [S3]'s
  shared-memory pattern, or conflict with the existing episode-based "every
  brain logs its own hop" audit trail the org layer relies on?
</content>
