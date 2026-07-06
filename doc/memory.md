# Memory

Lhassa's memory is not a single database — it is a small system of layers, each
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
| **brain.db** (the grande) | `~/.lhassa/brain.db` | `LHASSA_BRAIN_DB_PATH` | Append-only source of truth: `episodes`, `facts`, `tasks`, `tools`, `approvals`, and more — see `lhassa/core/db.py`. |
| **chica.db** (the chica) | `~/.lhassa/chica.db` | `LHASSA_CHICA_DB_PATH` | Derived working cache, rebuilt from the grande — `lhassa/memory/chica.py`. |
| **memory.db** (legacy) | `~/.lhassa/memory.db` | — | The original voice-session store (`facts` / `sessions` / `scheduled_tasks`) from before the grande/chica architecture existed — `lhassa/memory/store.py`. Still wired to the voice pipeline's system-prompt injection (`voice/pipeline.py`) and to shutdown fact/summary extraction (`memory/session.py`), and still backs the scheduler's task table and the dashboard's Session History panel. It predates, and does not talk to, the grande/chica/fact model described below — treat it as a separate, older lane that happens to still be live, not as part of the current fact/context design. |

Both the grande and the chica open in WAL mode and scope every row by
`instance_id` — one brain's memory never leaks into another's, from day one.

---

## The fact model (the grande)

Facts are the durable, typed knowledge the grande carries. The schema lives in
`lhassa/core/db.py` (`CREATE TABLE facts …`) and the typed API sits in
`lhassa/memory/facts.py`.

Every fact row carries:

- **`tipo`** — `dato` (a fact about the world/owner: "prefers dark mode") or
  `directiva` (a standing instruction: "answer concisely by default"). A
  `directiva` changes *how* Lhassa behaves and is always loaded into context;
  a `dato` changes *what* it knows.
- **`pinned`** — identity-grade facts that are never evicted or truncated.
- **`tags`** — free-form labels used for exact-membership context activation
  (not semantic — see layer 2 below).
- **`procedencia`** — where the fact came from (`distill:2026-07-05`,
  `consolidate_semantic`, a manual add, …) — provenance, not proof.
- **`confianza`** — a confidence score (default `1.0`); lower for
  machine-inferred facts (e.g. `distill_episodes` writes at `0.6`).

**Facts are append-only.** `add_fact()` always inserts a new row; nothing is
ever destructively rewritten. A fact superseding an older one is written as a
fresh row with a newer `actualizado_en` and, typically, a different
`confianza`. Reading back one "current" view of the world is the job of
`resolve()` in `lhassa/memory/facts.py`: it groups facts by a normalized
content key and keeps the winner ranked by `(actualizado_en, confianza)` —
**recency leads, confidence breaks ties.** Order is deterministic
(first-appearance of each conflict key), so the same facts always resolve the
same way.

On every `add_fact()` call the fact is also embedded (best-effort — an
embedder failure never blocks the write) into `fact_embeddings`, the table
that backs semantic retrieval (layer 4, below).

---

## How context is loaded at think time

`lhassa/memory/load.py` builds the **chica context** for one think — a
`Context` object with a token budget, assembled from five layers in strict
priority order (`PRIORITY = ["pinned", "recent", "tags", "semantic",
"recall"]`):

1. **Pinned + directivas (identity — always).** All `pinned` facts and every
   `directiva` fact, folded together with the persona's own directives
   (`persona.load_identity`). This is the mandatory core: `truncate()` never
   drops it, even if it alone exceeds the budget.
2. **Recency.** The most recent episodes (`core.db.load_recent_episodes`) —
   continuity from one wake to the next.
3. **Active tags.** Facts whose tags intersect the caller's `active_tags` —
   exact membership, not semantics.
4. **Semantic retrieval (layer 4).** Facts whose stored embedding is closest
   (cosine similarity) to the active `query`. Off by default; fires only when
   `LHASSA_MEMORY_LAYER4` is set **and** a `query` string is supplied **and**
   facts have been indexed (`memory.load.index_facts` / `reindex`). Vectors
   are only ever compared if they were produced by the same embedder model —
   mixed-model comparisons are skipped, not guessed at.
5. **Episodic recall (layer 5).** The top few past episodes whose text
   matches the active `query`, via the FTS5 index over `episodes` (see
   below) — `$0`, no model call, fully defensive (any failure degrades to an
   empty layer, never raises).

`truncate()` then trims the assembled context to the requested
`token_budget`, dropping items from the **lowest-priority layer first**:
recall goes before semantic, semantic before tags, tags before recency, and
the identity core never goes. Token counts are a deterministic estimate —
exact via `tiktoken` when the optional dependency is installed, else a
~3-chars/token heuristic (`estimate_tokens`) — so budgeting never depends on
a network call.

The default behavior (layer 4 and `LHASSA_MEMORY_LAYER4` unset) is
byte-for-byte the pinned/recent/tags load that existed before semantic
retrieval was added — turning the knob on is strictly additive.

---

## Episodic recall (layer 5)

Beyond facts, Lhassa keeps a full-text index over its own append-only episode
log — every decision it has ever logged — so it can answer "what happened
last time…" at `$0`, no model call, BM25-ranked.

- **Storage:** `episodes_fts`, an FTS5 external-content table over `episodes`
  (`lhassa/core/db.py`), kept in sync by insert/update/delete triggers and
  backfilled once on first creation. Some SQLite builds ship without FTS5;
  `init_db()` catches that case and disables recall rather than failing to
  start.
- **Search:** `core.db.search_episodes(instance_id, query, n)` sanitizes the
  query into a safe FTS5 `MATCH` expression (each token individually quoted,
  so stray `AND`/`OR`/parentheses/quotes can never raise a syntax error),
  ranks by `bm25()`, and demotes (never hides) automation-originated episodes
  (`despertar_por == "monitor"` or `task:*`) so interactive history surfaces
  first.
- **Reachable directly:**
  - Telegram **`/recall <text>`** (`lhassa/notify/commands.py`) — returns the
    top 5 matches, LLM-free.
  - **`GET /api/episodes/search?q=...&n=...`** (`lhassa/api/reports_routes.py`)
    — `n` capped `1..200`, returns `{query, count, results}`.
- It is the lowest-priority context layer (item 5 above): a think only spends
  the tokens on it when a `query` is actually supplied, and it is the first
  thing to fall off the budget.

---

## The chica: working cache, LRU, and consolidation

The chica (`lhassa/memory/chica.py`) is a small, independent SQLite file that
mirrors the current wake's context — directives, pinned facts, active-tag
facts, and recent episodes — so it survives a process restart without
re-querying the grande on every touch.

- **Mechanical consolidation (the ~80% path, `$0`).** `chica.consolidate()`
  is a pure threshold-LRU eviction: once the cache exceeds
  `LHASSA_CHICA_MAX_ITEMS` (default 200), the least-recently-used
  **non-pinned, non-directiva** items are dropped down to the threshold.
  Pinned items and directives are never evicted — no tokens, no model call.
- **Semantic consolidation (the cheap-model second pass).**
  `lhassa/memory/consolidate.py:consolidate_semantic()` is threshold-driven,
  never clock-driven: a no-op unless `LHASSA_SEMANTIC_CONSOLIDATION` is on and
  the chica is over its threshold (or `force=True`). It asks the cheap,
  routine-seat model (`llm.router.pick_provider(hard=False)` — the same
  resolution every heartbeat think uses) to cluster genuinely-redundant items
  into one summary each, and separately to flag contradictory fact pairs.
  Only the chica is touched; the grande is untouched unless the caller opts
  into `record_provenance=True`, which appends the summary back as a
  low-confidence (`confianza=0.5`) `dato` fact.
- **`regenerate()`** — "kill the chica, it regenerates from the grande": wipes
  the instance's cache and rebuilds it from `memory.load.load()`. The grande
  surviving the chica's destruction is the whole point of the split.
- **`distill_episodes()`** (also in `consolidate.py`) is the one function in
  the memory layer that spends tokens outside of consolidation: it asks the
  cheap model to extract up to 10 novel, durable facts from the last N hours
  of episodes and writes them as `dato` facts (`confianza=0.6`,
  `procedencia="distill:<date>"`, tagged `distilled`), skipping anything
  whose exact text already exists. It degrades to a logged no-op on
  `BudgetExceeded` / `ProviderNotConfigured` — it never crashes a heartbeat.
  Scheduled daily by the `maintenance` seed.

---

## Backups

`lhassa/memory/backup.py` mirrors the grande using SQLite's **online backup
API** — WAL-safe, a consistent snapshot taken while readers and writers keep
running, no manual checkpoint dance:

- **`backup(dest=…)`** — copies `brain.db` to `LHASSA_BACKUP_DIR`
  (default `~/.lhassa/backups`). Idempotent and cheap enough to run on every
  heartbeat; the mirror is simply overwritten.
- **`restore(src, dest=…)`** — rebuilds a database from a backup file, same
  online-copy mechanism, reversed.
- **`verify(path)`** — opens a backup **read-only**, runs
  `PRAGMA integrity_check`, and confirms the expected tables
  (`episodes`, `facts`, `tasks`) exist. Returns `True`/`False`; never raises,
  so a corrupt or missing backup is a signal, not a crash.

---

## Inspecting memory: CLI, API, dashboard

- **`lhassa memory <query> [--json]`** (`lhassa/cli/commands.py`) — searches
  the grande's facts (via `memory.facts.load_facts` + `resolve`) for a
  case-insensitive substring match against a fact's content or its tags, and
  prints a table of `id / tipo / contenido / tags / pinned / confianza`
  (or the raw JSON with `--json`).
- **`GET /api/memory/facts`** — every stored fact (`lhassa/api/memory_routes.py`).
- **`GET /api/memory/sessions`** — the 20 most recent legacy voice-session
  summaries (the `memory.db` lane, above), not the grande's episodes.
- **`GET /api/episodes/search?q=...`** — episodic recall (layer 5, above).
- Telegram **`/recall <text>`** — the LLM-free recall command, above.
- The dashboard's Overview and Reports panels surface facts, recent episodes,
  and recall results without a dedicated "Memory" section of their own; the
  facts table and session history are the two panels reading the two
  respective stores.

---

## Privacy

Everything above lives on the owner's machine. The only points where memory
content leaves it are: a `think` that includes loaded context in its prompt
(sent to whichever provider the router picked — subscription CLI or metered
API), the optional `RemoteEmbedder` endpoint (local Ollama by default, cloud
only if explicitly configured), and `distill_episodes()` / semantic
consolidation, which route through the same provider path and are subject to
the same budget guard as any other think. No memory data is ever sent
anywhere as a side channel outside these.
