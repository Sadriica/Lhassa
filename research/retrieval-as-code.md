# Retrieval as code — deterministic selection before the model ever opens a file

> Draft research note — not yet reviewed by the owner. Part of Lhassa's research
> line (`doc/research.md`): "Memory architectures on humble hardware."

## The idea in one sentence

Point an agent at a folder of thousands of notes and every question reads all
of them by default — expensive and unscoped; the fix is to make retrieval a
small, deterministic PROGRAM (a router file + an index + a scoring function)
that decides what the model gets to see, so the model reads three files
instead of five thousand [S7].

## Mechanics — down to the base

The claim, as summarized in the source thread, is a pipeline of twelve named
steps collapsing into six checkpoints: `CLAUDE.md → index.md → nodes → edges →
retrieval as code → memory → ship it` [S7]. The shared rule threading all of
them is stated as one sentence: **evidence lives in the index** — the model's
context window holds a router file and an index file (small, cheap, always
read), while the actual notes ("nodes") sit untouched on disk until the index
says a specific one is relevant [S7]. The mechanism the thread credits for
making this work is explicit: "code that SCORES before the model ever opens
anything" — i.e., retrieval is not "give the model a `search` tool and let it
decide," it is a deterministic ranking function that runs first, outside the
model, and only the winners are ever materialized into the prompt [S7]. The
default failure mode this design targets is named directly: point Claude at a
flat folder and "every question reads all of them — that's the default and
it's expensive" [S7]. The result reported is a token-cost cut of about 40% for
one team's "second brain" versus reading the raw pile [S7].

The thread also names a specific failure test for this architecture — "the
stale index test": one line in the index drifts out of sync with the notes it
describes, and "the whole graph quietly turns back into the pile it was built
to escape" [S7]. That is: the entire value of retrieval-as-code depends on the
index staying an accurate proxy for the underlying files; a stale index is
worse than no index because it looks authoritative while silently lying.

Two adjacent pieces widen the mechanics without adding new primitives:

- **RAG and a knowledge graph are complementary, not competing, retrieval
  modes.** RAG suits single-hop direct retrieval (find the passage that
  answers this question); a graph suits multi-hop/structural questions
  (traverse relationships a flat similarity search cannot reconstruct) — the
  independent synthesis behind [S3]/[S4] frames this as a decision table, not
  an either/or, and explicitly reuses the graph as a grounding layer an
  evaluator can fact-check claims against, with provenance per edge. This
  matters to a retrieval-as-code design because the "index" in [S7]'s pipeline
  is graph-shaped (nodes + edges), not a bare vector store — the scoring
  function it runs before the model touches anything can therefore include
  structural signals (which node is connected to which), not only embedding
  similarity.
- **RAG has known engineering failure modes upstream of any index**, per a
  503-lesson open curriculum's treatment of the topic: chunking strategy and
  reranking are called out as first-class, separately-taught concerns, not
  incidental to "just embed and retrieve" [S11]. A retrieval-as-code index is
  only as good as the chunking that produced its nodes and the (possible)
  reranking step that orders candidates before the deterministic scorer picks
  winners — sloppy chunking silently degrades the same "evidence lives in the
  index" guarantee [S7] rests on, independent of whether the index itself is
  stale.

## Evidence the authors present

- A token-cost reduction of "40%" attributed to one team building this pattern
  for a personal note corpus, framed against the default (unscoped, read-all)
  behavior [S7]. No methodology, corpus size, or measurement window is given
  in the thread.
- The "12-step roadmap" article this thread summarizes and links
  (KingWilliam, "Graph engineering: the 12-step roadmap from note-taker to
  graph builder") is the primary source for the step breakdown, but it is
  behind a login wall and **was not captured** — everything above is the
  thread's own paraphrase of that article, not the article itself [S7].
- No benchmark, ablation, or reproducible corpus accompanies the 40% number;
  it reads as one practitioner's report, not a measured result in the sense
  Lhassa's own research method requires (`doc/research.md`: "a technique
  enters only after a measured comparison... 'it seems better' is not a
  result").

## Sources

- [S7] leopardracer, X thread on a second-brain / retrieval-as-code build,
  https://x.com/leopardracer/status/2082063601159004517 (Jul 28, 2026).
  Links KingWilliam's article (login-walled, not captured; the 12-step
  breakdown above is this thread's paraphrase of that article, not a direct
  read of it).
- [S3] Independent synthesis, "Knowledge Graph Engineering for Multi-Agentic
  Systems: The Anthropic Playbook" (12-page PDF, July 2026) — compiled from
  Anthropic's public Knowledge Graph Cookbook, "Building Effective AI Agents,"
  and Claude API docs; explicitly **not** Anthropic-affiliated or endorsed
  despite how it is framed in the tweets that link it. Used here only for its
  RAG-vs-KG complementarity framing (decision table, evaluator-as-grounding
  pattern), read directly from the local copy at
  `scratchpad/graph-engineering.pdf`.
- [S4] nicos_ai, X post distilling [S3]'s five phases,
  https://x.com/nicos_ai/status/2081066452694556956 (Jul 25, 2026). Used only
  as a secondary confirmation of [S3]'s "the graph doesn't rebuild, it grows"
  framing.
- [S11] rohitg00, `ai-engineering-from-scratch` (503 lessons / 20 phases,
  MIT), https://github.com/rohitg00/ai-engineering-from-scratch — Phase 13/14
  lesson index (read from the sources pack summary, not independently
  re-fetched for this note) cited for the chunking/reranking framing.

## Mapping to Lhassa

Lhassa already runs a real, code-reviewed instance of "retrieval as code" —
independently arrived at, not built from these tweets:

- **`lhassa/memory/load.py`** is a deterministic, ranked, budget-truncated
  selector that runs entirely outside any model call. It layers context by a
  fixed `PRIORITY` list (`pinned, recent, tags, semantic, recall, knowledge,
  graph`) and `truncate()` drops the lowest-priority layer first under a
  token budget — the "identity core" (directives + pinned facts) is
  mandatory and never dropped (`lhassa/memory/load.py:53,182-243`). This is
  the same shape [S7] describes — "code that scores before the model ever
  opens anything" — except the scoring key here is an explicit priority rank
  plus, within the semantic/knowledge/graph layers, cosine similarity
  (`_semantic_layer`, `lhassa/memory/load.py:246-287`), not an LLM judgment
  call.
- **`lhassa/notify/intent.py:classify()`** is a second, earlier deterministic
  gate: before any retrieval happens at all, a pure-regex classifier decides
  whether a chat message is `command` / `live_data` / `cacheable` /
  `judgment` — "thinking is the exception," per its own docstring
  (`lhassa/notify/intent.py:1-29`). This is retrieval-as-code one layer up:
  deciding whether to retrieve/think at all is itself a deterministic,
  pre-model program, mirroring [S7]'s router-file step conceptually (decide
  the path before opening anything).
- **`lhassa/memory/knowledge.py:ingest()`** is Lhassa's own version of [S7]'s
  stale-index problem, already solved rather than merely diagnosed: each
  indexed chunk is stamped with the source file's `mtime` and `size`, and a
  file is only re-chunked/re-embedded when either has changed
  (`lhassa/memory/knowledge.py:205-219`, the `prior` check). This is a
  literal, running answer to "the stale index test" [S7] names as the
  architecture's chief failure mode — Lhassa's index cannot silently drift
  out of sync with its source files because staleness is the trigger for
  re-indexing, not an assumption.
- **`lhassa/memory/answer_cache.py:lookup()`**'s `stats` out-parameter
  (`best_similarity`, `scanned`, `hit`) gives a caller visibility into *why* a
  retrieval missed — a near-miss under threshold vs. nothing to scan vs. an
  embedder failure (`lhassa/memory/answer_cache.py:110-166`). This is a
  near-miss diagnostic that [S7]'s thread does not describe having (it names
  the failure mode — a stale index — but not an instrumented way to see a
  retrieval degrade in real time).

## Not yet verified by us — and the experiment that would

- **The 40% token-cost claim [S7] reports has no equivalent measured number
  in Lhassa yet.** The experiment: instrument `memory/load.py`'s knowledge
  layer (`_knowledge_layer`) with a before/after token count — run a fixed
  set of queries against a real `knowledge_roots` corpus once with the
  knowledge/recall/graph layers disabled (forcing a hypothetical "read
  everything" baseline reconstructed from the raw chunk table) and once with
  them enabled as they run today, and compare `estimate_tokens()` totals via
  `lhassa bench run` or an equivalent instrumented trial, per
  `doc/research.md`'s own method ("a technique enters only after a measured
  comparison"). Until that trial exists, [S7]'s 40% number is a citation, not
  a Lhassa result.
- **Whether Lhassa's index can go stale the way [S7]'s does.** The mtime+size
  check (`memory/knowledge.py`) guards against a *changed file* going
  unnoticed, but does not guard against a *deleted or moved* file leaving
  stale chunks behind, nor against the chunk boundaries themselves becoming a
  poor proxy for the file's current content after a small in-place edit that
  doesn't change size meaningfully. A staleness experiment worth running:
  edit a knowledge-root file's content while holding its byte size constant
  (or nearly so) and confirm the chunk re-indexes anyway (it should, since
  mtime alone triggers re-check) — but also probe the deleted-file case,
  which `ingest()` does not appear to handle (no code path removes chunks for
  files no longer present under a root).
- **Whether structural (graph) signals actually change retrieval quality**
  over cosine-only ranking in Lhassa's own `_graph_layer` (off by default,
  `config.memory_graph_enabled`) — [S3]'s RAG-vs-KG complementarity claim is
  itself uncited/unmeasured beyond the PDF's own Apollo-corpus numbers
  (precision 1.00, recall 0.38-0.55), which were not independently
  re-verified for this note.

## Open questions

- Does [S7]'s router-file step have a Lhassa equivalent beyond
  `notify/intent.py:classify()` — i.e., is there value in an explicit,
  human-readable `index.md`-shaped file the way the thread describes, versus
  Lhassa's current approach of encoding routing logic directly in Python
  (`PRIORITY`, `classify()`)? The tweet's argument for a router FILE
  specifically (not router code) is that it's legible/editable by a human
  without touching code — Lhassa's settings service (`settings.py`) covers
  some of this (toggling layers on/off) but not the ranking logic itself.
- What does "evidence lives in the index" mean for Lhassa's `answer_cache`
  specifically — should a cache hit carry a pointer back to the knowledge
  chunk or episode that grounded the original answer, the way [S7]'s nodes
  are meant to stay the single source of truth behind the index's claims
  about them? Today `answer_cache` rows store only the question/answer/
  embedding, not a provenance link to what grounded the cached answer.
- Is there a cheap way to detect the deleted/moved-file staleness gap named
  above without a full re-walk of every knowledge root on every heartbeat
  (which would undercut the $0-per-tick design the rest of the system holds
  to)?
</content>
