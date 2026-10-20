# Memory

Lhassa's memory is not a single database; it is a small system of layers, each
with a distinct job, described in `doc/concepts.md` and enforced by the
architecture guard (`memory/` sits in the interfaces layer, below every shell).
This document is the subsystem reference: what is stored where, how it is
loaded at think time, and how to inspect or manage it from the CLI, the API,
or the dashboard. For the *why* behind the design (the grande/chica split, the
append-only discipline), see [`concepts.md`](concepts.md).

---

## What lives where on disk

| File | Default path | Config knob | Role |
|---|---|---|---|
| **brain.db** (the grande) | `~/.lhassa/brain.db` | `LHASSA_BRAIN_DB_PATH` | Append-only source of truth: `episodes`, `facts`, `tasks`, `tools`, `approvals`, and more; see `lhassa/core/db/`. |
| **chica.db** (the chica) | `~/.lhassa/chica.db` | `LHASSA_CHICA_DB_PATH` | Derived working cache, rebuilt from the grande; `lhassa/memory/chica.py`. |
| **memory.db** (legacy) | `~/.lhassa/memory.db` |; | The original voice-session store (`facts` / `sessions` / `scheduled_tasks`) from before the grande/chica architecture existed; `lhassa/memory/store.py`. Still wired to the voice pipeline's system-prompt injection (`voice/pipeline.py`) and to shutdown fact/summary extraction (`memory/session.py`), and still backs the scheduler's task table and the dashboard's Session History panel. It predates, and does not talk to, the grande/chica/fact model described below; treat it as a separate, older lane that happens to still be live, not as part of the current fact/context design. |

Both the grande and the chica open in WAL mode and scope every row by
`instance_id`: one brain's memory never leaks into another's, from day one.

---

## The fact model (the grande)

Facts are the durable, typed knowledge the grande carries. The schema lives in
`lhassa/core/db/` (`CREATE TABLE facts …`) and the typed API sits in
`lhassa/memory/facts.py`.

Every fact row carries:

- **`tipo`**: `dato` (a fact about the world/owner: "prefers dark mode") or
  `directiva` (a standing instruction: "answer concisely by default"). A
  `directiva` changes *how* Lhassa behaves and is always loaded into context;
  a `dato` changes *what* it knows.
- **`pinned`**: identity-grade facts that are never evicted or truncated.
- **`tags`**: free-form labels used for exact-membership context activation
  (not semantic; see layer 2 below).
- **`procedencia`**: where the fact came from (`distill:2026-07-05`,
  `consolidate_semantic`, a manual add, …); provenance, not proof.
- **`confianza`**: a confidence score (default `1.0`); lower for
  machine-inferred facts (e.g. `distill_episodes` writes at `0.6`).

**Facts are append-only.** `add_fact()` always inserts a new row; nothing is
ever destructively rewritten. A fact superseding an older one is written as a
fresh row with a newer `actualizado_en` and, typically, a different
`confianza`. Reading back one "current" view of the world is the job of
`resolve()` in `lhassa/memory/facts.py`: it groups facts by a normalized
content key and keeps the winner ranked by `(actualizado_en, confianza)`;
**recency leads, confidence breaks ties.** Order is deterministic
(first-appearance of each conflict key), so the same facts always resolve the
same way.

On every `add_fact()` call the fact is also embedded (best-effort; an
embedder failure never blocks the write) into `fact_embeddings`, the table
that backs semantic retrieval (layer 4, below).

---

## How context is loaded at think time

`lhassa/memory/load.py` builds the **chica context** for one think; a
`Context` object with a token budget, assembled from layers in strict
priority order (`PRIORITY = ["pinned", "recent", "tags", "semantic", "recall",
"knowledge", "graph"]`: document perception and the persisted-graph layer,
both below, are the two lowest and both fire only when a `query` is given):

1. **Pinned + directivas (identity; always).** All `pinned` facts and every
   `directiva` fact, folded together with the persona's own directives
   (`persona.load_identity`). This is the mandatory core: `truncate()` never
   drops it, even if it alone exceeds the budget.
2. **Recency.** The most recent episodes (`core.db.load_recent_episodes`);
   continuity from one wake to the next.
3. **Active tags.** Facts whose tags intersect the caller's `active_tags`;
   exact membership, not semantics.
4. **Semantic retrieval (layer 4).** Facts whose stored embedding is closest
   (cosine similarity) to the active `query`. Off by default; fires only when
   `LHASSA_MEMORY_LAYER4` is set **and** a `query` string is supplied **and**
   facts have been indexed (`memory.load.index_facts` / `reindex`). Vectors
   are only ever compared if they were produced by the same embedder model;
   mixed-model comparisons are skipped, not guessed at. See "Real embeddings"
   below for how the embedder itself is chosen.
5. **Episodic recall (layer 5).** The top few past episodes whose text
   matches the active `query`, via the FTS5 index over `episodes` (see
   below); `$0`, no model call, fully defensive (any failure degrades to an
   empty layer, never raises).

`truncate()` then trims the assembled context to the requested
`token_budget`, dropping items from the **lowest-priority layer first**:
recall goes before semantic, semantic before tags, tags before recency, and
the identity core never goes. Token counts are a deterministic estimate;
exact via `tiktoken` when the optional dependency is installed, else a
~3-chars/token heuristic (`estimate_tokens`); so budgeting never depends on
a network call.

The default behavior (layer 4 and `LHASSA_MEMORY_LAYER4` unset) is
byte-for-byte the pinned/recent/tags load that existed before semantic
retrieval was added; turning the knob on is strictly additive.

---

## Episodic recall (layer 5)

Beyond facts, Lhassa keeps a full-text index over its own append-only episode
log (every decision it has ever logged) so it can answer "what happened
last time…" at `$0`, no model call, BM25-ranked.

- **Storage:** `episodes_fts`, an FTS5 external-content table over `episodes`
  (`lhassa/core/db/`), kept in sync by insert/update/delete triggers and
  backfilled once on first creation. Some SQLite builds ship without FTS5;
  `init_db()` catches that case and disables recall rather than failing to
  start.
- **Search:** `core.db.search_episodes(instance_id, query, n)` sanitizes the
  query into a safe FTS5 `MATCH` expression (each token individually quoted,
  so stray `AND`/`OR`/parentheses/quotes can never raise a syntax error),
  ranks by `bm25()`, and demotes (never hides) automation-originated episodes
  so interactive history surfaces first. The penalty is **graded by how much
  outcome the episode carries**, added to the raw bm25 rank:

  | `despertar_por` | penalty | why |
  |---|---|---|
  | `monitor`, `task:*` | 5.0 | routine automation |
  | `org-deliver`, `org-delegate` | 5.0 | pure transport; `deliver:`/`forward:`/`delegate:` hops record movement, not outcome, and are the largest single source of episodes |
  | `temple-execute` | 2.0 | the hop that actually RAN the action and holds its real `resultado`, so "what happened with `curate_skills`?" still finds it |
  | everything else | 0.0 | interactive |

  Because the penalty is additive on bm25, at real corpus size a strong
  automation match still outranks a weak interactive one; a nudge, not a
  filter. (With only a handful of episodes bm25's IDF term collapses toward 0
  and the flat penalty dominates instead; the behaviour is a ranking bias, not
  a guarantee.)
- **Reachable directly:**
  - Telegram **`/recall <text>`** (`lhassa/notify/commands/`); returns the
    top 5 matches, LLM-free.
  - **`GET /api/episodes/search?q=...&n=...`** (`lhassa/api/reports_routes.py`)
   ; `n` capped `1..200`, returns `{query, count, results}`.
- It was the lowest-priority context layer until document perception (below)
  landed beneath it: a think only spends the tokens on it when a `query` is
  actually supplied.

---

## Real embeddings; the pluggable Embedder (layer 4)

`lhassa/memory/embeddings.py:get_embedder()` chooses which embedder backs
layer 4 (and document perception, below); nothing about the layer's logic
changes, only whether its vectors carry real semantic meaning or a
deterministic offline hash:

- **`HashingEmbedder`**: pure-stdlib, hashed token n-grams, no model, no
  network. The default when nothing else is reachable; deterministic (same
  text → same vector), which is why the test suite pins
  `embedder_backend="hash"`: a live local Ollama must never change a test's
  outcome.
- **`RemoteEmbedder`**: speaks Ollama's native `/api/embed` (with a fallback
  to the older singular `/api/embeddings`) or an OpenAI-compatible
  `/v1/embeddings` endpoint, resolved PER CALL; any failure degrades silently
  (and logs at most once per process) to `HashingEmbedder`: a down daemon
  never blocks a think.
- **`LHASSA_EMBEDDER_BACKEND`** gates resolution (`auto`: the pre-existing
  explicit knobs win first, else probe a local Ollama and fall back to
  hashing; `ollama`: force the native backend, degrade to hashing if
  unreachable; `hash`: always offline). `LHASSA_EMBEDDER_MODEL` (default
  `nomic-embed-text`) names the Ollama model.
- **The fingerprint guard.** Mixing vectors from different embedders makes
  cosine comparisons garbage, so every embedder exposes a stable
  `fingerprint` (the CONFIGURED backend+model, e.g. `ollama:nomic-embed-text`)
  distinct from `name` (the backend that ACTUALLY answered the last call;
  what gets stamped onto stored vectors). `needs_reindex(stored_model,
  embedder)` is the cheap per-row check the semantic-query filter already
  uses to skip stale-stamped vectors rather than compare them.
- **`lhassa memory reindex [--json]`**: re-embeds every fact and every
  cached answer-cache question with the CURRENT embedder
  (`embeddings.reindex_all`), best-effort per row. Run it after switching
  backend/model (e.g. turning on a local Ollama) so previously hash-stamped
  vectors become comparable again.

Known gap: `answer_cache` rows carry no per-row model stamp (unlike
`fact_embeddings`), so there is no live mismatch guard for it yet; `reindex`
brute-forces the whole table instead of skipping already-current rows.

---

## Answer-cache diagnostics; how close was a miss?

`memory/answer_cache.py:lookup()` takes an optional `stats` out-param; when
supplied it is filled with `{"best_similarity": float, "scanned": int, "hit":
bool}` on every call, hit or miss. The Telegram chat think uses this: a
`cacheable`-intent question that misses the cache logs its episode with
`cache-miss best=0.83 scanned=12` appended to the decision text; over days,
this tells whether the cache's 0-hit problem (if any) is the
`LHASSA_ANSWER_CACHE_THRESHOLD` (0.90) being too strict, low traffic, or the
embedder's quality; rather than guessing.

---

## Document perception (knowledge roots)

Beyond what Lhassa is told and what it has lived (episodes), it can also read
the owner's OWN documents (notes, papers, projects) so thinking, recall and
the knowledge graph are grounded in real material. This is deliberately **not**
"read the whole PC": like the calendar knob, the owner points Lhassa at a small
set of **knowledge roots**, and only those are ever touched.

- **Opt-in, empty by default.** `LHASSA_KNOWLEDGE_ROOTS` (CSV of directories,
  `~`-relative allowed) is empty until the owner sets it; nothing is indexed
  otherwise.
- **A hard ignore-list, non-negotiable.** Regardless of what a configured root
  contains, `lhassa/memory/knowledge.py` always skips: `.env`/`.env.*`,
  `*.key`/`*.pem`/`id_rsa*`/`*.p12`, anything with `secret`/`credential`/`token`
  in the name, `.git/`, `node_modules/`, `__pycache__/`, `.venv/`/`venv/`,
  `.cache/`, `dist/`, `build/`, `.ssh/`, and every dotfile/dotdir. This is not a
  default the owner widens; it is baked into `ingest()` itself.
- **What's indexed.** Only `.md`/`.txt`/`.rst`/`.org`/`.tex`/`.markdown` and
  `.pdf` (extracted with `pypdf` through `memory/extract.py`, the same seam the
  CV pack reads uploads with); code and everything else is out of scope; this is document
  perception, not a code indexer. A file over `LHASSA_KNOWLEDGE_MAX_FILE_KB`
  (default 512) is skipped.
- **Chunking + embedding.** Each file's text is split into
  `LHASSA_KNOWLEDGE_CHUNK_CHARS`-sized chunks (~500 tokens) and embedded with
  the same offline-capable `get_embedder()` layer 4 uses; $0, deterministic,
  no network by default.
- **Storage.** One row per chunk in the `knowledge` table (`lhassa/core/db/`)
 ; local to `~/.lhassa/brain.db`, scoped by `instance_id`, **never** the repo
  (see `doc/boundaries.md`: the index is private, gitignored territory).
- **Incremental.** `ingest()` tracks each indexed file's `mtime`: unchanged →
  skipped; changed → its old chunks are deleted and it's re-indexed; a brand
  new match under a root → indexed for the first time.
- **Search.** `knowledge.search(instance_id, query)` embeds the query and
  cosine-scans a bounded recent set of chunks; the same pattern as
  `memory/answer_cache.py`: returning the top matches with their source file
  and a score.
- **Grounds thinking.** `memory/load.py` adds a **`knowledge`** context layer at
  the LOWEST priority (below recall); fires only when a `query` is given,
  truncates first under budget, and is fully defensive (any failure adds
  nothing, never breaks the load). Matching chunks are attributed
  `[from your notes: <filename>]` so the brain's own answer can point back at
  its source.
- **Re-indexing.** The `synchronizer` monk (see `doc/architecture.md`: the
  temple's inner departments) owns `ingest_knowledge`, scheduled weekly by the
  `maintenance` seed through the same down-execute-up temple loop as
  `calendar_sync`; a clean $0 no-op with no roots configured.
- **Surfaces.** Telegram **`/knowledge <query>`** and
  **`lhassa knowledge ingest|search <q>|clear`** (CLI); both LLM-free.
  `clear` wipes the whole index (a re-ingest, or a privacy reset).

---

## The chica: working cache, LRU, and consolidation

The chica (`lhassa/memory/chica.py`) is a small, independent SQLite file that
mirrors the current wake's context; directives, pinned facts, active-tag
facts, and recent episodes; so it survives a process restart without
re-querying the grande on every touch.

- **Mechanical consolidation (the ~80% path, `$0`).** `chica.consolidate()`
  is a pure threshold-LRU eviction: once the cache exceeds
  `LHASSA_CHICA_MAX_ITEMS` (default 200), the least-recently-used
  **non-pinned, non-directiva** items are dropped down to the threshold.
  Pinned items and directives are never evicted; no tokens, no model call.
- **Semantic consolidation (the cheap-model second pass).**
  `lhassa/memory/consolidate.py:consolidate_semantic()` is threshold-driven,
  never clock-driven: a no-op unless `LHASSA_SEMANTIC_CONSOLIDATION` is on and
  the chica is over its threshold (or `force=True`). It asks the cheap,
  routine-seat model (`llm.router.pick_provider(hard=False)`: the same
  resolution every heartbeat think uses) to cluster genuinely-redundant items
  into one summary each, and separately to flag contradictory fact pairs.
  Only the chica is touched; the grande is untouched unless the caller opts
  into `record_provenance=True`, which appends the summary back as a
  low-confidence (`confianza=0.5`) `dato` fact.
- **Episode distillation has an independent spend switch.** Set
  `LHASSA_MEMORY_DISTILL_ENABLED=0` to stop `distill_episodes()` at the memory
  boundary before provider resolution. The guard applies equally to scheduled,
  CLI and future callers.
- **`regenerate()`**: "kill the chica, it regenerates from the grande": wipes
  the instance's cache and rebuilds it from `memory.load.load()`. The grande
  surviving the chica's destruction is the whole point of the split.
- **`distill_episodes()`** (also in `consolidate.py`) is the one function in
  the memory layer that spends tokens outside of consolidation: it asks the
  cheap model to extract up to 10 novel, durable facts from the last N hours
  of episodes and writes them as `dato` facts (`confianza=0.6`,
  `procedencia="distill:<date>"`, tagged `distilled`), skipping anything
  whose exact text already exists. It degrades to a logged no-op on
  `BudgetExceeded` / `ProviderNotConfigured`: it never crashes a heartbeat.
  Scheduled daily by the `maintenance` seed.

---

## The knowledge graph

`lhassa/memory/graph.py` is a **deterministic read-model** over the grande;
"what do I know about X?" answered from stored facts and episodes, at `$0`,
with **no model call**. It builds nothing new to store; it computes entities
and their co-occurrences on the fly from rows `load_facts`/
`search_episodes`/`load_recent_episodes` already return.

- **Entity extraction** (simple, honest limits): every fact **tag** is an
  entity (the strongest signal); a regex over fact `contenido` and episode
  text also pulls capitalized-multiword terms ("Ana Torres") and quoted
  terms. This is **not NLP**: no lemmatization, no coreference ("my sister"
  and "Ana" link only if a fact ties them together explicitly), and an
  isolated capitalized word can slip in as a false positive. It's tuned to be
  useful, not perfect.
- **`what_do_i_know(instance_id, query, limit=10)`**: the flagship
  primitive: returns the facts and episodes mentioning `query`, the
  co-occurring `related_terms` (other entities appearing alongside it in the
  same fact/episode), and the `first_seen`/`last_seen` time span. An empty or
  unmatched query degrades to an empty result; it never raises.
- **`entities(instance_id)`**: every distinct entity the brain tracks (tag-
  or text-derived), each with a mention count, `last_seen`, and its `source`
  (`"tag"` (the stronger signal) or `"text"`). A lone text-derived term
  seen only once is dropped as noise.
- **`timeline(instance_id, entity)`**: every fact and episode mentioning
  `entity`, oldest first: the story of one thing.
- **Reachable at:** `GET /api/memory/graph?q=<term>`, `GET
  /api/memory/entities` (`lhassa/api/memory_routes.py`), and `lhassa memory
  graph <query> [--json]` / `lhassa memory entities [--json]`
  (`lhassa/cli/memory_cmds.py`).

---

## The persisted knowledge graph ("LightRAG-lite")

Where `memory/graph.py` (above) recomputes co-occurrence at query time from
raw facts/episodes, `lhassa/memory/kg.py` accumulates the SAME extraction
**durably**, into two new grande tables (`kg_entities`/`kg_edges`), so 1-hop
expansion doesn't have to re-scan the whole grande on every retrieval. Off by
default; the whole feature is gated by one flag, `LHASSA_MEMORY_GRAPH`
(`config.memory_graph_enabled`).

- **Entities** carry a `kind`: `"tag"` (from a fact's own `tags`: the
  stronger signal, mirrors `memory/graph.py`'s own "once tagged, stays
  tagged" rule) or `"term"` (pulled from free text via
  `memory.graph.extract_terms`).
- **Edges** are a CLOSED vocabulary (`memory/kg.py:EDGE_KINDS`, mirroring
  `org/relationships.py`'s discipline): `co_occurs` (symmetric; two
  entities in the same fact/episode, weight accumulates per co-occurrence),
  `mentions` (directed, term → tag, same fact only; reserved for the one
  relationship that carries extra structure beyond plain co-occurrence),
  `derived_from` (reserved for future provenance edges; part of the closed
  set but not yet emitted by the feeder), and `asserts` (directed,
  subject → object; opt-in, only emitted by triple extraction, below).
- **`kg_sync`** (`memory/kg.py:sync`, fronted by the `kg_sync` action,
  `actionpack/_kg.py`) is the $0 incremental feeder: it walks facts +
  episodes SINCE the last sync (a `watch_state` marker, `kg_sync:last`: the
  same dedup pattern the watchlist senses use), upserts entities/edges, and
  advances the marker. Never calls a model, never raises; a disabled flag or
  any internal failure degrades to a zeroed summary. Owned by the `guardian`
  monk under the temple department, scheduled **weekly** by the
  `maintenance` seed alongside `review_tools`/`curate_skills`.

### Triple extraction; opt-in, `asserts` edges

`kg_sync`'s co-occurrence feeder is byte-for-byte unchanged by default. A
second, separate opt-in (`LHASSA_KG_TRIPLES` / `config.kg_triples_enabled`,
off by default) layers cheap-model **triple extraction** on top: batched over
NEW facts/episodes only (never a full re-scan), one completion per batch asks
the routine model for `subject / predicate / object` triples grounded in the
rows it was actually shown. A triple's `source` field must name one of the
sources actually offered in that prompt; anything else (a hallucinated
source the model invented) is dropped, never trusted, along with empty fields
and self-edges. Surviving triples are written as `asserts` edges (directed,
subject → object); a **fourth** edge kind alongside `co_occurs`/`mentions`/
`derived_from` (`memory/kg.py:EDGE_KINDS`). Because the edge schema carries no
predicate column, the predicate rides inside the `source` string by
convention: `fact:<id>|pred:<verb>` or `episode:<id>|pred:<verb>`: provenance
and relationship type in one field, by choice, rather than widening the
schema for one new column. Per-batch isolation means one bad batch never sinks
the others; any provider failure (budget, no provider) degrades to skipping
extraction for that run, never a crash.

### `kg_resolve`: merging duplicate entities, exact first

Two co-referring entities (`"budget"` and `"Budget"`, or a near-miss spelling)
fragment the graph unless something merges them. `kg.resolve` (the
`kg_resolve` action, owned by the `guardian` monk, run weekly) finds candidate
pairs deterministically; same `kind`, then either an **exact** match (case/
accent-normalized equality) or a **fuzzy** one (a shared token ≥3 chars, or a
`difflib` similarity ratio ≥0.6); capped at a small number of pairs per run.
The two tiers are handled with very different discipline:

- **Exact pairs merge with no model at all**: `db.merge_kg_entities()`
  repoints every edge from the duplicate onto the entity with the earlier
  `first_seen`, folding a colliding edge's weight rather than dropping it.
- **Fuzzy pairs only merge when `LHASSA_KG_TRIPLES` is on**, and even then
  only after one bounded, batched completion judges that specific pair
  genuinely the same entity; with the flag off, or the provider unavailable,
  fuzzy candidates are left alone rather than guessed at. Merging on a
  hallucinated "same" verdict would silently corrupt the graph, so a fuzzy
  merge is never attempted without an actual judgment backing it; an honest
  skip, not a downgraded merge.

`resolve()` never raises and reports `{merged, candidates, skipped}`.

### Cited retrieval; the `via` label names its provenance

`memory/graph_retrieve.py`'s fused retrieval (above) already tags each result
with a `via` field explaining why it was pulled in. With triple extraction on,
a hop-derived result's `via` string carries the SAME edge-kind + provenance
convention triples write: `"<seed> —asserts→ <object> (fact:<id>|pred:<verb>)"`;
so a citation for an asserted relationship names both the kind of edge
(`asserts`, not just `co_occurs`) and the exact fact/episode + predicate it
came from, not merely "found via the graph." The trailing `(<provenance>)` is
always the originating edge's own `source` column, verbatim.

### Fused retrieval; vector seed, 1-hop expand

`lhassa/memory/graph_retrieve.py:retrieve(instance_id, query)` layers the
persisted graph on top of the existing layer-4 semantic search, LightRAG-lite
style:

1. **Seed**: the layer-4 semantic hits (reusing `memory/load.py`'s own
   `_semantic_layer`, no second embedding index) plus a direct name match of
   the query's terms against stored `kg_entities`.
2. **Expand 1-hop**: `db.kg_neighbors` over the seed entity ids, heaviest
   edge first.
3. **Map back to facts**: each neighbor entity is resolved to the facts
   whose tags/content mention its name.
4. **Merge + rank**: semantic-seed facts first, then direct entity-name
   matches, then hop-derived facts ranked by the originating edge's weight
   (a stronger co-occurrence is a more reliable hop). Deduped by fact id,
   capped at `limit`.

Each result carries a `via` field naming WHY it was pulled in; `"semantic"`,
`"entity:<name>"`, or `"<seed> —<edge_kind>→ <neighbor>"`: so a caller can
always show its provenance. $0 beyond the one embedding call the semantic
seed already makes; any internal failure degrades to `[]`, never raises.

### The graph context layer

`memory/load.py` wires `graph_retrieve.retrieve` in as the **`graph`**
layer; the LOWEST priority in `PRIORITY` (dropped first under budget) and,
like the `knowledge` layer above it, fires only when both a `query` is given
**and** `config.memory_graph_enabled` is on. With the flag off (the default),
this layer is a byte-for-byte no-op; turning it on is strictly additive,
the same contract layer 4 and document perception both keep.

---

## Backups

`lhassa/memory/backup.py` mirrors the grande using SQLite's **online backup
API**: WAL-safe, a consistent snapshot taken while readers and writers keep
running, no manual checkpoint dance:

- **`backup(dest=…)`**: copies `brain.db` to `LHASSA_BACKUP_DIR`
  (default `~/.lhassa/backups`). Idempotent and cheap enough to run on every
  heartbeat; the mirror is simply overwritten.
- **`restore(src, dest=…)`**: rebuilds a database from a backup file, same
  online-copy mechanism, reversed. The source is verified before the destination
  is touched.
- **`verify(path)`**: opens a backup **read-only**, runs
  `PRAGMA integrity_check`, and confirms the expected tables
  (`episodes`, `facts`, `tasks`) exist. It also checks SQLite's schema version:
  unversioned (`user_version=0`) legacy backups remain restorable and are
  upgraded by the next `init_db()`, while backups created by a newer Lhassa are
  refused rather than downgraded. Returns `True`/`False`; never raises, so a
  corrupt, missing, or incompatible backup is a signal, not a crash.

---

## Inspecting memory: CLI, API, dashboard

- **`lhassa memory <query> [--json]`** (`lhassa/cli/commands.py`); searches
  the grande's facts (via `memory.facts.load_facts` + `resolve`) for a
  case-insensitive substring match against a fact's content or its tags, and
  prints a table of `id / tipo / contenido / tags / pinned / confianza`
  (or the raw JSON with `--json`).
- **`GET /api/memory/facts`**: every stored fact (`lhassa/api/memory_routes.py`).
- **`GET /api/memory/sessions`**: the 20 most recent legacy voice-session
  summaries (the `memory.db` lane, above), not the grande's episodes.
- **`GET /api/episodes/search?q=...`**: episodic recall (layer 5, above).
- **`lhassa memory graph <query>`** / **`GET /api/memory/graph?q=...`**;
  "what do I know about X?", and **`lhassa memory entities`** / **`GET
  /api/memory/entities`**: the entity map, both above.
- Telegram **`/recall <text>`**: the LLM-free recall command, above.
- The dashboard's Overview and Reports panels surface facts, recent episodes,
  and recall results without a dedicated "Memory" section of their own; the
  facts table and session history are the two panels reading the two
  respective stores.

---

## Privacy

Everything above lives on the owner's machine. The only points where memory
content leaves it are: a `think` that includes loaded context in its prompt
(sent to whichever provider the router picked; subscription CLI or metered
API), the optional `RemoteEmbedder` endpoint (local Ollama by default, cloud
only if explicitly configured), and `distill_episodes()` / semantic
consolidation, which route through the same provider path and are subject to
the same budget guard as any other think. No memory data is ever sent
anywhere as a side channel outside these.
