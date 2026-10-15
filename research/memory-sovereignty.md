# Memory sovereignty; the agent's memory as the owner's asset, not the vendor's

> Draft research note; not yet reviewed by the owner. Part of Lhassa's research
> line (`doc/research.md`): "Memory architectures on humble hardware."

## The idea in one sentence

An AI agent's memory should be a first-class, owner-visible, owner-editable,
exportable asset that lives independently of any single vendor's chat
history; not an opaque blob the provider keeps and the user can neither
inspect nor take with them [S6].

## Mechanics - down to the base

**Memanto** [S6] frames the problem as six concrete gaps in how agent memory
is normally handled, per the project's own documentation: memories arrive as
"static injection" (immutable blobs, not queryable entities); there is no
temporal decay (an old preference weighs the same as yesterday's deadline);
provenance is missing (an inferred pattern and an explicit statement blur
together); the architecture is flat (episodic, semantic, and procedural
memory collapse into one undifferentiated layer); contradictions coexist
silently with no detection; and indexing is a bottleneck (mandatory LLM
extraction delays when a memory becomes searchable). Its answer is to make
memory management itself an agent with three primitives; `remember` (store
with type, confidence, provenance), `recall` (retrieve by relevance with
temporal filtering, e.g. `--as-of`, `--changed-since`), and `answer` (respond
directly from memory context, LLM-grounded). Thirteen typed memory categories
(instruction, fact, decision, goal, preference, relationship, context, event,
learning, observation, artifact, error) give the "curates what's worth
keeping per session" claim concrete shape: curation is typing plus temporal
versioning plus a `conflicts` command that surfaces detected contradictions
rather than silently overwriting, plus a daily-summary workflow that produces
a session recap with relevance scoring. Export/no-lock-in is a real command
surface, not a slogan: `memanto memory export` (structured markdown),
`memanto memory sync` (a portable "Open Knowledge Format" bundle), and
`memanto migrate` (importing FROM Mem0, Letta, or Supermemory into Memanto's
own store). It integrates with ten-plus surfaces (Claude Code, Cursor,
Windsurf, Codex, CrewAI, LangGraph, GitHub Copilot, Gemini CLI, OpenCode,
Goose) via a one-command `memanto connect <tool-id>`, and can run fully local
(Docker + Ollama, no API key) or as a hosted free tier.

**jcode** [S8] is a from-scratch coding-agent harness (Rust, MIT, 11.2k
stars) whose memory design is a specific, running answer to "how does an
agent recall relevant context without a human typing `/remember`": every
turn is embedded as a semantic vector; that embedding queries a persistent
graph of prior memories via cosine similarity on every single turn, not on
demand. The distinguishing move for this note is the verification step: hits
are either fed straight into the conversation or, optionally, routed through
a **memory sideagent that verifies the memories are actually relevant** (and
can do further retrieval work) before anything is injected into context;
the project's own framing is that this makes memory "human-like": recalled
automatically, without the primary agent burning tokens on explicit memory
tool calls. Memory capture itself is asynchronous and threshold-driven, not
per-turn: a memory sideagent extracts new memories into the graph on
triggers named as "semantic drift, K turns since last extraction, session
end, etc.," and a separate background "ambient mode" periodically
"reorganizes, checks for staleness and conflicts" across the whole graph;
i.e., consolidation is a standing process, not a one-time index build. jcode
also exposes explicit memory tools for the agent to use deliberately,
alongside the passive background path; the two are not exclusive.

**The memory ladder** [S2] gives a five-level taxonomy for how much an agent
actually DOES with a documented failure, independent of storage mechanics:
(1) fail and write it down, (2) investigate why, (3) verify the diagnosed
cause into a fact, (4) distill the fact into a general reusable rule, (5)
consult the rule instead of re-deriving it from scratch. The thread's
benchmark claim (Continual Learning Bench, sequential SQL questions across
fresh sessions sharing one memory file) reports three models topping out at
different rungs: one model stays at level 1 (notes things but never rereads
them, score 0.364), another reaches level 3 (verifies roughly 17% of its
diagnoses, score 0.700), and a third is reported completing the full cycle
(verifies 73% of diagnoses into reusable rules, score 0.839); the thread's
own conclusion is "the model forgets between sessions. The memory file does
not." [S2]

**Multi-layer memory as a named architecture pattern** [S11]: a curriculum
phase on agent memory systems teaches "virtual context, memory paging
(MemGPT-style), hybrid vector+graph+KV" as the production-grade shape;
i.e., no single storage primitive (a vector store alone, a graph alone) is
treated as sufficient; the pattern is specifically to combine a fast
approximate layer (vector), a structural/relational layer (graph), a
low-latency key-value layer, and durable/paged storage for context that
doesn't fit in-window, with MemGPT's paging metaphor cited as the reference
design for swapping memory in and out of the active context window the way
an OS pages virtual memory.

## Evidence the authors present

- Memanto [S6] cites two named benchmarks directly on its own repository:
  89.8% on LongMemEval and 87.1% on LoCoMo, claiming it outperforms Mem0, Zep,
  and Letta on both. No methodology, run count, or independent reproduction
  is available from what was fetched; these are the project's own reported
  numbers, not third-party verified.
- jcode [S8] reports concrete resource numbers: boot to first frame in 14ms
  vs. 3,436ms for Claude Code (245x); one session at 27.8MB RAM vs. 386.6MB
  for a comparator; ten parallel sessions at 117-260MB vs. 833MB+ for others.
  These are the project's own benchmark claims; the exact comparators and
  measurement conditions were not independently re-verified for this note.
- The memory-ladder benchmark [S2] (Continual Learning Bench scores 0.839 /
  0.700 / 0.364) is reported only as a thread summary with no link to a
  published methodology, dataset, or reproducible harness that was captured.
- The multi-layer memory pattern [S11] is presented as curriculum content
  (a teaching framework built "from scratch, no frameworks" per lesson, then
  reimplemented with a framework) rather than as a benchmarked result; its
  evidentiary weight here is that it names a converged-upon architecture
  across many practitioners' lesson plans, not that it measures anything
  itself.
- None of these four sources meets the bar Lhassa's own research method sets
  (`doc/research.md`: "every public claim cites an instrument... numbers come
  from the ledger, `bench_runs`, or the episode log (with provenance) or
  they are not made"). They are documented here as raw material, not as
  verified results.

## Sources

- [S6] angeldot_, X post on Memanto, https://x.com/angeldot_/status/2082123282921488890
  (Jul 28, 2026). Repository directly fetched: https://github.com/moorcheh-ai/memanto
  (MIT license, `pip install memanto`).
- [S8] thisguyknowsai (quoted by RoundtableSpace), X post on jcode,
  https://x.com/thisguyknowsai/status/2081680037544427793 (Jul 27, 2026, 195K
  views). Repository directly fetched: https://github.com/1jehuang/jcode
  (11.2k stars, Rust, MIT license); memory-system section specifically.
- [S2] angeldot_, "Cómo crear Loops en Fable 5" thread,
  https://x.com/angeldot_/status/2065145486890164524 (Jun 11, 2026, 2.7M
  views). Used here only for the five-level memory-use ladder and the
  Continual Learning Bench numbers; the thread's broader loop-engineering
  content is out of scope for this note.
- [S11] rohitg00, `ai-engineering-from-scratch`,
  https://github.com/rohitg00/ai-engineering-from-scratch (503 lessons / 20
  phases, MIT); Phase 14 memory-systems lesson index (read from the sources
  pack summary, not independently re-fetched for this note).

## Mapping to Lhassa

Lhassa is, structurally, already an implementation of the memory-sovereignty
thesis [S6] argues FOR; not a system that needs it bolted on:

- **Lhassa owns its own store.** The grande is a local, append-only SQLite
  database (`lhassa/core/db.py`), not a provider-held chat history. Backup,
  restore, and integrity verification are first-class, WAL-safe operations
  (`lhassa/memory/backup.py:backup/restore/verify`); `verify()` migrates a
  disposable copy to the current schema and runs `PRAGMA integrity_check`
  plus a table-presence check before ever trusting a backup
  (`lhassa/memory/backup.py:140-148`). This is the sovereignty half of
  Memanto's pitch (own it, verify you can get it back) already built as
  running code, not a roadmap item.
- **`/config` and the settings service give the owner visibility Memanto
  sells as a differentiator** ("nothing hidden": inspect/edit everything):
  `settings.py`'s `effective()` read model shows every editable knob's value,
  default, whether it's overridden, and a restart hint, with secrets masked
  (per the architecture doc's own description). This is not memory-content
  inspection specifically, but it is the same transparency posture applied to
  the system that decides what memory gets used.
- **`[[remember: …]]` is Lhassa's owner-facing curation primitive**: a
  chat-side marker that persists ONE durable fact to the grande per nudge
  interval, substring-deduped, procedencia-tagged `telegram-chat`
  (documented in `doc/architecture.md`'s Notify section). This is a narrower analog of
  Memanto's `remember` primitive: Lhassa's version is single-fact,
  cadence-gated, and $0 (no model call needed to decide WHETHER to persist;
  the marker itself is the decision), where Memanto's is a general-purpose
  agent call.
- **`distill_memory` (`lhassa/actionpack/_memory.py:103-135`, registered in
  `actionpack/__init__.py:85`) and `apply_feedback`
  (`lhassa/actionpack/_feedback.py:13-54`, registered at
  `actionpack/__init__.py:114`) are Lhassa's curation agents**, in the sense
  Memanto uses the word: `distill_memory` runs `memory/consolidate.py`'s
  semantic pass; a CHEAP model call that summarizes clusters of similar
  evictable chica items into one compact item and flags contradictions
  (`detect_contradictions`, per `memory/consolidate.py:1-28`); directly
  mirroring Memanto's stated gap #5 ("silent contradictions... coexist
  without detection") and its `conflicts` command, except Lhassa's version is
  threshold-triggered (chica fills up) rather than always-on, and the grande
  stays the append-only source of truth so nothing is ever actually deleted
  by the consolidation pass. `apply_feedback` closes a different loop
  Memanto's docs don't name at all: it reads `core/feedback.py`'s inferred
  landing signal and feeds it into the autonomy streak knob
  (`security/autonomy.py`), which is closer to procedural memory (what
  should I do differently) than declarative memory (what do I know).
- **Context packs (`lhassa/harness/context_pack.py`) are "agents start warm"
  turned inside-out.** Memanto's pitch is that ITS agent hands warm context
  to whatever harness it's plugged into; Lhassa's version is that LHASSA
  generates the warm-context files (`VISION.md` / `ARCHITECTURE.md` /
  `RULES.md` / `MEMORY.md`, `context_pack.py:42`) and hands them to a rented
  harness (claude/codex/opencode) at launch; the same "VISION/ARCHITECTURE/
  RULES/MEMORY" convention S2's loop-engineering thread names directly for
  its own SKILLS block, independently converged upon here. `ensure()`'s
  digest-based staleness check (`_compute_digest`, `context_pack.py:168-174`,
  derived from project `updated_at` + newest fact timestamp + newest run
  `started_at`) is the same "detect when the source material changed, skip
  regenerating otherwise" discipline as the knowledge index's mtime+size
  check documented in the companion note (`research/retrieval-as-code.md`);
  a second, independently-built instance of the same staleness-guard
  pattern, this time over generated files instead of an ingested index.
- **What Lhassa visibly lacks against jcode's [S8] specific move.** jcode's
  differentiator over a plain vector-recall loop is the **verification
  sideagent that checks a memory hit's relevance before it is ever injected
  into context**: an explicit, separate judgment step between retrieval and
  use. Lhassa's closest existing vocabulary for this distinction is
  `memory/evidence.py`'s three-rung claim ladder (`prepared` / `observed` /
  `verified`, `memory/evidence.py:1-35`), which is a PURE, no-I/O function
  computed off `status`/`estado` fields on runs/tasks; it classifies
  evidence about WORK OUTCOMES (was this run's result independently
  corroborated by a passing verify result or an approved approval,
  `evidence.py:149-176`), not memory RECALL HITS. There is currently no
  Lhassa seam that asks "is this retrieved fact/episode/knowledge-chunk
  actually relevant to THIS query" as a separate, independent check before
  injection into `Context.to_prompt()`: `memory/load.py`'s layers rank by
  similarity/priority but do not verify a hit's relevance the way jcode's
  sideagent does. `evidence.py`'s vocabulary is the natural place such a
  check would report through if built, but the check itself does not exist
  today.
- **Cross-harness import (Memanto's `migrate` importing from Mem0/Letta/
  Supermemory) has no Lhassa equivalent**: Lhassa's memory is populated by
  its own facts/episodes/knowledge pipelines, not designed to ingest another
  agent framework's memory export. This is a real gap against [S6]'s
  no-lock-in pitch, in the opposite direction from what Lhassa already does
  well (exporting/backing up its OWN memory): Lhassa has never needed to
  import someone else's.
- **Per-turn automatic capture (jcode's always-on embedding of every turn)
  has no Lhassa equivalent either.** Lhassa's capture is either explicit
  (`[[remember: …]]`, `lhassa memory add`, `lhassa onboard`) or
  action-triggered (`distill_memory` on a threshold/schedule); there is no
  standing process that embeds and considers storing something from every
  single turn the way jcode's memory sideagent does on its own cadence
  ("semantic drift, K turns since last extraction, session end").

## Not yet verified by us - and the experiment that would

- **Whether Lhassa's `distill_memory` actually catches the contradiction
  cases Memanto's `conflicts` command is built for.** The experiment: seed
  the grande with two facts that assert incompatible values for the same
  slot (e.g., two different stated preferences, timestamped apart), force a
  `distill_memory` run, and confirm `detect_contradictions` flags the pair;
  this is directly testable today with existing code
  (`memory/consolidate.py`) and would turn a mapping claim into a measured
  one per `doc/research.md`'s method.
- **Whether adding a jcode-style relevance-verification step before
  `Context.to_prompt()` changes anything measurable.** The experiment: for a
  sample of queries, compare the current `memory/load.py` output (ranked by
  priority/cosine only) against a variant that runs each candidate item
  through a cheap-model relevance check before inclusion, and measure both
  token cost and (if a labeled query set exists) whether the included
  items are judged more relevant. This would require building the check
  first; today it is a design gap, not yet an experiment.
- **The Memanto and jcode benchmark numbers themselves** (LongMemEval/LoCoMo
  scores; the 245x boot-time and RAM figures) were read from each project's
  own repository documentation, not reproduced. They should be treated as
  vendor-reported until independently rerun.

## Open questions

- Should Lhassa's `evidence.py` ladder (`prepared`/`observed`/`verified`) be
  extended to cover retrieval hits specifically, given it already exists as
  the right vocabulary for "was this corroborated by something independent"
 ; or is a retrieval-relevance check different enough in kind (relevance vs.
  outcome-truth) that it deserves its own module?
- Is per-turn automatic memory capture (jcode's model) actually desirable
  for Lhassa's "$0-first, thinking is the exception" posture, or is
  threshold/marker-triggered capture (Lhassa's current model) the more
  disciplined choice on purpose; i.e., is this a gap or a deliberate
  design difference?
- What would cross-harness memory import actually be FOR in Lhassa's case,
  given Lhassa is meant to be the durable brain and harnesses the
  interchangeable rented body (`doc/concepts.md`'s "build the brain, rent the
  body"); is importing another framework's memory export even a coherent
  ask inside that model, or is it solving a problem Lhassa's architecture
  doesn't have?
- Memanto reports zero-indexing-delay via an "information-theoretic"
  retrieval mechanism instead of a vector database; is there a legible,
  humble-hardware-compatible version of that idea worth a measured trial
  against Lhassa's current `HashingEmbedder`/cosine approach, or is this a
  vendor claim without enough public detail to even design an experiment
  around yet?
</content>
