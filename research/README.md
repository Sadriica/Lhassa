# The research corpus

This folder is the KNOWLEDGE BASE of Lhassa's research line (declared in
[`../doc/research.md`](../doc/research.md)): one document per idea/technology
we study, taken **down to the base** — how it actually works, what evidence
its authors present (labeled as theirs), how it maps to what Lhassa really
built, and what WE have not yet verified with our own instruments.

Ideas do not enter the corpus merely because they are interesting. `/idea`
first produces an evidence-bound assessment and deterministic investment level;
only `applied` and `rigorous` ideas receive a full corpus. Archived, watched and
failed ideas remain queryable results with complete event logs.

Every document follows the same seven sections:

1. **The idea in one sentence**
2. **Mechanics — down to the base**
3. **Evidence the authors present** (their numbers, their benchmarks — never
   presented as ours)
4. **Sources** (primary + nested, all URLs; unavailable sources named as such)
5. **Mapping to Lhassa** (verified against code and `doc/decisions.md`, with
   honest divergences)
6. **Not yet verified by us — and the experiment that would** (the bridge to
   the comprobación method: LhassaBench/ledger/episodes as the instruments)
7. **Open questions**

## The corpus

| Document | Core idea | Primary source |
|---|---|---|
| [loop-engineering.md](loop-engineering.md) | The unit of work is a bounded, verifier-terminated loop; the doer never judges | Anthropic loop-engineering material (via the 2.7M-view thread) |
| [graph-engineering.md](graph-engineering.md) | A knowledge graph as the multi-agent memory layer: extract→resolve→assemble→query, provenance per edge | The 12-page "Anthropic Playbook" PDF (independent synthesis of Anthropic's public cookbook) |
| [retrieval-as-code.md](retrieval-as-code.md) | Deterministic scoring + a curated index BEFORE any model read — 3 files, not 5,000 | The "second brain" thread + KG/RAG complementarity |
| [memory-sovereignty.md](memory-sovereignty.md) | Agent memory as a first-class, owner-owned, curated asset — served warm to every harness | Memanto + jcode's verified-injection memory graph |
| [mixture-of-agents.md](mixture-of-agents.md) | A panel of independent models + an aggregator beats model luck on expensive-to-be-wrong calls | Hermes /moa + Wang et al. 2024 |
| [token-efficient-serialization.md](token-efficient-serialization.md) | Serialize tabular data for prompts in a token-compact form — a translation layer, not a storage format | TOON spec/repo |
| [eval-driven-agents.md](eval-driven-agents.md) | Evals before production; bounded autonomy, budgets and durable execution as infrastructure, not afterthought | ai-engineering-from-scratch (503 lessons) |
| [output-quality.md](output-quality.md) | A versioned skill as a quality gate over generated text; slop is enumerable and removable | no-ai-slop (skill + eval rubric) |

## Provenance notes (read before trusting a tweet)

- The graph-engineering PDF **self-declares as an independent synthesis** of
  Anthropic's public material — the threads that circulated it over-attribute
  it to "a senior Anthropic engineer" and (one of them) to Andrej Karpathy.
  The corpus documents the chain and works from the PDF itself.
- Two nested X *articles* (the "14 steps" and the "12-step second brain")
  are login-walled and were NOT captured; where cited, the corpus works from
  the threads' own summaries and says so.
- Vendor benchmark numbers (Memanto, jcode, HermesBench, TOON) are reported
  as THEIR claims; section 6 of each document names the experiment that would
  verify the relevant claim on OUR instruments.

## How this corpus is used

It is the standing context for building: before adopting or extending any of
these techniques in Lhassa, read the document, check section 5 for what
already exists, and section 6 for what must be measured first. When an
experiment from section 6 runs, its results feed a technical note
(`lhassa research draft <topic>` → `doc/notes/drafts/`).

New material enters through `/idea <text|link>`. Before any model writes,
Lhassa creates a versioned `idea-<slug>-<digest>.evidence.json` custody bundle
with `S1…Sn` captures (URL, redirect destination, timestamp, status, SHA-256,
bounded visible text, nested-source provenance). The four aides receive that
same bundle cumulatively. A draft is recorded only after the LLM-free
`research_corpus` gate checks all seven sections, declared/cited source ids,
evidence bullets, and a concrete experiment design. Passing that gate means
“method-complete draft,” never “externally proven result.”
