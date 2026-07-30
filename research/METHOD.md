# The research method — how Lhassa writes, drafts, builds and focuses

This is the GESHE's charter — the scholar-monk of the temple owns this method
and carries it as its `research-method` skill, so every research think applies
it. The line it serves is declared in [`../doc/research.md`](../doc/research.md);
the corpus it feeds is [this folder](README.md).

## How we FOCUS (what deserves research at all)

1. **Born from real work.** A topic enters the line because the instruments
   surfaced it (a bench result, a ledger pattern, a repeated failure, a corpus
   doc's section 6) — never because it is fashionable. The anti-example is
   named in the corpus provenance notes: ideas that arrive over-attributed
   and unmeasured.
2. **The three limits filter.** A topic that needs a big GPU, that steals the
   owner's attention, or that erodes the person's sovereignty is out of scope
   by house design — not postponed, out.
3. **Comprobación before construcción.** If a technique is a candidate for
   adoption, the FIRST artifact is the experiment design (what will be
   measured, on which instrument, against which baseline) — not the
   implementation.
4. **One open front per theme.** A theme (loops, graph, memory…) carries at
   most one active experiment at a time; depth beats breadth.

## How we BUILD (the shape of an investigation)

Every investigation walks the same path, and each step leaves an artifact:

| Step | Artifact | Where |
|---|---|---|
| 1. Capture the sources, down to the base | corpus document (7 fixed sections) | `research/<topic>.md` |
| 2. Design the comprobación | the experiment in section 6 of the corpus doc, made concrete (baseline, instrument, metric, stop rule) | same doc, updated |
| 3. Run it on OUR instruments | bench_runs / ledger / episodes rows | the grande |
| 4. Draft the note from evidence | evidence-cited draft | `doc/notes/drafts/` via `lhassa research draft` |
| 5. Owner review → publish | the public note, bilingual | the site (owner's act) |

A step may end the investigation honestly ("the data does not support
adoption") — a negative result is a result and may still become a note.

## How we WRITE (the voice of a research artifact)

- **Claim → cited evidence.** Every number names its instrument or source.
  A claim the pack/corpus cannot support is written as "what's missing", not
  invented. The first live draft set the standard: *"the mechanism is sound
  and the record is absent."*
- **Theirs vs ours, always labeled.** Vendor and paper numbers are "evidence
  the authors present"; only instrument data is "ours".
- **Calm, no slop.** The house brand (calmado, sin hype) + the no-ai-slop
  discipline: no binary contrasts, no throat-clearing, no fake-profound
  endings, no banned filler (delve/leverage/robust/…). The `no-ai-slop`
  skill is installed and is the mechanical check.
- **Plain structure.** Thesis first, evidence next, unknowns named, next
  measurement last. 400–700 words for a note; a corpus doc follows its 7
  fixed sections.
- **Bilingual on publication** (Spanish + English) — the corpus and drafts
  stay English; the published note ships in both.
- **One mythology touch max** (the narrative rule) — and in research, usually
  zero.

## Templates (per artifact type)

**Technical note** (`doc/notes/drafts/`, then published):
```
# <Title — the claim, plainly>
Thesis (one paragraph).
The evidence (each number cites its instrument/source inline).
What we do not yet know.
The next measurement worth taking.
[appendix: the evidence pack, verbatim]
```

**Corpus document** (`research/<topic>.md`): the seven fixed sections — see
[README.md](README.md).

**Experiment log** (inside the corpus doc's section 6 when an experiment
runs):
```
### Experiment: <name> (<date>)
Baseline: <what we compare against>  ·  Instrument: <bench/ledger/episodes>
Method: <exactly what ran, reproducible on humble hardware>
Result: <numbers, with rows/ids>  ·  Verdict: <adopt / reject / inconclusive>
```

## Who does what

- **The geshe** owns this method, the `draft_research_note` action, and the
  focus decisions it can make alone (which section-6 experiment is next).
- **The owner** decides publication — a draft NEVER ships without review
  (the draft header says so), and adoption decisions stay with the human.
- **The scribe** keeps the repo docs in sync AFTER an adoption; the guardian
  runs the instruments' hygiene; the oracle feeds repeated-cost signals into
  the focus queue.

## The standing rule

The method is itself subject to the method: when a step repeatedly fails or
produces slop, that is an instrument signal — the geshe revises this file and
the revision is logged in `doc/decisions.md` like any other decision.
