# TOON; a tabular serialization format built to shrink the token cost of bulk data in a prompt

> Draft research note; not yet reviewed by the owner. Part of Lhassa's
> research line (`doc/research.md`): "$0-first routing and the post-token-
> bubble economy."

## The idea in one sentence

TOON (Token-Oriented Object Notation) encodes the same data JSON encodes, but
for uniform arrays of flat objects it declares the field list once in a
header line and streams one CSV-style row per object after it instead of
repeating every key name on every element; the format's own site reports
this cuts token count 30-60% on that kind of data with accuracy at least as
good as JSON on a retrieval benchmark, while explicitly not helping (and
sometimes losing to plain JSON) on non-tabular or deeply nested data
[toon-repo, toonformat.dev].

## Mechanics - down to the base

**Grammar.** Four rendering forms, chosen by payload shape, not per-call
[toon-repo]: **inline form** for a flat scalar array (`alerts[2]:
frost,wind`); **tabular form** for a uniform array of flat objects; the
form producing the headline savings, a header naming the array, its length,
and the field list once (`forecast[3]{day,condition,rainChance}:`), then one
indented CSV-style row per object; **keyed tabular form** for an object whose
values are themselves uniform objects, same amortization one level up; and
**list form** as the fallback for mixed-type or non-uniform arrays. The
shared mechanism: JSON's biggest structural cost in bulk data; repeating
`"field_name":` on every one of N objects; is paid exactly once, in the
header, instead of N times [toon-repo].

**Version history.** The spec has moved past v3 to v4.1 (2026-07-26) by the
time of this note. v3.0's breaking change governed nested tabular arrays
inside list items: "Encoders MUST emit `- key[N]{fields}:` on the hyphen
line. Tabular rows MUST appear at depth +2": "pre-v3.0 alternative layouts...
are no longer normative" [toon-spec CHANGELOG]. Later point releases: v3.1
added Unicode escapes for control characters; v3.2 added strict-mode
duplicate-sibling-key errors (or documented last-write-wins); v3.3 scoped
canonical-decimal number formatting [toon-spec CHANGELOG]. The throughline:
a format tightening its own determinism so independent encoders produce
byte-identical output; the same discipline structured outputs bring to
Claude API calls in the graph-engineering pipeline (`research/graph-
engineering.md`).

**Benchmark claims, per the format's own site.** A weather-forecast
illustration: ~117 JSON tokens vs. ~66 TOON [toonformat.dev]. A larger
mixed-structure set: 393,637 JSON tokens vs. 264,734 TOON, −32.7%; a fully
tabular feature-flags set (TOON's best case): 23,141 vs. 10,503, −54.6%
[toonformat.dev]. A separate accuracy study. 244 retrieval questions across
4 models; reports TOON at 72.2% accuracy vs. JSON's 71.4%, using 42.6%
fewer tokens; an efficiency metric (accuracy%/1K tokens) puts TOON at 29.2
vs. JSON-compact's 23.8 [toon-repo, toonformat.dev]. Per-model spread was
wide (Grok-4.5 97.1% with TOON, GPT-5.4-nano 57.0%), stated without further
explanation in what was fetched.

**Where TOON explicitly does not help.** The documentation names its limits
directly: "TOON excels with uniform arrays of objects. Reach for something
else when" data is deeply nested or non-uniform ("compact JSON often wins
outright"); semi-uniform data sits in the ~40-60% tabular-eligibility range
("savings shrink; stay on JSON if your pipeline already speaks it"); data is
purely tabular with no nesting ("CSV is smaller"); or the deployment is
latency-sensitive, since "some deployments (notably local or quantized
models) process compact JSON faster despite the higher token count"
[toon-repo]. A deeply-nested config dataset scored 0% tabular eligibility,
where JSON stayed competitive [toon-repo]. The underlying principle: **the
format is a translation layer, not a value in itself**: it pays only when
data actually has the amortizable shape; forcing non-tabular data through it
does nothing or costs tokens versus plain JSON.

## Evidence the authors present

- The 30-60%-class savings (32.7% mixed, 54.6% fully-tabular) come from the
  format's own published benchmark suite; maintainer-selected datasets, not
  an independently reproduced benchmark [toonformat.dev, toon-repo].
- The accuracy study (72.2% vs. 71.4%, 244 questions, 4 models) is the
  closest thing to a task-outcome result, notable because it reports no
  accuracy cost for the token savings; but the per-model spread (57.0% to
  97.1%) is wide enough that the aggregate figure could be sensitive to
  which models are included; no confidence interval was surfaced in what was
  fetched.
- The explicit "when TOON does not help" section is itself first-class
  evidence: a maintainer-published boundary condition (0% eligibility on
  deeply-nested data) is more falsifiable than the win-case percentages
  alone, since it names the exact condition under which the mechanism has
  nothing to amortize.
- No independent, non-maintainer-run comparison was located; both sources
  fetched (`github.com/toon-format/toon`, `toonformat.dev`) are the
  project's own properties.

## Sources

- [toon-repo] `github.com/toon-format/toon`: reference implementation and
  README, fetched directly (grammar, rendering forms, the "when TOON does
  not help" section, benchmark numbers).
- [toonformat.dev] the format's own site, fetched directly; same benchmark/
  accuracy figures, cross-checked against the repo (identical numbers,
  expected since both are maintainer-controlled, not independent
  corroboration).
- [toon-spec CHANGELOG] `github.com/toon-format/spec`, `CHANGELOG.md`,
  fetched directly for v3.0-v3.3 history; current spec version at fetch time
  was 4.1, so "v3" here is a superseded revision, not the current spec.
- `lhassa/llm/toon.py` and `tests/test_toon.py`: Lhassa's own build-and-
  shelve encoder, read directly.
- `doc/decisions.md`, 2026-07-28 entry ("Six fronts in one day"); the
  house's own record of building and shelving this encoder, read directly.

## Mapping to Lhassa

`lhassa/llm/toon.py` is a deliberately minimal TOON encoder/decoder, built in
the 2026-07-28 wave and explicitly **unwired** by design; its docstring
states "nothing in the repo calls this module today" and warns against
wiring it in speculatively. Its stated rationale: "the house style for
bulk-tabular data in a prompt is to hand-render compact lines inline at the
call site... there is currently no place that puts a large uniform table of
dicts into an LLM prompt." `doc/decisions.md` confirms this from the build
side: "the inventory showed no JSON-in-prompt anti-pattern to fix... the
encoder sits unwired with its first consumer named... and an explicit
do-not-wire-speculatively note": a direct application of the "format is a
translation layer, not a value in itself" principle: the house looked for
TOON's target failure mode, found it doesn't currently exist (ad hoc compact
rendering already avoids it), and shelved the fix rather than deploy a
solution to a non-problem.

**Named first consumer.** The module docstring points at a specific future
site: "a bulk graph-expansion result table out of `memory/graph_retrieve.py`
(many `{"text": ..., "source": ..., "via": ...}` rows)": a precise match
for TOON's sweet spot, since `graph_retrieve.py:retrieve()`'s actual output
is exactly that three-key dict list, capped at `limit`, currently returned
as plain Python objects with no serialization step because nothing yet
renders it into a prompt in bulk.

**Measured, not claimed; but with a caveat.**
`test_token_savings_over_json_on_bulk_tabular_fixture` builds a 50-row,
6-field table modeled on that graph-expansion shape and asserts the TOON
encoding is under 60% of `json.dumps` length; `doc/decisions.md` records the
result as "52.8% of JSON's size." This sits inside the published 30-60%
range, but the comparison basis differs: Lhassa's number is a **character-
count ratio**, not a real tokenizer count; the module's own docstring says
so plainly ("NOT a real tokenizer count, just a cheap, deterministic
stand-in"). The published figures are real tokenizer-counted. Directionally
consistent, not the same measurement.

**Grammar gap.** `toon.py` self-labels its grammar "TOON v3 spirit, kept
minimal; uniform arrays of objects only." It implements only the tabular
form; no inline form, no keyed-tabular form, no list-form fallback; a
non-uniform row set is handled by unioning field names rather than switching
rendering forms. Its cell-encoding rules are a from-scratch minimal design,
not a port of the spec's own quoting rules, and the spec has since moved
through v3.1-v3.3 (Unicode escapes, duplicate-key handling, canonical
number formatting) that `toon.py` does not implement. A legitimate scope
choice for a shelved single-purpose encoder; but a future wiring decision
should re-examine whether the minimal grammar still suffices, or closing the
gap to the current spec (v4.1) is worth it for genuine interop.

**Round-trip lossiness is a documented choice, not a spec claim.**
`from_toon()`'s docstring names specific permanent ambiguities (`None` vs.
`""`; `true`/`false`/numeric literals vs. their string spellings) as
"inherent to a text format this small." This matches Lhassa's own
degrade-never-raise design posture more than anything found in the upstream
spec fetches for this note; a Lhassa-specific documentation choice, not a
claim about the reference format.

## Not yet verified by us - and the experiment that would

- **No real tokenizer count exists for Lhassa's own encoder.** The 52.8%
  figure is a character-count proxy. The experiment: once a real bulk
  multi-row payload is a genuine prompt candidate, run the actual tokenizer
  for the receiving model against JSON and TOON renderings of the same real
  data, and compare that ratio to both the 52.8% proxy and the published
  30-60%/42.6% figures.
- **No comparison against the house's own current baseline exists.** TOON's
  win over JSON is not evidence it beats what Lhassa already hand-writes
  (the ad hoc compact-line style named in `toon.py`'s docstring); that
  comparison, not TOON-vs-JSON in the abstract, was the actual finding that
  led to shelving it. The experiment: token-count an ad hoc rendering, its
  TOON equivalent, and raw JSON for the same real data.
- **No accuracy-side experiment exists for Lhassa.** The 72.2%-vs-71.4%
  study is third-party, on a third-party benchmark; nothing measures whether
  TOON-encoded rows change answer quality on Lhassa's own retrieval tasks.
  Correctly sequenced *after* a real consumer appears, since the module is
  unwired by design.

## Open questions

- Does the `asserts` triple data `kg.py`'s cheap-model pass produces
  (subject/predicate/object/source, per `research/graph-engineering.md`)
  become an earlier candidate consumer for `to_toon` than
  `graph_retrieve.py`'s expansion rows; it's the same uniform flat-dict
  shape, already assembled in memory during `sync()`?
- If `graph_retrieve.py`'s output becomes the first real consumer, should
  the encoder grow toward the current spec (v4.1), or does the minimal "v3
  spirit" grammar remain right forever, given Lhassa has no interop
  requirement; only an internal token-cost one?
- Does the "when TOON does not help" boundary (local/quantized inference
  processing compact JSON faster despite higher token count) apply to
  Lhassa's `LocalModelProvider` paths; a real, Lhassa-specific reason to
  gate any eventual TOON use to non-local provider paths, not yet
  investigated?
</content>
