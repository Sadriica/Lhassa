# Output quality: a versioned skill as the enforcement layer for a house style

Raw sources: a launch post for `no-ai-slop` (petergyang, X, 2026-07-22, 705K
views — captured in `scratchpad/sources-pack.md` S10) and the local clone of
the skill at `scratchpad/no-ai-slop/` (`SKILL.md`, `eval.md`, `README.md`,
`agents/openai.yaml`, read directly 2026-07-30). The clone contains
substantially more than the tweet: the tweet names five example patterns and
the two modes; `SKILL.md` enumerates the full rule set and `eval.md` is a
separate, machine-checkable pass/fail rubric the skill runs against its own
output before returning it — a detail absent from the tweet entirely.

## The idea in one sentence

A quality bar for generated prose can be enforced the same way a code quality
bar is: as a versioned, explicit rule file plus a self-check pass, not as a
vague instruction to "sound more human."

## Mechanics — down to the base

`SKILL.md` (9.9KB, read in full) defines two modes and a five-category rule
set:

- **Two jobs.** *Edit* (default): given a draft, make "the minimum effective
  edit" and return the edited draft plus a **What changed** section. *Detect*:
  given a draft (or a direct question — "is this AI slop?"), name each pattern
  that appears, quote the offending line, give the fix in a few words, and
  explicitly do **not** rewrite, score, or guess whether AI wrote it — the
  skill's own reasoning: "AI detectors guess. Named patterns are evidence the
  user can check."
- **Voice preservation as the stated prime directive**, ahead of every
  individual rule: "First notice the draft's vocabulary, cadence, bluntness,
  humor, uncertainty, digressions, and level of polish. Keep the traits that
  feel personal to the writer. Do not make every paragraph equally tidy." The
  edit principles repeatedly return to this: keep "I think," "maybe," or
  fragments when they carry real uncertainty or spoken rhythm; don't
  "aggressively compress" away character; if reorganizing structure, say why.
- **Banned words, outright:** delve, foster, leverage, utilize, facilitate,
  empower, streamline, robust, cutting-edge, paradigm shift, game changer,
  "this is huge," "this changes everything," tapestry, realm, beacon,
  multifaceted, meticulous, intricate, paramount, transformative, elevate,
  embark, supercharge, harness, ever-evolving.
- **Often-empty adverbs** (cut only when they add nothing, kept when they
  carry real emphasis/uncertainty): just, literally, honestly, simply,
  actually, truly, fundamentally, importantly, crucially, inherently,
  inevitably.
- **Often-empty phrases:** "it's worth noting," "at the end of the day,"
  "when it comes to," "at its core," "in today's world," "the reality is,"
  "in order to," "going forward," "let's dive in" — cut when they delay the
  point, kept occasionally if part of the writer's recognizable voice.
- **Named structural patterns**, each with a diagnostic example and a
  rewrite:
  - *Binary contrasts* — "It's not X. It's Y." → state Y directly.
  - *Throat-clearing openers* — "Here's the thing," "I'll be honest."
  - *Faux-insight setups* — "What most people get wrong," "here's what
    nobody tells you" — flagged as flattering the writer as the lone expert.
  - *Colon reveals* — a noun phrase, colon, lowercase dramatic reveal ("The
    best part: it learns.") → rewrite as a plain sentence.
  - *Superficial analysis* — trailing `-ing` clauses that gesture at meaning
    without stating it ("highlighting the team's commitment").
  - *Importance puffery* — "stands as a testament," "marks a pivotal
    moment" → state the fact, let the reader judge significance.
  - *Weasel attribution* — "experts agree," "studies show" → name the
    source or cut the claim; ask the user rather than invent one.
  - *Fake-strong verbs* — "serves as a centralized hub" → say what it does.
  - *Synonym cycling* — rotating "agent/assistant/tool" for the same
    referent → repeat the clear word.
  - *Negative listing* — "Not a X. Not a Y. A Z." → say Z.
  - *Dramatic fragmentation* — "X. And Y. And Z." / "That's it. That's the
    whole thing." → complete sentences.
  - *Rhetorical setups* — "What if I told you...", "Plot twist:", and
    self-answered question/answer pairs.
  - *Fake-profound kickers* — the closing "deep" line that turns the piece
    into a metaphor or mic-drop. The rule is explicit and unusual: **delete
    it, do not rewrite it into a better metaphor**, end on the clearest
    concrete sentence already in the draft.
  - *Summary-recap endings* — "In conclusion," "Ultimately" → end on the
    last concrete point or next action; the reader was just there.
  - *Formatting slop* — emoji in headings, decorative mid-sentence bold,
    bullets where prose would read better, headers over two-sentence
    sections.
  - *Em dashes* — none as a default rhythm crutch in short copy; 1-2 tolerated
    in longer drafts only when they clearly beat commas/periods/parens;
    remove clusters.
- **The self-check loop.** Step 4 of the workflow: "make the minimum effective
  changes, then check the edited draft against `eval.md` yourself." `eval.md`
  is a ~20-item pass/fail rubric mirroring every rule category (voice
  preservation, banned words, each named pattern, formatting, em-dash
  discipline) plus meta-checks: "Would the writer recognize the edited draft
  as their own voice?" and "Was the edit checked directly against this file
  without requiring separate editor and evaluator agents?" — i.e., the same
  agent both edits and grades against a written rubric, in contrast to the
  eval-driven-agents note's "the doer never judges" principle for objective,
  code-checkable tasks. The two are not in tension: this is subjective prose
  quality with no ground truth to check against, so the rubric substitutes
  for an independent verifier rather than replacing one that could exist.
- **The responsible-use stance**, stated once in `README.md` and reflected in
  the "detect, don't guess" design of the second mode: the author's own
  process is "25/50/25" — write or dictate the first draft personally, use
  the skill for the middle edit pass, do the final line-by-line pass
  personally. The skill is framed as a mid-process aid, explicitly not a
  first-draft generator or an end-to-end automation of the writing itself.

## Evidence the authors present

The evidence is adoption metrics and self-reported process, not a controlled
study: 2,700+ GitHub stars, a launch post cited at 611K impressions / 10K+
saves / 13K+ link clicks (`README.md`), and the author's own stated editing
workflow (25/50/25). No blind evaluation, no before/after quality scoring
against a baseline, no inter-rater study. This should be read as a popular,
well-specified rule set with real-world traction, not as an empirically
validated quality intervention — the skill's rules are plausible on their
face (each pattern comes with a concrete diagnostic example) but "removes
slop" is not independently measured anywhere in the source material.

## Sources

- `no-ai-slop` repository clone, read in full 2026-07-30:
  `scratchpad/no-ai-slop/SKILL.md`, `eval.md`, `README.md`,
  `agents/openai.yaml` (a ChatGPT/Codex plugin adapter — no rules beyond the
  canonical `SKILL.md`, confirmed by inspection).
  Canonical repo: https://github.com/petergyang/no-ai-slop (MIT).
- Launch post: https://x.com/petergyang/status/2079943830024188105 (2026-07-22,
  705K views) — captured in `scratchpad/sources-pack.md` S10.
- Article referenced by `README.md`:
  https://creatoreconomy.so/p/use-my-no-ai-slop-skill-to-remove-20-ai-slop-patterns
  (not separately fetched; `README.md`'s own summary of the 25/50/25 process
  was used instead, since it is the primary source's own restatement).

## Mapping to Lhassa

Verified directly against code and the live filesystem, 2026-07-30:

- **The skill is already installed, unused.** `ls ~/.lhassa/skills/` shows
  `no-ai-slop/SKILL.md` (9,901 bytes, matching the clone) alongside `monitor`
  and `research`. `~/.lhassa/skills/.usage.json` records it as
  `"use_count": 0, "last_used_at": null, "state": "active"` — installed but
  never invoked by any brain yet, as of this capture.
- **A house-native quality gate that predates the skill.** `doc/narrative.md`
  is Lhassa's own voice book and enforces a materially similar discipline,
  written and committed to the repo well before this skill was installed
  (`git log` on `doc/research.md` shows the research line itself only landed
  2026-07-30; `narrative.md` is long-standing project doc). Its overlapping
  rules: "**At most one mythological touch per section**... then back to
  plain language" (a direct analog of the skill's one-image-per-text
  discipline for imagery); "Never mystical, never preachy. No wisdom-quotes"
  (overlaps the skill's fake-profound-kicker rule — both refuse an
  aphoristic closing line, though narrative.md's reason is tonal-brand,
  the skill's is anti-AI-tell); "If a sentence could appear in any agent's
  README, sharpen it until it couldn't" (a genericness test, not in the
  skill's rule set); a defined tone register ("mítico-sobrio... never
  corporate, never hyped, never cute. Numbers beat adjectives"). The
  divergence is real too: narrative.md is optimized for a consistent brand
  voice across two registers (product third-person, Lhassa's own first-person
  monk register), while `no-ai-slop` is optimized for preserving one
  individual writer's idiosyncratic voice against homogenization — the two
  rule sets solve adjacent but not identical problems, and narrative.md has
  no equivalent of the skill's banned-word list or its machine-checkable
  `eval.md` rubric.
- **The persona re-voice passes are the natural wiring point, not yet
  wired.** `lhassa/notify/voice.py` implements `revoice()`: a
  `config.notify_style` switch where `"persona"` adds "ONE cheap-model
  re-voicing pass on top of the template... ANY failure in the persona pass
  ... degrades to the template() result; a notification ALWAYS arrives."
  `lhassa/notify/digest.py`'s `_persona_pass()` is the same pattern for the
  daily digest: `digest_style = "persona"` re-voices the deterministic digest
  text through a cheap model using the identity's persona block as system
  prompt, with the same silent-degrade-to-plain-text guarantee. Both are
  exactly the seam where a `no-ai-slop`-style rubric would slot in as a
  post-pass check — the persona pass already exists, is cheap, already
  degrades safely on failure, and already touches every piece of
  brain-voiced text Lhassa sends. Confirmed by reading both modules: neither
  currently references the skill, `eval.md`, or any slop-pattern rule.
- **General pattern worth naming: skill-as-quality-gate.** The `no-ai-slop`
  design — a versioned rule file plus a companion pass/fail rubric the same
  agent checks its own output against before returning it — is structurally
  the same shape as Lhassa's `agents/gates.py` "mejor" gate (beats a concrete
  baseline on a concrete metric) applied to prose instead of code, except
  there is no objective verifier standing in for `eval.md` the way
  `agents/verify.py` stands in for the four gates' correctness check. That
  gap is real: `eval.md`'s checks are graded by the same model that wrote the
  edit, which the eval-driven-agents note above flags as against the "the
  doer never judges" principle for anything with a checkable ground truth —
  prose quality mostly doesn't have one, so the rubric substituting for an
  independent judge is a reasonable compromise, not an oversight.

## Not yet verified by us — and the experiment that would

- **Whether wiring `no-ai-slop` into `notify/voice.py`'s persona pass would
  measurably change anything a Lhassa owner notices.** The skill has never
  been invoked (`use_count: 0`), so there is no Lhassa-generated before/after
  text to compare. The experiment: take a week of real `digest_style=persona`
  and `notify_style=persona` output already logged (episodes carry the sent
  text), run the skill's *detect* mode over the batch, and count how many of
  the 20+ patterns actually appear in Lhassa's own persona voice — this would
  tell us whether the gap is real or whether the existing persona-block
  system prompt (`doc/narrative.md`'s tone rules, already in the prompt via
  `identity.persona_block`) already suppresses most of it.
- **Whether `eval.md`'s self-check catches what it claims to catch.** No
  measurement exists (in the source material or in Lhassa) of the rubric's
  false-negative rate — cases where slop patterns survive a "passed" self-
  check. An objective proxy: since several patterns are near-regex-detectable
  (banned words list, "It's not X. It's Y." binary contrasts, "In
  conclusion" endings), a cheap deterministic pre-filter could catch a subset
  without a model call at all, closer to Lhassa's $0-first house
  discipline — untested whether that subset is large enough to be worth
  building versus just running the model-graded rubric.

## Open questions

- Should the skill be wired as an automatic gate on the persona passes
  (`notify/voice.py`, `notify/digest.py`) or left as an on-demand tool the
  owner invokes by name — the former matches "skill-as-quality-gate" as a
  general pattern but adds a model call to a path that currently degrades
  safely and cheaply; the latter keeps the $0-first discipline but means the
  gate only fires when someone remembers to ask.
- `narrative.md`'s rules are brand-consistency rules (one voice, on purpose);
  `no-ai-slop`'s rules are individual-voice-preservation rules (many
  possible voices, keep whichever one is real). Lhassa's persona pass is
  closer to the `narrative.md` case — is `no-ai-slop`'s voice-preservation
  framing even the right fit, or does Lhassa need a house-specific variant
  that checks against `narrative.md`'s tone table instead of "the writer's
  original voice" (there being no single human writer's draft in the
  digest/notification path to preserve)?
- Given `eval.md`'s self-check is model-graded, not code-graded, does it
  belong in the same trust tier as Lhassa's `agents/verify.py` objective
  verifiers at all, or is it closer to the judgment branch of `core/step.py`
  — a think, not a $0 deterministic check — and should be priced and
  budgeted accordingly if wired in?
