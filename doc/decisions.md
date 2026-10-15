# Lhassa; decisions log

## 2026-08-30. Strategy: infrastructure and evidence, not a personal assistant for others

Lhassa is no longer positioned as a personal assistant for other people.
OpenClaw (~345k stars) and Hermes (~140k) already own that space at a scale
this project cannot compete with by adding features faster. Lhassa stays as
the owner's own infrastructure, and as the reference implementation and
evidence source for a separate accounting project: a real system with a real
usage history is what makes that project's accounting honest, not a synthetic
demo.

**Phase 7 (the organisational layer, multi-brain hierarchy) is FROZEN, not
deleted.** Orca (~43k stars) already owns the multi-agent orchestration
space; building it out further here duplicates work with no distribution
this project has. The code stays, stays tested, and gets bug fixes; it does
not grow new department kinds, new org primitives or new multi-user surface
without checking with the owner first. `CLAUDE.md`'s "The org layer (Phase
7)" section is marked FROZEN accordingly.

This does not change anything about how Lhassa is built for the owner's own
use: the brain, the memory, the security model, the notify surfaces and the
CLI/dashboard/Telegram doors keep evolving exactly as before. It changes what
the project is FOR when described to someone else, and it stops one
speculative direction (Phase 7) from absorbing further build time.

See `witara/context/LOG.md`, entry of the same date, for the umbrella-level
record.

## 2026-08-12. A reasoning model spends the budget thinking, and the owner gets silence

The owner's messages were arriving wrong and unclear. The cause was not the
prompt or the phrasing layer: DeepSeek V4 (flash and pro, like any
`deepseek-reasoner`) writes into a separate `reasoning_content` field BEFORE it
writes `message.content`, and `gateway.py` read `message.content or ""`.
Measured against the live API: the same question at `max_tokens=20` returns
`content=""` (the whole budget went to reasoning), at `1024` returns `"ok"`.
Every bounded call Lhassa makes is in that first regime: the notify persona
pass (200 tokens), the digest re-voicing (400), a chat reply.

**The fix is at the gateway, not per surface.** `_reasoning_kwargs` sends
`reasoning_effort="none"` so the budget buys ANSWER tokens.
`LHASSA_DEEPSEEK_REASONING`: `auto` (default) turns reasoning off below
`LHASSA_DEEPSEEK_REASONING_MIN_TOKENS` (2048) and keeps it above, so a short
message is clear and a real judgment still thinks; `off`/`on` are absolute.
A model that still answers empty gets ONE retry with reasoning off; both calls
are billed honestly. `notify/telegram_inbound.py` had already patched the same
symptom locally with a bigger-cap retry: that was one surface paying twice for
a defect every surface had.

**Two clarity defects found on the way, both in owner-facing text.** The
persona pass was told to reply "in the SAME language as the notification", and
a machine-built English line ("Health check: disk 91% full on /") got a
Portuguese answer to a Spanish-reading owner: `persona.language_directive`
pins the configured language (`LHASSA_NOTIFY_LANGUAGE`, falling back to the
digest's) and names it in English rather than passing a bare `es`. And the
model kept emitting em dashes, which the owner's rule forbids, partly because
the chat system prompt was full of them: the prompts now say not to,
`persona.scrub_dashes` enforces it deterministically on the way out (a prompt
is a request, not a guarantee), and the owner-facing strings in
`telegram_inbound.py` were swept.

**Seats moved onto the metered API.** `heartbeat_provider` was `opencode`
(the free CLI seat) and is now `cloud`; `telegram_provider` is pinned to
`default`; `model_smart` is `deepseek-v4-pro` so an evaluation that is actually
hard gets the better model. The CLIs stay in `LHASSA_FALLBACK_PROVIDERS`, so
an outage still degrades rather than breaking.

**One test-isolation defect this exposed.** Turning the persona voice on made
five tests fail: they read the owner's live `.env`, so they were asserting
English strings against a live, billed, Spanish re-voicing. `conftest.py` now
pins the deterministic $0 voice for the whole suite, the same way it already
neutralizes the calendar feed and the notify channels.

## 2026-08-12. A notification that names the intent is not reporting the failure

The first fix made the messages arrive; the owner's next one arrived clear and
still wrong: "monitor · interns: 1 new failure(s): guardian ejecuta
check_integrity". Three separate defects in one line, and only the third was
about voice.

**It reported the intent, not the outcome.** `org/watcher.py` grouped fresh
failures by the episode's `decision`, which states what the brain set out to do
("guardian executes check_integrity"). The outcome was sitting unread in
`resultado`: "check_integrity: fail; 1 critical and 1 warning". So the alert
about a FAILED integrity check never said it failed. `_failure_reason` now
reads `resultado` first (falling back to decision, then accion), stripping the
run id and the temple's "· reports ↑ temple" hop trail, which are true and
belong in the log but are not the owner's news.

**It led with org plumbing.** Every alert was prefixed "monitor · <department>:
". The department is real and worth recording; it is recorded, on the episode
(`dominio`, `despertar_por="monitor"`). Prefixing the message with the internal
seat that noticed something tells the one person receiving it nothing.

**The voice was flat because the instruction described a tone instead of naming
the failure modes.** Measured against the live model, "first person, warm, calm
and BRIEF" produced passive clerk's prose ("Una falla nueva quedó registrada")
padded with offers the brain never made ("dime cuándo quieres que lo miremos
juntos"). Naming what to avoid fixed most of it; the rest needed EXAMPLES, not
more rules: repeated runs of one fixture through the rules alone gave first
person, third person and passive by turns, and once dropped the action's name
entirely. Two worked example pairs, threaded as real chat turns, hold it. They
are keyed by language (an example only teaches in the language of the answer)
and every one of them demonstrates what the rules ask, including keeping the
technical identifier: the first draft's examples paraphrased the name away and
promptly taught the model to do the same.

**One honesty rule earned along the way.** A mid-iteration output closed with
"Estoy revisando qué causó el problema". Nothing runs after a notification is
written, so that is a lie of exactly the kind the tool-less chat prompt already
guards against. The instruction now forbids claiming any ongoing or future
action.

Before: "Requiere tu mirada: monitor · interns: 1 new failure(s): guardian
ejecuta check_integrity." After: "Revisé la integridad y encontré un fallo:
check_integrity marcó 1 problema crítico y 1 advertencia."

## 2026-08-12. One integrity run, three notifications, and a toast that ate its own punctuation

The owner sent a screenshot of two toasts side by side and asked what the
difference between them was. The answer was that they were the same event,
reported twice, in two different vocabularies. Tracing the episode log for one
`check_integrity` cycle found a third.

**The watcher was notifying about work it had ordered itself.** It is the CLOCK
for the conformance pass: on its interval it runs `check_integrity` through
`temple_run`, so the work goes down the chart (lhassa → temple → guardian) and
the result comes back up through `org/deliver.py`, which notifies the owner in
the owner's own words (the `notify.voice` phrasebook renders it as "1 serious
problem and 1 minor in my own internal checks. Run `lhassa integrity findings`
to see what failed."). The watcher then appended its OWN
`conformance_failed` event and pushed that too, machine-worded and carrying a
run id. Then, on the next tick, `detect_events` found the `temple-execute`
episode the same run had written and reported it a THIRD time as a brand-new
failure. Standing principle already in this repo: a capability that delivers
something to the owner is born on the deliver path, and a surface-direct send
is a design bug. The watcher's own push was exactly that.

Two fixes, both narrow. An event may now carry `already_delivered`, which keeps
it an event (logged, still delegable) but not a push; the conformance event
sets it, because the run it triggered always self-reports. And after running
its own action the loop re-captures the signal snapshot, so the episodes it
just caused join the baseline instead of being rediscovered next tick as news.
The watcher reports what it finds, never what it caused.

**The toast was dropping the punctuation it split on.** `notify/desktop.py`
lifts a message's opening clause into the notification summary and partitions
on `": "`, `" — "` or `" · "` WITHOUT keeping the separator. The daemon stacks
summary over body, so "the integrity check failed: 1 critical finding" rendered
as "…falló" above "hay 1 hallazgo crítico": the colon gone and nothing in its
place, which is what the owner was reading as a broken sentence. The separator
now stays on the summary.

**And it was printing the name twice.** With no separator in the first 60
characters the function fell back to the application name as the summary, so
the toast showed "Lhassa" as the app header AND "Lhassa" as the summary, with
the actual message pushed down a line. The message itself is now the summary
(an empty body is fine; a duplicated summary is not), and a first SENTENCE is
accepted as a headline with a longer allowance, so a two-sentence message does
not put its whole text in a line the daemon truncates. Both halves already
travel to the macOS path through the environment rather than being interpolated
into the AppleScript, which is what makes it safe for owner text to become the
summary; that invariant is now pinned by its test.

## 2026-08-04. A signature gate that refuses must not also delete the schedule

Four daily hygiene routines (`distill_memory`, `detect_repetition`,
`calendar_sync`, `apply_feedback`) and a digest stopped running without a word.
They had not failed: they were REFUSED at the task signature gate. Refusing is
correct (deny-by-default) but a recurring task mints its next occurrence only
when it completes, so a refusal takes the whole schedule with it, permanently.
Every remaining pre-existing pending task was queued to die the same way,
including five of the owner's own reminders with real application deadlines
running to December.

**What is established.** The current pipeline is healthy end to end: a freshly
minted `temple_run` passes the gate, executes under `oracle`, walks its hops up
to `lhassa`, and mints its recurrence. The key on disk is internally consistent
(the derived public key matches the stored one) and Ed25519 signing here is
deterministic.

**What is not.** None of the 233 historical signatures reproduces under that
key. The check covered the current canonical payload, the real pre-`6231b81`
payload (taken out of git rather than reconstructed from memory), 1816 subsets
of the stored columns, and variants of `params` (dict / raw string / re-encoded),
`estado`, `origen_actor` and `creado_en`. The remaining explanation is a key
that no longer exists, but the file on disk predates every task, so that does
not close either. The cause is recorded as UNKNOWN rather than guessed.

**Remedy; re-mint, never re-sign.** Rows that failed authentication are not
retroactively blessed. Each legacy row was CANCELLED (its trace stays in the
log) and an equivalent task minted afresh through `dispatch.mint_task`, which
signs with the live key and applies the approval frontier as usual: the five
reminders individually with their text and due date preserved, the routines by
re-applying the `maintenance` and `digest` seeds. All 18 pending tasks now
verify.

**Code change.** A refusal that also kills a recurrence now says so in its own
episode; it names the action and states that no next occurrence will be minted,
pointing at the trusted seam for re-creation. It stays deterministic and $0 and
is deliberately NOT a direct notification: `core/` must not import a shell, and
`org/watcher.py` already elevates failure episodes to the owner. Which it now
does readably; the desktop toast stopped spending its summary line repeating
the application name, and the watcher groups identical failure reasons with a
tally instead of quoting one cause three times.

## 2026-08-03. Security sweep closure: every execution path enforces the same contract

The findings deferred by the philosophy sweep, plus the follow-up audit findings,
are closed in this pass. This entry is the durable project memory for the change;
the normative behavior lives in `doc/security.md` and `doc/calendar.md`, and the
regressions are executable in the corresponding `tests/test_*.py` modules.

- **Local execution no longer bypasses policy.** Persisted tasks are signed at
  creation, retain the exact signed creation timestamp across watchlist transport,
  and are verified before fixed actions, reflexes, judgment or delegation. The
  heartbeat and `run_task_now` both reload the stored envelope, cross the same
  deny-by-default allowlist, and reject tampering terminally with an episode.
- **Approval is evidence at execution time, not just a routing decision.** An
  approval-required task can run only with either a valid autonomy decision or a
  durable approved `mint_task` row for that exact task id. This final check applies
  even to callers that reach the database/dispatch seam directly.
- **Approval identity and separation of duties are fail-closed.** API, CLI, TUI and
  Telegram resolve approvals as the canonical owner principal; user-controlled
  `by_actor` is audit metadata, never authority. High-risk actions, including
  `run_command`, `run_script`, protected-root reads and non-read-only project
  attachment, require proposer and approver to differ. A missing proposer is not a
  reason to waive the rule.
- **The model kill switch is global.** Every provider checks it immediately before
  opening HTTP or launching a local CLI subprocess. It also remains a hard stop in
  the heartbeat, fallback chain, Telegram and `/ai`; no alternate provider may turn
  an emergency stop into fallback behavior.
- **Calendar feeds resist redirect and DNS-rebinding SSRF.** Every redirect target is
  revalidated, automatic redirects are disabled, and the HTTP connection uses the
  public IP that passed validation while preserving the original Host header and TLS
  server name. Unsafe/unresolvable feeds may use an existing stale cache but never
  make the network request.
- **The skill store has one filesystem boundary.** Symlinked skill directories,
  aliases, definitions and resources cannot escape or reinterpret the store. The
  directory name is the canonical identity, so hostile frontmatter cannot redirect
  export/delete behavior.
- **Memory spend is explicit.** `LHASSA_MEMORY_DISTILL_ENABLED` is now a declared
  configuration and live setting; disabling it stops episode distillation before
  provider selection for scheduled, CLI and future callers.

Verification at closure: **2407 passed**, `ruff check .` clean and
`git diff --check` clean. The only suite warning is the pre-existing asyncio
deprecation in `tests/test_actionpack.py::test_web_fetch_happy_path`.

## 2026-08-03. Round two: the traversal that could delete the brain

A second wave of four reviewers closed the gaps the first six had not covered
(all ~20 API routers, tools/remote/health, the rest of db.py plus skills/seeds/
bench, and dashboard/voice/cli). It found the worst defect of the day.

**CRITICAL; a skill name could delete `~/.lhassa`.** `SkillStore.folder_for`
fell back to the RAW name whenever `slugify()` returned "": and `slugify("..")`
is "". So `DELETE /api/skills/..` (and `lhassa skills delete ..`) resolved to
the store's PARENT and `shutil.rmtree`'d it: brain.db, the signing keys, the
whole data dir. Two reviewers found this independently, and one verified that
`GET /api/skills/%2e%2e` returned 200 reading the parent directory before the
fix. Closed at the root: an unsluggable name becomes a stable sha256-derived
folder INSIDE the store, and every resolved path is re-validated against the
root. `get`, `delete` and `curator.restore` all cross the same boundary now,
pinned by `tests/test_skills_traversal.py`.

**`watch_command` bypassed the shell guardrail.** `handle_shell` routes every
command through `validate_shell_command`; `watch_command` built an identical
`subprocess.run(..., shell=True)` from model-supplied text and skipped it;
a way around the destructive-pattern check the shell tool enforces.

**Calendar feeds had no SSRF guard.** An owner-supplied feed URL is fetched on
a SCHEDULE, so an unguarded one turns the heartbeat into a repeating probe of
loopback/link-local/cloud-metadata addresses. It now crosses the same
`ssrf_check` the research and web_fetch paths already did.

Verified clean across the remainder: no unauthenticated route (all 24 routers
sit under the middleware's prefixes), no unsigned path to `allow()`, no
f-string SQL with user values, append-only tables genuinely append-only, seeds
idempotent, bench scored only by objective verifiers, no
`dangerouslySetInnerHTML` in the dashboard, the API token never in
localStorage or a URL, and every CLI mutation going through the same seams as
the API.

Suite after both waves: 2375 passed.

## 2026-08-03. Six reviewers in parallel: the philosophy sweep, phase by phase

Six read-only reviewers ran in parallel, one per subsystem, each against the
repo's own invariants, each required to VERIFY findings before reporting.
Applied on `review/philosophy-sweep`; the two heaviest findings are recorded
here and deliberately deferred to a fresh session rather than rushed.

**Fixed in this pass:**

- **The kill switch now actually stops everything** (`core/step.py`).
  `KillSwitchActive` subclasses `BudgetExceeded`, and the budget branch walks
  the $0 CLI fallback; so with the switch engaged the heartbeat still called
  claude/codex/opencode. A hard stop now skips the fallback entirely.
- **The fallback chain pseudonymizes** (`llm/router.py`). `pick_provider`
  wrapped remote seats in `PseudonymizingProvider`; `fallback_chain` did not;
  the one turn that failed over carried raw text to the cloud.
- **`/recall` stops conflating "no matches" with "recall is broken"**
  (`core/db.py:recall_available` + the command). Machine callers keep the
  degrade-to-empty contract; the owner-facing surface answers honestly.
- **Self-approval refused at the general seam** (`security/approval.py`).
  Proposer ≠ approver existed only inside org delegation; the generic
  `tipo="task"` approvals; the ones carrying `run_command`-class actions;
  had no check.
- **Registry/allowlist parity is now a test**, and it caught real drift on its
  first run: `check_integrity` was registered but never allowlisted.
- **Four more phrasebook entries** (calendar_sync, derive_world_facts,
  apply_feedback, draft_research_note); formats verified against the actions'
  real return strings. `distill_memory`/`ingest_knowledge`/`kg_sync`/
  `kg_resolve` still reach the owner raw: their success formats were not
  verifiable this pass, and guessing regexes is how dead phrasings are born.
- **Monitor episodes carry `dominio`** (`org/watcher.py`), and the monitor's
  direct-to-owner alert is now a DOCUMENTED exception to the deliver-chain
  invariant (it is Lhassa's own ear, like health/checks.py) instead of being
  indistinguishable from a bug.
- **`distill_episodes` gained its own boundary flag** (`memory_distill_enabled`)
  so no future caller can spend tokens without an explicit toggle at the
  memory-module edge.

**Found, verified, and DEFERRED (the next session's first work):**

- **CRITICAL; the local execution path never consults the allowlist.**
  `allow()` is called only by PTC and the remote agent; the heartbeat's
  fixed-action branch runs `actions.run` directly, so `ActionSpec.objetivos`
  and `min_trust` are silently unenforced on the dominant path (verified live:
  a `sync_repo` outside `LHASSA_ALLOWLIST_REPOS` runs anyway). The fix must
  thread signed-task verification through `step.py` without breaking the
  sense-generated triggers (repo_changed auto-sync); heartbeat semantics,
  not a patch to rush.
- **`/ai` is a third paid command** alongside `/moa` and `/cv`; the docs name
  only two. Update the catalog and add a closed-set test.
- The autonomy-streak knob never engages on the local path (`mint_task` passes
  no autonomy verdict); fail-safe today, but the documented feature is inert.

## 2026-08-03. The owner stops receiving machine output

Two separate defects reached the owner's screen as the same symptom: raw,
unreadable notifications.

**The test suite was firing real desktop toasts.** `tests/conftest.py` carefully
neutralises the calendar feed, the embedder and the quarantine, but never
touched notifications; so `LHASSA_NOTIFY_CHANNELS` kept its default of
`telegram,desktop` and any test exercising a notify path went out through the
LIVE dispatcher. The goal-loop turn cap in `core/step.py` does exactly that, so
every full-suite run popped a real `notify-send` toast reading *"Goal loop
loop_… capped: 'watch for READY' did not verify-pass"*; `watch for READY` being
the fixture goal in `tests/test_loop_runtime.py`. The Telegram channel was
equally unguarded: with a token in `.env`, a test could have messaged the
owner's real chat. Fixed by pinning the suite to the `log` channel and blanking
the Telegram credentials, so fan-out still runs and nothing escapes the process.

This is why the message could not be found earlier: it leaves no episode, no
loop row and no journal line, because pytest writes to a temporary DB and is not
a systemd unit. It was only ever visible in dunst's history.

**`check_integrity` answered in JSON.** It returned `json.dumps({...})` while
every sibling action (`curate_skills`, `distill_memory`) returns a readable
line; and that string travels through `org/deliver.py` all the way to the
owner, so a notification read `guardian completed check_integrity:
{"findings_critical": 1, …}`. The notify voice layer was working correctly; it
prefixes ("Done; ") but cannot translate a payload it was handed. The action
now answers in a sentence, and `org/watcher.py` reads the structured verdict
from `conformance_runs` (the source of truth) instead of parsing a return
string that was never meant for a machine and a person at once.

## 2026-08-03; macOS CI corrects the audit that preceded it
## 2026-08-03. A real voice: the $0 plane finally speaks as Lhassa (Stream 4)

Root cause, confirmed: the deterministic notification plane never consulted
`persona.py`. `notify/voice.py`'s lead phrases and `persona.format_alert`'s
lead phrases were two hand-maintained near-copies ("Heads up: " vs
"Heads up; ") that were free to drift, and `notify_style="template"` (the
default) never touched `persona.py` at all; only the opt-in, off-by-default
persona pass did. So the messages the owner actually received never carried
Lhassa's identity unless a model call happened to be spent on them.

Fixed, all deterministic, zero model calls:

1. **One source of voice.** `persona.LEAD_PHRASES` (+ a Spanish mirror,
   `LEAD_PHRASES_ES`) is now the single vocabulary; `notify/voice.py` imports
   it instead of carrying its own `_FRAMING` dict, and `persona.format_alert`
   draws from the same table via a severity→lead mapping. The duplicated
   strings are gone.
2. **The phrasebook is an obligation.** New `notify/phrasebook.py` gives every
   allowlisted action (the union of `security.allowlist.build_allowlist()` and
   `actionpack._temple.TEMPLE_ACTIONS`) a natural first-person lead, plus
   intent overrides (`LEARNED_ACTIONS`, `ATTENTION_ACTIONS`) so an
   action-result reads as "Done (", "Heads up) ", or "I learned
   something; " depending on what actually happened, not one blanket
   framing. `tests/test_notify_phrasebook.py` is the parity guard; it fails
   the moment a new allowlisted action ships with no phrasing.
3. **Identity reaches the $0 plane.** `voice.template()` now takes an optional
   `config` and applies two deterministic identity effects with no model call:
   localizes the lead phrase to the owner's language
   (`config.digest_language`, reused as the general notify-language signal),
   and caps notification length when the loaded identity carries a
   "keep it concise" directive (`persona.load_identity`); live today, since
   the default personal identity already carries
   "responder conciso por defecto".
4. **Closed the `org/deliver.py` document bypass.** A delivered document's
   caption used to go straight to `telegram_api.send_document`, skipping
   `revoice()` entirely; the one surface where machine text could still
   reach the owner. The caption now routes through the same voice layer a
   text-only delivery already gets.
5. **Templates by intent, not only by kind**: implemented as point 2's
   intent overrides rather than a separate mechanism, since kind-level
   framing was already too coarse for action-results specifically.

Full suite: 2338 passed, ruff clean. `tests/test_notify_voice.py` and
`tests/test_persona.py` were updated where the unified "Heads up; " phrasing
(dash, not colon) changed an asserted string; behavior for `format_alert`
itself is unchanged (verified by existing tests).
## 2026-08-03. Plan 4, Stream 1: the versatile brain (task type / mode / effort)

`core/policy.py`'s `PolicyDecision` already carried a hidden binary
(`context_tier` minimal/full). This stream makes it an explicit model of HOW
work gets done, in three deterministic, $0 axes; no model call anywhere in
this work, same "never raises, degrades to the cheapest safe default"
contract as every existing $0 layer.

1. **Task identification**: `notify/intent.py:classify_work_type()` grows a
   second, independent regex axis alongside the existing `classify()` (chat-
   cache eligibility): `question` / `research` / `build` / `review` /
   `maintenance` / `decision`. Defaults to `question` (the cheapest bucket),
   deliberately the opposite default from `classify()`'s `judgment`: this
   axis's safe default is the cheapest trajectory, not the most expensive one.
2. **Working mode**: `notify/modes.py` is a new `Registry`-backed swap point
   (`MODES`, `@register_mode`, mirroring `PROVIDERS`/`VERIFIERS`/`CHANNELS`):
   `answer_directly` / `search_and_cite` / `iterate_with_verifier` /
   `delegate_to_hierarchy` / `open_flow`, with a default `WORK_TYPE_MODE` map.
   `resolve_mode()` never raises; an unknown type or a deregistered mode name
   degrades to `answer_directly`.
3. **Effort level**: `core/effort.py` (spine, zero shell imports) defines
   `minimal` / `standard` / `deep` / `exhaustive` profiles (token budget, loop
   turns, verifier count, whether MoA opens, a declared pre-spend cost
   estimate). `resolve_effort()` picks in priority order: an explicit
   override > a `--<level>` marker parsed out of the message text itself >
   the work type's policy default > the global default (`standard`).
   `parse_effort_marker()` always strips the marker from the returned text,
   whether or not it matched a real level, so a mistyped marker never reaches
   a model.
4. **Composition seam**: `notify/task_router.py:route()` wires the three
   together for one message and returns a `RoutingDecision`; `describe()`
   renders it into one Episode-ready line. Wired into
   `notify/telegram_inbound.py:_brain_reply` BEFORE the answer-cache lookup
   and BEFORE any provider call: the resolved `effort.token_budget` now sizes
   the actual `max_tokens` request (replacing the old fixed `_CHAT_MAX_TOKENS`
   constant), and every episode's `decision` is prefixed with the declared
   trajectory (`route: type=... mode=... effort=... budget=...tk turns=...
   verifiers=... moa=... declared_cost≈$...`); the DoD ("minimal vs. deep
   produce visibly different trajectories, cost declared before it's spent")
   is checkable straight from the episode log, not only from a test.

**Two pre-existing tests updated, not the design changed under them**:
`tests/test_answer_cache_telegram.py`'s `decision.startswith("think:"/"cache-
hit:")` assertions became `in` checks; the routing prefix now precedes both
labels; the behaviour they actually assert (a real think happened / a cache
hit was served) is unchanged.

**Not done in this stream** (explicitly out of scope, left for later
integration passes): wiring `loop_turns`/`verifier_count`/`moa` into the
heartbeat's `_judge()` path in `core/step.py`, and into `/moa`'s panel-open
decision; those consumers can adopt the resolved `EffortProfile` without
further design work, they just haven't been touched yet so the risk surface
of this stream stays small.
## 2026-08-03; `lhassa setup`: one command closes the eight-manual-step gap

Verified problem (`doc/plan-4.md`, Stream 3): installing Lhassa never produced a
working system. `seeds.apply_all` was only ever called from `lhassa seed
apply-all`: a fresh clone gave a bare chat, with no departments, digest,
maintenance or flows until eight manual steps were run by hand.

**`lhassa setup`** (`cli/setup_cmds.py`) closes the gap with six deterministic,
$0 steps, none of which touches a model: detect the host (reusing `host.py`
unchanged; no new detection was written), detect or configure a brain
($0-first: a subscription CLI on PATH, then a reachable local Ollama, then an
existing API key), ask for permissions **by scope** in plain language (never
all-or-nothing; declining a scope always leaves a working system with that
capability off), apply the matching seeds for a chosen profile, install
systemd/launchd services (the existing unit files, only the checkout path is
templated in), and a smoke check that reports one honest sentence about what
is now alive.

**The actual fix**: every profile applies a fixed baseline
(`temple`, `digest`, `maintenance`, `personal`) that alone already satisfies the
DoD; a real department, the daily digest, the maintenance schedule, and at
least one real work-flow (the personal secretary's morning/evening briefings)
— then layers ONE domain seed on top (`research` → `research`, `dev` → `cv`,
`knowledge` → none beyond the baseline, since its distinguishing scope is the
`read` permission rather than a seed). This is a deliberate design choice, not
an existing seed named per-profile; no "dev" or "knowledge" seed exists today.

**Permissions write real config**, not a flag: `read` → `knowledge_roots` /
`protected_roots`; `commands` → `auto_sync_repos` (+ optionally
`LHASSA_ALLOWLIST_REPOS` via the settings advanced escape hatch, and a pointer
to `scripts/install-privileged.sh` for sudoers, run separately); `network`
declined pins `heartbeat_provider` to `local` so the routine think never
reaches the network; `notify` toggles `notify_channels`. Every decision routes
through `lhassa.settings` (never `.env`), so a re-run is idempotent by the same
contract every setting write already carries.

**Testable by construction**: every judgment; brain detection, scope
decisions, which seeds a profile applies; is a pure function over injected
inputs (`env`, `which`, `probe_ollama`, a plain `dict` of granted scopes),
mirroring `host.py`'s own "pure parser + injected collector" pattern; no TTY
and no network are needed to exercise the wizard's logic. `tests/test_setup_cmds.py`
adds 32 tests, including two end-to-end ones that run the REAL seed registry
(not a stub) for every profile and assert the DoD against `smoke_check`'s real
topology/flow counts, plus an idempotency check.

**DoD met**: one command, `lhassa setup --yes`, takes a fresh clone to a brain
with departments, digest, maintenance and flows; no hidden manual step. Full
suite green, ruff clean.
## 2026-08-03. Stream 5: four interfaces, one capability

Plan 4's Stream 5 asked that anything doable in one surface be doable in the
others, and that no endpoint be left without a surface. Three findings shaped
the work, and each one is a variation of the same defect: a capability existed
but only one door opened onto it.

**The CLI could describe flows but not author them.** `lhassa flows` was
read-only; list, templates, show, apply. Authoring, running and following a
flow happened on the dashboard canvas or nowhere. A singular `lhassa flow` now
carries the full lifecycle: `create` (from a template or a blank one-trigger
scaffold), `edit` (a `$EDITOR` round-trip), `validate`, `run`, `watch`, `logs`,
`runs`, `delete`.

Two decisions inside it are load-bearing. First, **the run crosses
`org/delegate.py:delegate_flow`, never `flows/engine.py:run_flow`**: the
standing principle that a flow is never run bare binds a new surface exactly as
it binds the old ones, and a test asserts that `run_flow` is unreachable from
the CLI. Second, **`edit` never discards the operator's work**: an invalid
buffer stays on disk, the command says where, and it offers to re-open rather
than dropping the edit on the floor. Losing someone's typing to a validation
error is not a validation failure, it is a data-loss bug.

`watch`/`logs` read the durable kernel directly rather than through the API, so
they keep the CLI's existing virtue; the terminal works with the server down.

**The TUI covered 4 of ~18 domains and could not find anything.** It is now
eight tabs (Flows, Skills, Memory and Org join the original four), but the
structural change is `cli/tui/screens/base.py:TableScreen`: one place that
owns the `/` filter, the live re-poll and the degrade-on-read-failure, so a
future screen inherits all three instead of re-deriving them.

Two small judgements there. The filter is a **substring match, not fuzzy**: a
filter you can predict beats one that guesses, and there is nothing to learn.
And the live refresh **skips the redraw when the rendered rows are unchanged**,
which is what keeps an idle brain from flickering and keeps the cursor where the
operator left it; a polling UI that redraws unconditionally is unusable long
before it is slow. The command palette (`ctrl+p`) is Textual's own, fed a
provider; it offers navigation only. Approve/deny/run stay on the screen that
owns the selected row, because a palette entry cannot show you what you are
about to act on.

**The dashboard had no API client and seven endpoints with no consumer.**
`lib/api.js` was ten lines. Every panel decided for itself whether to check the
status, how to word the error, and whether to send credentials; and none of
them sent credentials, so the dashboard only ever worked on loopback.
`lib/apiClient.js` is now the one door (base URL, token header, status→Error,
JSON parsing, one JSDoc-annotated function per endpoint). One behaviour
improves as a side effect: the client surfaces the API's own `{"detail": …}`,
so a flow that fails validation names the offending node instead of reporting
`Save failed (400)`.

Artifacts, Integrity, Research assessments, the memory graph/entities, episode
search and `/api/flow-runs` all have surfaces now. **The memory graph renders as
ranked lists, not a node-link diagram**: stated here rather than buried,
because it is a real reduction. The read model is a projection over facts and
episodes; a force layout would add a dependency and a screen full of pixels
without adding a fact the lists do not already state. The client function is
unchanged if a canvas is layered on later.

**What was cut.** The v2 node kinds on the canvas (they belong to Stream 2's
engine work, not to a surface), `lhassa init`, and a full migration of every
dashboard panel off raw `fetch`: the client covers every domain, but roughly a
dozen peripheral panels still call `fetch` directly and were left for a
mechanical follow-up.
## 2026-08-03. Stream 6 Phase A: one brain per person, shared resources

Multi-user, Phase A only (Phase B; a shared team instance with per-actor
auth/RBAC; is explicitly out of scope). The owner's choice stands: multiple
sovereign instances, not one shared instance.

**`actor_id` promoted, not invented.** `Origin.actor_id` (`security/trust.py`)
and `tasks.origen_actor` already existed; `approvals` carried an actor only
inside its JSON `payload`. Migration 11 (`core/db.py`, additive, backfilled;
existing approvals recover their actor from `payload["actor_id"]`, facts and
knowledge default to `Config.root_admin_id`, no row is dropped or blanked)
promotes it to a real column on `approvals`/`facts`/`knowledge`: the three
places "who did/authored this" is load-bearing. Skills/flows/templates stay
file-based and untouched.

**The shared/private boundary is closed by construction.** `org/sharing.py`'s
`SHAREABLE_TYPES` is `{skill, flow, template, knowledge}`: no code path
accepts `fact` or `episode`, checked both at `publish()` and, defensively, at
`list_offerings()` read time (so a row that reached `shared_resources` through
any other writer still can't surface). Two new tables, `shared_resources` /
`resource_subscriptions`, hold only what was explicitly published/subscribed.

**Cross-instance permission rides `org/relationships.py:permits()`: the
existing seam, extended, not bypassed.** A fifth, closed edge kind, `shares`
(`RELATION_KINDS`), directed A→B ("A may discover/subscribe to B's
resources"); it is INSTANCE-scoped (`INSTANCE_SCOPED_KINDS`), so its
endpoints skip local-topology validation and instead enforce `to_id ==` the
local instance's own id (an instance can only grant access to itself). Every
publish/grant/subscribe/list call logs its own $0 `cross-instance-share`
episode either way (allow or refuse), attributed `dominio=root_admin_id`.

**Product decision made without asking (flagged, not silently assumed): the
SHARES grant registry (`org_relationships_file`) is a file shared across every
instance on one box**: the same assumption the existing multi-tenant DB shape
already makes for `core.db`. There is no live multi-machine transport yet;
once instances run on separate machines, either the grant must replicate or
the requester must ask the provider over a real transport. This is written
into `org/sharing.py`'s module docstring, not decided silently.

**Surfaces:** `lhassa share publish|unpublish|grant|revoke|offerings|
subscribe|unsubscribe|subscriptions` + `/api/share/*` (`api/share_routes.py`).

**What the leak test actually proves, stated plainly.** Two tests exist:
`test_private_facts_never_leak_across_instances` shows the happy path never
returns a fact even with a full grant; the stronger
`test_list_offerings_filters_out_a_row_that_bypassed_publish` writes a
`fact`-typed row directly via the low-level `db.publish_resource` (bypassing
`publish()`'s own check entirely) and asserts `list_offerings` still refuses
it; that is the one that would actually fail if the read-time filter were
ever removed. Both are `core.db`-row isolation tests (a real safety
guarantee), not a network/process penetration test; no real cross-machine
transport exists yet to attack.

**Not touched:** `core/step.py`'s allowlist gate (Stream 0) is unchanged and
not weakened; sharing operations are explicit CLI/API calls, not ACTIONS
routed through the heartbeat. Trust degrade semantics for consumed shared
content (treat as MUNDO) are documented in `org/sharing.py` but not yet wired
into an actual consumption path; nothing subscribes-and-executes in Phase A.

Full suite: 2352 passed (baseline was 2349 passed + 2 failures in
`test_relationship_api.py` from a hardcoded four-kind assertion, updated to
include `shares`; +1 net new test added while hardening the leak coverage
below). `ruff check .`: all checks passed.

## 2026-08-03. El CI de macOS corrige la auditoría que lo precedió

The first real macOS CI run (triggered by the push to `main`) came back
**9 failed, 2316 passed**: and its value was not that it found nine failures,
but that it refuted two things the macOS evaluation had asserted.

**A regression introduced by the platform layer itself.** `check_mem_pressure`
dispatched to the host layer whenever the platform was not Linux; including
when the caller had EXPLICITLY passed `meminfo_path`. A test injecting a
meminfo fixture silently got a `vm_stat` reading instead. That violates the very
contract the layer was documented as replicating ("pure parser + injected
input"): a caller naming a file is asking for THAT file, on any platform. Fixed
so an explicitly injected path always outranks platform dispatch.

**A misclassification in the Phase-1 inventory.** `tools/privileged.py` was
filed under "(d) strings only; do not touch". It is not: `allowlist()` filters
its candidates through `shutil.which`, so the set is genuinely smaller on a host
without systemd or pacman, and three tests asserting a hard-coded
`{systemctl, journalctl, pacman, power-mode}` failed. The audit looked at the
health/system surface and concluded there was almost no platform coupling left;
it had simply not looked here.

The rest were tests encoding the developer's own machine: the desktop-channel
test expected `notify-send` (correct on Linux, wrong as a universal claim), and
three `test_moa.py` tests depended on `claude`/`codex` being installed. All are
now pinned by injection; `LHASSA_PLATFORM`, `_binary_missing`,
`LHASSA_PRIVILEGED_ALLOW`: so they assert the same thing on any host.

Verification is now run in two modes locally: the normal Linux suite, and one
with `LHASSA_PLATFORM=darwin` and the CLI binaries stripped from `PATH`, which
approximates the macOS runner. That second mode immediately caught a test of its
own that depended on the ambient `LHASSA_PLATFORM`.

**The honest lesson.** The macOS work claimed that injection-parametrised tests
running on Linux "genuinely cover the macOS branches". They cover the branches
that were written; they do not cover the assumptions that were never questioned.
Only the real runner found those.

## 2026-08-03. AGPL-3.0: sovereignty, attribution, and the commercial option
## 2026-08-03. AGPL-3.0: soberanía, atribución y la opción comercial

Lhassa is licensed under **AGPL-3.0-or-later**, with one additional term under
section 7(b) requiring the attribution notice to stay visible.

**Why AGPL.** A permissive licence gives away the one thing this project is
about. A brain that is local-first and sovereign can be taken under MIT-style
terms, closed, and offered back to its own users as a service; the exact
capture the design exists to prevent. The
AGPL closes that gap and closes only that: someone self-hosting Lhassa for
themselves or inside their company owes nothing, and the network clause bites
only when the software is offered to other people as a service.

**Why not a permissive licence to attract contributors.** The nearest analogue,
Nous Research's Hermes Agent, is MIT with ~225k stars and ~43.5k forks. You
cannot out-permissive an incumbent that size; competing on licence terms is a
race already lost. AGPL costs adoption from a base of zero stars while enabling
a business; and companies that cannot deploy copyleft are a different customer
from people who want something free, which is precisely the segmentation a
commercial licence needs.

**The 7(b) term is implemented, not merely declared.** An attribution clause is
worth nothing if the notice is a decorative string. `lhassa/legal.py` is the one
source of truth; the CLI version panel, `GET /api/status`, a dedicated
`GET /api/legal` and the dashboard's about card all render it (the dashboard via
`dashboard/src/legal.js`, kept in sync by test). `tests/test_legal_notice.py`
fails if any surface stops showing it, or if the two copies drift. The term
itself is minimal: preserve a notice, nothing more; it grants no rights over
the names and removes no AGPL freedom.

**The CLA is a draft, and it is the one asymmetric thing here.** It exists so a
commercial licence can be offered later, which requires one party to hold
sufficient rights. `CONTRIBUTING.md` says that in plain words rather than hiding
it behind process language, and it is marked **not in force until a lawyer
reviews it**: no external contribution has been accepted yet, so nothing is
pending on it.

Phase-1 audit found **no incompatibility**: zero GPL anywhere. Python deps are
MIT/BSD/Apache-2.0; the only proprietary packages are NVIDIA CUDA libraries
pulled exclusively by the optional `voice` extra (via `torch`), never
distributed by Lhassa and absent from the default install. The 181 npm packages
are 134 MIT / 19 ISC / 15 Apache-2.0 / 6 BSD-2 / 3 BSD-3 / 2 MPL-2.0 /
1 Python-2.0 / 1 CC-BY-4.0; all compatible.

**Open decisions, deliberately not assumed:**

- **Copyright holder stays `Sadriicaa`.** If Witara is incorporated, assigning
  copyright to it is a later, separate change.
- **`lhassa/_vendor/aiosqlite.py` needs a determination.** Its docstring says it
  is an original subset implementation over stdlib `sqlite3`, written because
  upstream's worker thread stalls; i.e. NOT vendored third-party code. If that
  is right, the directory name is misleading and the file should carry an SPDX
  header like everything else. It was left untouched pending that call, because
  an API-compatible reimplementation can raise derivative-work questions that
  are not mine to settle.
- **The LICENSE body must be verified verbatim.** It was written from the
  system's SPDX text (`/usr/share/licenses/spdx/AGPL-3.0-or-later.txt`) because
  gnu.org was unreachable from this machine; the line wrapping differs between
  renderings. Replace it with the canonical FSF file before publishing:
  `curl -o LICENSE https://www.gnu.org/licenses/agpl-3.0.txt`, then re-append
  the section 7 block.

## 2026-08-03; macOS as tier 2: a host layer, and a quarantine that does not yield
## 2026-08-03; macOS como tier 2: una capa de host, y una cuarentena que no cede

The audience for a local-first, open-source brain runs local inference, and that
audience is largely on Apple Silicon. macOS becomes a **tier-2** platform: the
Python suite and lint run in CI, everything that is not hardware works, and what
is best-effort says so. Linux stays tier 1 and its behaviour is unchanged.

Phase 1 (evaluate-before-implementing) corrected two premises worth recording:

- **There was almost no "silently wrong" to find.** The task assumed the system
  surface would return plausible-but-wrong values off Linux. It does not:
  `check_mem_pressure` on a missing `/proc/meminfo` returns *"Memory info
  unavailable"*, and `/proc/uptime` was already wrapped to *"uptime unknown"*.
  The repo's degrade-honestly discipline was stronger than the brief assumed.
  `actionpack/_system.py`'s uptime read was rigid in STYLE (not injectable, so
  not testable by injection) but never dishonest.
- **The one real "silently wrong" was somewhere nobody looked:**
  `security/quarantine.py`. None of `unshare`/`firejail`/`bwrap` exist on macOS,
  so isolation would degrade to `"none"` and the run would proceed. On Linux
  that is a rare misconfiguration; on macOS it would be the NORMAL state, which
  would quietly turn *"authored code never executes outside quarantine"* into
  running model-written code unconfined on the owner's machine; and the tool
  forge and PTC are exactly what this audience exercises.

What was built:

- `host.py`, leaf infra: pure parsers (`parse_proc_uptime`, `parse_boottime`,
  `parse_vm_stat`) plus thin collectors, and a `PLATFORMS` registry so adding a
  platform is a drop-in and selecting one is configuration (`LHASSA_PLATFORM`),
  never surgery. Tests parametrise by injection, so a Linux CI run really does
  cover the macOS branches. The module-level `platform.system()` dispatch that
  already existed in six modules was left alone and lives inside the default
  implementations; two idioms, not a third.
- **The quarantine fails closed off Linux.** `sandbox-exec` is added as a level;
  when no sandbox is available at all the run is REFUSED. An operator who set
  `LHASSA_QUARANTINE_ISOLATION=none` deliberately still gets their opt-out; the
  refusal is for "none was available", not for "none was chosen". The Linux
  selection chain is byte-identical: the layer is consulted only off Linux.
- **A metric that differs is labelled, not converted.** macOS memory accounting
  (compressed/purgeable/wired) is not Linux's arithmetic, so `MemoryReading`
  carries `comparable=False`, the summary names its source, and no caller
  compares the two numbers as if they were one.
- `scripts/launchd/` mirrors `scripts/systemd/`, with the mapping documented as
  APPROXIMATE: `StartInterval` measures from start (not end), and launchd has no
  `Persistent=true`. A sleeping Mac fires every overdue job on wake; the same
  class of bug fixed earlier today in `next_recurrence_due`, which already keeps
  a schedule on its wall-clock slot across suspend.

Linux verification: 2288 passed before, 2288 + the new platform tests after, no
regression. **The macOS CI run itself is still outstanding**: GitHub Actions
only runs on pushed commits and this work is deliberately uncommitted, so the
matrix is written and validated but its first real result is pending.

## 2026-08-03. Recall stops remembering its own paperwork
## 2026-08-03. El recall deja de recordar su propio papeleo

`db.search_episodes` already demoted automation episodes below interactive ones
(a flat +5.0 on the bm25 rank for `monitor`/`task:*`); but that list predates
the org hierarchy and was never updated. The result: `org-deliver` (152),
`org-delegate` (38) and `temple-execute` (37); 227 of one week's 309 episodes;
competed on equal footing with the owner's actual conversations. Since this same
function feeds `/recall` AND the lowest-priority context layer in
`memory/load.py`, the brain's memory of its own life had filled with its own
memos.

The penalty is now GRADED by how much outcome an episode carries, rather than
applied flat to everything automated:

- **Transport takes the full 5.0**: `org-deliver`/`org-delegate` record only
  movement (`deliver:lhassa→owner`, `forward:guardian→temple`,
  `delegate:lhassa→temple→oracle`). No outcome, no recall value.
- **Execution takes only 2.0**: `temple-execute` is the hop that actually ran
  the action and carries the real `resultado` ("curate_skills → 3 stale skills
  archived"), so "what happened with curate_skills?" must still find it instead
  of the memo about forwarding it.

Nothing is hidden and nothing is deleted: the rows stay indexed and still win
when they genuinely match better. Writing the test for that claim corrected it;
with only two documents bm25's IDF term collapses to ~0 and the flat penalty
dominates, so the demotion behaves like a filter; it is only at real corpus size
that scores spread past the penalty and "a nudge, not a filter" is actually
true. The test now builds a 200-episode corpus so it asserts the real behaviour
rather than a convenient one, and the caveat is recorded in its docstring.

## 2026-08-03. Two bugs of a machine that sleeps
## 2026-08-03. Dos bugs de una máquina que duerme

Investigating repeated notifications surfaced two defects that only appear on a
laptop (the machine suspended 2684 times in 14 days) and both had been
invisible because the code LOOKED correct.

**The recurrence slot did not survive suspend.** `core/step.py` re-minted the
next occurrence with `max(now, old_due + interval)`. The `old_due` anchor is
right; the `max` destroys it. Suspend past more than one interval and every
overdue recurring task re-anchors to the WAKE INSTANT instead of its wall-clock
slot; so an 08:00 digest walked to whenever the lid was opened (observed:
21:30 → 15:51 → 14:11 → 09:03), and because every overdue task re-anchors to the
SAME instant, the deliberately-separated 08:00 and 21:00 schedules collapsed onto
each other and fired as duplicates. The owner had been getting the digest twice,
seconds apart, every day (09:05:29 + 09:05:19, 14:13:18 + 14:13:12, …).
Extracted as `next_recurrence_due()`, which advances in WHOLE intervals: 08:00
stays 08:00 across any downtime, and occurrences missed while suspended stay
missed rather than replaying as a wake-up notification storm. 7 regression tests.
The two already-collapsed pending rows were cancelled through
`db.set_task_state` (not deleted; the table is a record) and the `digest` seed
re-applied, restoring 08:00 + 21:00; `brain.db` backed up first.

**The inbound poller's backoff could never fire.** `run_forever` had correct
exponential backoff, but it only ran on exceptions ESCAPING `poll_once`: and
`poll_once` is defensive: it catches the transport error and returns `offset`
unchanged, which is byte-identical to a clean idle long-poll. The loop therefore
could not tell "broken" from "idle" and spun at the speed of the failures:
~45 `getUpdates` attempts per second with DNS down, burning CPU and battery and
flooding the journal. Added `poll_cycle()` returning `(next_offset, ok)`;
`poll_once` stays as the offset-only wrapper so its many existing callers are
untouched. An idle cycle is explicitly `ok=True`: no updates is not a failure.

Both were found while looking for something else: the owner reported repeated
"Goal loop … did not verify-pass" notifications. That message has **never been
emitted on this machine**: zero occurrences in the full user journal (back to
2025-09-09), zero rows in `loops`, no task carrying `_loop_id`. Its only emitter
(`core/step.py`) requires a loop row that has never existed. Recorded here as an
open question rather than a fixed bug: the real text is still unidentified.

## 2026-08-03. The utility tribunal: the counterweight that was missing
## 2026-08-03. El tribunal de utilidad: el contrapeso que faltaba

Every invariant Lhassa had was an invariant of RESTRAINT; deny-by-default,
signed tasks, quarantine, provenance, integrity contracts. None asked **"is this
used?"**, so the system optimised what it measured and accreted capability it
never exercised. Its own instruments said so: in the seven days before this
change, 227 of 309 episodes (73%) were hierarchy bookkeeping, and owner-facing
thinks per week ran 41 → 34 → 4 → 14 → 8 while ~2000 lines of self-governance
machinery went in. `agents/utility.py` is the counterweight, deliberately built
as the mirror of `skills/curator.py`: the curator gives SKILLS a usage
lifecycle; this gives CAPABILITIES (registered actions) the same discipline.

- **No new storage.** A skill read leaves no trace, so the curator needs a
  sidecar. A capability leaves an Episode. So this is a pure derived read over
  the append-only log; the choice `memory/evidence.py` already makes.
- **Owner-driven vs self-driven is the whole point.** Counting bare uses would
  call a capability "active" because Lhassa's own maintenance seed schedules it
 ; exactly the self-referential trap. Each use is bucketed by wake source into
  owner / self / unknown against a closed, documented heuristic vocabulary.
- **Two ledgers, maxed not summed.** The episode's wake source misses deferred
  work the owner asked for (`/remind` mints today, fires next week from
  `task:*`). The task row keeps the real provenance in `origen_actor`, so that
  counts too. The ledgers overlap, so they combine with `max()`: an inflated
  count in the module whose job is honest measurement would be the worst
  possible bug. `origen_trust` is deliberately NOT the signal: a seed mints at
  HUMANO trust too (`seed:digest`); trust answers "how much may this do", never
  "who asked for it".
- **It proposes; it never retires.** A registered action lives in code. The
  `review_capabilities` action parks ONE `retire_capability` approval per
  capability, once ever (watch_state-deduped), capped at 5 per sweep with the
  remainder REPORTED, never silently dropped. Guardian owns it (weekly, via
  `temple_run`), so it rides the same down-execute-up hop as its siblings.
- **A young brain is not put on trial:** nothing is proposed until the instance
  has more than `utility_grace_days` (14) of episode history; the curator's
  mtime-fallback idea, applied to time-in-service.
- Surfaces: `lhassa utility survey [--all] [--json]` and `lhassa utility
  docket`; `LHASSA_UTILITY_PINNED` is the deliberate-dormancy escape hatch.

First live verdict on the owner's own brain: of 43 registered capabilities,
**3 are owner-driven** (`research_reply` 18, `send_reminder` 8, `check_spend` 1),
13 are self-driven, and 27 have never been used by anyone. The instrument found
its own first defect during that run; `send_reminder` read as self-driven
because its execution is deferred; which is what forced the task-ledger pass.

**Correction to the 2026-07-30 (post-review) entry.** That entry recorded the
full suite as order-dependently hanging at `test_turn_cap_stops_before_any_
model_call` after reaching 43%. Reverified this session: the full suite runs to
completion, **2245 passed in 104–124s**, both with and without flags, and
`pytest-randomly` is not even installed (only `pytest-asyncio`), so the earlier
`-p no:randomly` reading was a no-op. The hang does not reproduce. A recorded
debt that no longer exists is as corrosive as a hidden one; the original entry
stands as written, corrected here rather than edited away.

## 2026-07-30. Integrity Watcher: la filosofía se convierte en contratos

The existing monitor could see health/failure transitions but could not prove
that a functioning system still respected Witara/Lhassa. Added a second,
deterministic plane:

- `org/integrity.py` defines manifest v1: DB schema, research event continuity
  and earned investment, artifact presence, canonical research flows, product
  adoption/approval chain, enforceable quarantine and approval age.
- Migration 10 persists every run, check and finding append-only, plus a small
  per-contract current projection. A warning is not green; only a completely
  passing current manifest issues a time-bounded attestation.
- The ordinary `$0` watcher runs integrity on a separate hourly cadence and
  converts non-pass results into normal monitor events/alerts. Attestation
  expiry observes the observer without a second daemon.
- Product `apply-change` now fails closed unless the current manifest has a
  fresh green attestation. This gives contracts authority only over the
  dangerous transition they govern; routine/read-only operation stays
  available.
- Surfaces: `lhassa integrity contracts|check|status|findings`,
  `/api/integrity`, `/api/integrity/check`, `/api/integrity/findings`, and the
  overview's Integrity line.

The guardian records and routes remediation but does not silently repair or
weaken a contract. Thinking remains the exception: no integrity check calls an
LLM.

Implementation verification: 383 focused DB/flow/research/product/integrity/
watcher/API/CLI/architecture tests pass; ruff and `git diff --check` are clean.

## 2026-07-30. Las ideas ahora se ganan su presupuesto de investigación

The owner asked to reserve academic effort for technologies worth pursuing and
made complete stage logs non-negotiable. `/idea` is now a durable funnel before
it is a corpus factory:

- A new two-brain assessment flow reconstructs the theoretical mechanism first,
  then has the skeptic score nine criteria with rationales and custody source
  ids. An `idea_assessment` verifier rejects incomplete rubrics and unsupported
  positive theory/evidence claims.
- The model does not decide investment. `core/idea_assessments.py` validates
  against the real custody source registry, recomputes the weighted score and
  deterministically routes `none→archive`, `exploration→watch`,
  `applied→research`, or `rigorous→experiment`.
- Migration 9 adds `idea_assessments`, the current query projection, and
  `idea_assessment_events`, the append-only audit source. Creation and every
  transition atomically append an event and update the projection. Evidence,
  assessments and completed corpora are also registered artifacts.
- Only `research`/`experiment` dispositions commission the existing full
  collector→analyst→skeptic→synthesist corpus. Replays key on the evidence
  digest and resume by state instead of duplicating history.
- The funnel is visible through `lhassa research assessments`,
  `lhassa research assessment <id>` and read-only
  `/api/research/assessments[/{id}]`.

## 2026-07-30. Del corpus al producto: adopción, no auto-modificación

Research may now lead to implementation without acquiring authority over the
product. The boundary is encoded in `agents/product_change.py` and the
`lhassa research propose-change/build-change/apply-change` commands:

- A proposal accepts only an active `code` project and a corpus draft that
  passes the LLM-free `research_corpus` gate. It freezes the source path and
  SHA-256, records an append-only artifact snapshot, and parks an experiment
  approval. No worktree or product change exists at proposal time.
- The approved experiment runs from a git worktree under
  `LHASSA_ADOPTION_DIR`, outside the product checkout. Its acceptance command
  crosses the existing quarantine with a scrubbed environment; best-effort
  (`isolation=none`) is insufficient and fails closed.
- A passing gate produces an immutable candidate commit plus patch artifact,
  then parks a *different* integration approval. Approval to learn is not
  approval to ship.
- Integration rechecks the approval/candidate pair, source and candidate
  immutability, clean product checkout, and unchanged base commit. It creates a
  merge commit and records an exact `git revert` rollback operation. Drift,
  owner edits, missing isolation, failed verification, or absent approvals
  stop the transition without merging.

Verification at close: 85 research/approval/architecture/CLI tests and 258
actionpack/dispatch/command/code/quarantine/versioning/gate tests pass; ruff and
`git diff --check` are clean.

## 2026-07-30 (post-review); La investigación deja de ser ceremonia

Owner approved a critical review of the new research line and asked to close
every gap it identified. The philosophy stays; its claims are now enforced:

- **Evidence before prose**: `agents/research_evidence.py` builds a bounded
  MUNDO-trust custody bundle before the first aide thinks: explicit URLs or
  Brave/DDG discovery, SSRF-checked manual redirects, HTTP/failure status,
  requested/final URL, timestamp, content hash, visible text and a capped chase
  of likely-primary nested resources. Search snippets are discovery, never
  proof. `S1…Sn` ids are stable inside the bundle.
- **No lossy telephone chain**: flow tasks opt into `input_mode=full` +
  `preserve_context`; every aide sees the original bundle and every previous
  stage in one flat dossier. The skeptic's prompt audits captured fragments,
  not the analyst's confidence.
- **A real árbitro**: the regex that only looked for `## Open questions` was
  replaced by the LLM-free `research_corpus` verifier: seven sections exactly
  once/in order, declared source ids, no unknown citations, every authors'
  evidence bullet cited or `UNVERIFIED`, and Baseline/Instrument/Metric/Stop
  rule required. It also rejects a small mechanical hype/slop vocabulary.
- **Durable + content-addressed `/idea`**: the command first mints a signed
  HUMANO `research_intake` task; `run_task_now` is merely the early heartbeat,
  so a restart cannot erase the promise. Evidence and draft filenames carry
  the bundle digest; the durable flow uses that digest as idempotency key, so
  retries do not overwrite research history.
- **Topic-scoped internal evidence**: research-note packs now filter bench,
  episode and loop rows by topic, preserve row ids, apply the declared seven-day
  episode window, and label global ledger/approval readings as operational
  context rather than topic evidence.
- **The first draft exposed the need**: `earned-autonomy.md` says cost fields
  were redacted although its verbatim appendix contains them. That contradicts
  the pack and proves a prompt is not a verifier; the draft remains explicitly
  review-only rather than being rewritten as if the failure never happened.

Offline verification at implementation close: 192 focused research/security/
actionpack/architecture tests green; ruff and `git diff --check` clean. The full
suite reaches 43% with no failure, then order-dependently hangs at
`test_turn_cap_stops_before_any_model_call`; that file and its neighboring
files pass in isolation, so the hang is recorded as pre-existing suite-state
debt rather than disguised as a green full-suite result.


## 2026-07-30 (final); El departamento del geshe: los aides + la puerta /idea

Owner asked whether the geshe's SUPPORT department existed; it didn't (the
corpus work had been done by ad-hoc subagents). Built and seeded live:

- **Four aides under the geshe** (`seeds/research.py`, seed `research`,
  idempotent, ensures temple first): `collector` / `analyst` / `skeptic` /
  `synthesist`: one per step of the corpus method, dept label `research`.
  The skeptic is a separate brain BECAUSE the doer never judges.
- **The idea-intake flow** (`flow-research-intake`): trigger → capture
  sources (nested resources included) → mechanics down to the base → verify
  claims (theirs-vs-ours, attribution chains) → synthesize the seven-section
  corpus draft → an OBJECTIVE structure gate (regex árbitro on the method's
  headers) → doc output. Runs through `delegate_flow` like every flow.
- **The `/idea <text|link>` door** (`notify/commands.py`): acks immediately,
  runs the flow in background, writes the draft to `doc/notes/drafts/idea-*`
  behind the review header, and DELIVERS the result up the hierarchy via
  `org/deliver` with the geshe as origin; no surface-direct send. A run that
  reaches no draft RELAYS the team's actual words (the CV lesson applied at
  birth).

Live verification: `lhassa seed apply research` created all four aides under
geshe in the real tree + saved the flow; re-apply is a no-op.


## 2026-07-30 (cierre); El geshe: el cerebro del método de investigación

Owner asked for a brain that owns HOW research is written, drafted, built and
focused; and picked the name: **geshe**, the Tibetan scholar-monk who earned
the doctorate. Deliberately the ONE Tibetan id among the temple's monks
(breaking the English-id convention as the list's single mythology touch;
the role IS the mythology). Sixth monk, seeded idempotently.

- **The method**: `research/METHOD.md` (the geshe's charter): FOCUS rules
  (topics enter only from instrument signals, never fashion; the three limits
  filter; comprobación designed BEFORE construction; one experiment per
  theme), the BUILD path (capture→design→run→draft→owner-publishes, one
  artifact per step; a negative result is a result), WRITE rules
  (claim→cited evidence, theirs-vs-ours always labeled, calm/no-slop,
  bilingual on publication), and per-artifact TEMPLATES (note / corpus doc /
  experiment log). Standing rule: the method is subject to the method.
- **Operative via the 3-layer skill pattern**: `research-method` SKILL.md
  installed and assigned to geshe in the seed (WORKER_SKILLS map); every
  geshe think loads the method.
- **Ownership moved**: `draft_research_note` scribe→geshe (research artifacts
  are the scholar's, docs remain the scribe's). resolve_worker verified live.
- **Lesson logged**: the 07-28 demo-brain "cleanup" had silently failed
  (`org remove` requires --id; the error panels were mistaken for success);
  the two per-project reviewer brains were actually removed today, verified
  by a zero-count in the tree. Panels are not verdicts; counts are.


## 2026-07-30 (noche); El corpus research/: las ideas de los links, hasta la base

Owner directive: the link-derived lines ALL belong to Lhassa's AI research;
extract each idea's core + its NESTED resources and build a `research/` corpus
with the full context down to the base. Executed: nested resources captured
via the owner's Chrome + curl/WebFetch; the t.co chains resolved, the
12-page graph-engineering PDF DOWNLOADED from Drive and read in full (it
self-declares as an independent synthesis of Anthropic's public cookbook; the
tweets over-attribute it; chain documented), the Memanto/jcode/toon/
curriculum repos fetched, no-ai-slop's local clone mined (its eval.md rubric
wasn't even in the tweet). Two login-walled X articles honestly marked
uncaptured.

Four parallel Sonnet writers produced 8 corpus documents (~2,000 lines) under
a fixed seven-section structure; idea / mechanics-down-to-the-base / the
AUTHORS' evidence (never presented as ours) / sources / code-verified mapping
to Lhassa with honest divergences / NOT-yet-verified-by-us + the experiment
that would / open questions; plus research/README.md (index + provenance
notes + how the corpus feeds `lhassa research draft`). Notable finds while
verifying: Lhassa already HAS an MCP client (tools/mcp_client.py; the gap is
an MCP server + A2A); our verifiers-are-code is STRONGER than the source
pattern's judge-model; six named increments from the PDF (entity
descriptions, typed entities, profiles, k-hop>1, graph-grounded evaluator,
monitoring signals) are now the graph line's backlog.


## 2026-07-30 (tarde); La línea de investigación del producto + la máquina de evidencia

Owner clarified the research structure: the five fields are the HOUSE's
(Witara); each product carries its own line. Lhassa's line declared in
`doc/research.md`: AI researched DOWN TO THE BASE (nothing adopted without a
measured comparison; the bench is the tribunal; every public claim cites an
instrument; reproducible on humble hardware; bilingual on publication),
processes optimized as a standing program, knowledge GENERATED as a documented
byproduct (the house principle made product-mechanical).

The evidence machine: `draft_research_note` (actionpack, scribe-owned,
temple-fronted, deliberate-not-scheduled); `gather_evidence` assembles a $0
pack with per-datum provenance (ledger spend, bench_runs, episode window,
loops, decisions excerpts), then ONE routine-model pass drafts a note that may
only state what the pack contains; no model → pack-only (the data is the
value). Drafts land in `doc/notes/drafts/` behind a review-before-publishing
header; the brain never publishes. CLI: `lhassa research draft|list`.

First live run: Lhassa drafted its own debut piece (earned-autonomy) via the
claude CLI at $0; and the method held: the draft says plainly that the
MECHANISM is sound but the RECORD is thin, names the redacted/missing data,
and proposes the next measurement (approvals→outcomes join). Comprobación
first, even against ourselves.


## 2026-07-30. Hardening from real use II + the notification voice

Owner-observed failures, root-caused from logs + episodes, all fixed same-day:

- **"I can't reach my model" was a LIE** (`9dde4bb`): deepseek V4 Flash's
  reasoning ate the 512-token chat cap → empty content WITH tokens billed → the
  code conflated it with unreachability (4× live, 16:27-16:30 on 07-29's chat).
  Now: cap 1024, ONE automatic retry at 3072 on empty-with-spend, and an honest
  `_EMPTY_REPLY` when even that comes back blank.
- **The /cv cascade** (same commit): `/cv <bare title>` woke six LIVE brains
  (LHASSA_ORG_LIVE=1 is baked into the poller unit since the pilot); the
  analyst rightly asked for the posting but its QUESTION cascaded downstream as
  the next brains' input, and the final generic error HID the useful ask. Now:
  a $0 preflight (<200 chars → ask for the full posting before any brain
  wakes), and a fileless flow run RELAYS the team's actual words. Bonus fix:
  `_parse_company`'s unbounded `@ tail` now stops at sentence boundaries.
- **Embeddings 400 → mixed vectors** (`earlier commit today`): an over-long
  input exceeded the embed model's context, 400'd, and that call's vectors
  silently degraded to hashing INSIDE a store of real ones. Inputs are now
  clipped to a hard bound before the remote call; a reindex purged the store.
- **The notification voice** (`notify/voice.py`, owner-requested): every
  outgoing notification passes ONE choke point (`dispatch.notify(kind=…)`) →
  `voice.revoice`: deterministic per-kind TEMPLATES ($0; "⚙️ check_spend:
  check_spend: X" → "Done. Check-spend: X"; reminders "You asked me to remind
  you: …"; alerts "Heads up: …"; approvals "Waiting on your word: …"; the
  digest passes through verbatim, already voiced) + an opt-in PERSONA pass
  (one cheap-model call, scribe-attributed episode, degrades to template; the
  message ALWAYS arrives; mirrors digest_style). `notify_style`
  (plain|template|persona, default template) is settings-editable. The
  deliver→notify seam forwards its kind so action-results and reminders arrive
  framed. Idempotent by prefix sentinel; nothing gets double-voiced.


## 2026-07-28 (tarde); El plan "Inteligencia": loops, context packs, grafo real, MoA, bench

Deep research over 10 owner-supplied sources (read IN FULL via the owner's
Chrome session (X blocks anonymous access) plus WebFetch): Anthropic's loop-
and graph-engineering material, Hermes /moa, Memanto, retrieval-as-code, the
ai-engineering-from-scratch curriculum, /no-ai-slop. Plan designed, approved,
and EXECUTED same-day: shared substrate first (migrations 7 `loops` + 8
`bench_runs`, `merge_kg_entities`, `LHASSA_KG_TRIPLES` flag), then five
parallel Sonnet waves on disjoint territories. Commits `a0c9a2f..633b111`.

- **Loops** (`614d536`, the flagship): the unit of work is a bounded,
  verifier-terminated LOOP. The forge ITERATES; a failing gate's detail (never
  a self-judgment) threads into the next authoring turn, max_turns default 3,
  'mejor'-no-baseline is non-retryable by design. `/goal --verify command:…|
  regex:… --turns N` gives standing goals a real termination: the deterministic
  verifier runs after each judgment turn; pass/cap stops the recurrence by
  clearing it on the SAME trigger the recurrence-mint already copies; feedback
  rides `params["_loop_feedback"]`, no new minting path. Caps fire BEFORE any
  model call. Every turn is an Episode.
- **Context packs** (`c74f2cd`): every rented-body run (/code) starts with
  `~/.lhassa/projects/<id>/context/{VISION,ARCHITECTURE,RULES,MEMORY}.md`
  generated $0 from the project row + facts + runs + pending work (MEMORY.md =
  TRIED/VERIFIED/OPEN, the loop-engineering ladder made file). Digest-header
  staleness (`source_digest` over project/fact/run timestamps) makes ensure()
  a no-op when fresh. The pack pointer is prepended to the CLI prompt; a
  missing pack never blocks a launch; context is a gift, not a gate.
- **Graph engineering** (`cf90dab`): opt-in (`LHASSA_KG_TRIPLES`) cheap-model
  triple extraction batched over NEW rows only; `asserts` edges with the
  predicate riding the provenance string (`fact:<id>|pred:<verb>`, schema-free
  by choice); hallucinated sources are dropped, not trusted. `kg_resolve`
  (weekly, guardian): exact-normalized merges need no model; fuzzy pairs are
  judged by ONE bounded completion or honestly skipped. Retrieval now cites
  kind + provenance in its `via` labels.
- **MoA** (`633b111`): `/moa` fans the same question to claude+codex+deepseek
  (missing CLI = honest ✗, <2 answers = no aggregation spend), one cheap-seat
  synthesis names real disagreement; `propose_tool(moa_verdict=True)` attaches
  an advisory panel review to the graduation approval; gates remain the only
  hard filter.
- **LhassaBench** (`bbc650e`): 10 self-contained fixtures scored ONLY by the
  existing objective verifiers (scorer-locked by test); `bench_runs` rows carry
  pass/metric/cost/duration; `lhassa bench run|history`. Router consumption of
  this data is a named follow-up, not silent scope.
- **/no-ai-slop** installed as a SKILL.md from the source repo.


## 2026-07-28. Six fronts in one day (projects, standing brains, platform, kg, TUI, TOON)

Deep research first (oh-my-hermes, TOON v3, graph-RAG 2026 consensus, Textual),
then a reuse-before-build inventory, then five parallel Sonnet subagent waves on
disjoint territories with the shared storage (migrations 4-6) pre-built by the
integrator. All owner decisions confirmed up front. Commits `f592e15..feb0494`.

- **Projects** (`f592e15`): the first-class typed workspace (`projects` table,
  Migration 4; CRUD + a deterministic resolver id>name>deepest-path;
  `entity_tag()`: scoping via `runs.entity_id="project:<id>"` +
  `artifacts.metadata.project_id`, NO new columns on existing tables). Alta by
  explicit CLI/chat AND a $0 detector sense that PROPOSES via a MUNDO-origin
  mint; the trust model itself parks the approval; detection never creates.
- **Standing brains** (`6bd6241`): `DEFAULT_KIND_TEMPLATES` (env-overridable),
  `template_is_read_only` (fail-closed), per-project brain namespacing
  (`code-review:<project_id>:reviewer`), the sense→mint→attach loop, and
  approval input (e): an attach parks UNLESS the template is provably
  read-only; risk computed UPSTREAM in the sense and stamped on params,
  because security (interface) may never import org (shell), even lazily.
  E2E-verified live: create code project → one beat → reviewers in the tree;
  second beat no-op; demo cleaned.
- **Platform** (`693312a`): `/api/projects/*`, dashboard Projects section, and
  the oh-my-hermes-inspired EVIDENCE VOCABULARY as a pure derived function
  (`memory/evidence.py`): prepared (intent, no outcome; the state we lacked a
  name for) / observed (episode exists) / verified (verifier passed or approval
  aprobada). Unknown shapes degrade to "prepared", the humblest claim.
- **Knowledge graph** (`75aaf44`): LightRAG-lite; `kg_entities`/`kg_edges`
  (Migration 6, closed vocabulary co_occurs/mentions/derived_from,
  relationships.py's validation discipline on SQLite), $0 incremental `kg_sync`
  (weekly, guardian-owned, temple-wrapped), fused retrieval (vector seeds +
  1-hop by edge weight, `via` hop labels) as the LOWEST-priority context layer
  behind `LHASSA_MEMORY_GRAPH` (OFF by default). Deliberately no multi-hop, no
  community summaries; the 2026 consensus and $0-first both say 1-hop is the
  increment that pays.
- **TUI** (`d178205`): `lhassa tui` (Textual 8.x); overview/projects/runs/
  approvals over DIRECT db reads (works with the API down); approvals render
  via `describe()` and resolve via the one approval seam (a/d keys,
  by_actor="tui"). Lazy-imported so `lhassa --help` stays fast.
- **TOON** (`feb0494`): built and SHELVED; the inventory showed no
  JSON-in-prompt anti-pattern to fix (the house hand-renders compact lines), so
  the encoder (52.8% of JSON's size on tabular data) sits unwired with its
  first consumer named (graph-expansion tables) and an explicit do-not-wire-
  speculatively note.
- **Test hermeticity hardened**: the whole suite pins
  `LHASSA_EMBEDDER_BACKEND=hash` in conftest; a live local Ollama must never
  change test outcomes or speed (69s → 12s on the affected set).


## 2026-07-24. The lean body: measured, then optimized (heartbeat, sentry shadow, one-command install)

- **The Rust question, answered with data first**: 7 days of production
  journalctl (997 beats) showed the brain awake 0.15% of the day; the wake
  paid ~336 ms of Python startup, dominated by httpx (122 ms, pulled at
  module level by the notify path even on beats that notify nothing) and
  rich.console (52 ms, dragged in by httpx). A full rewrite would optimize
  noise; surgery was chosen instead. Raw numbers live with the company
  drafts (witara: docs/drafts/latido-datos-crudos.md).
- **perf, beat path** (`52b0863`): httpx now imports lazily inside
  `notify/telegram.send()` and `actionpack/_net`; import tax 336 -> 215 ms.
  New `core/heartbeat_loop.run_loop()` gives the beat a resident `--loop`
  mode (imports paid once, clean SIGTERM/SIGINT, per-beat cost ~0 at any
  frequency); docker heartbeat role now execs it instead of respawning the
  interpreter each interval. Left alone on purpose: loguru (project-wide
  logging), core.db and config (real spine), dotenv via _curate (actionpack
  design). 2048 tests green.
- **lhassa-sentry, shadow only** (`e544b37`): the ear ported to Rust
  (sentry/ crate: signals from brain.db + digest/flows JSON, detect_events,
  fingerprints, importance gate; rusqlite + serde only, read-only, no
  network). 1.4 MB binary, ~3.9 MB RSS vs the 50 MB Python watcher. Parity
  by shadow harness (scripts/sentry-shadow.py): 21/21 ticks identical
  (10 real + 8 fixture-injected + 3 independent). Honest v1 gap in the
  code: agent_runs is in-process state no external process can observe, so
  run lifecycle events never fire from the sentry. The reaction (minting,
  trust, episodes) stays in Python. Cutover only after days of clean diff.
- **feat, install** (`4472047`): scripts/get-lhassa.sh, a one-command
  headless bootstrap over uv (installs uv if missing, clone or ff-pull,
  sync without the voice extra, `lhassa` shim in ~/.local/bin), verified
  in a clean simulated HOME including idempotent re-runs.
- **Known bug, filed not fixed**: a headless chat turn crashes on the
  unconditional pipecat import in `tools/schemas.py` (ModuleNotFoundError
  before any provider check). Deserves its own surgery: lazy or split
  schemas so the no-voice install can chat.

## 2026-07-21 (noche); Olas 2+3: la consciencia siente, la jerarquía sin deuda

- **2.1 adaptive thresholds** (`9010114`): `effective_threshold(base, population,
  floor=2)` = `max(floor, min(base, ceil(population/base)))`: full strictness
  only at population ≥ base²; a young brain fires on 3 repeats, a mature one
  stays strict; explicit args still win; findings carry `threshold_used`;
  cadence can relax to 2 occurrences, never 1.
- **2.2 feedback → autonomy**: `security/autonomy.apply_feedback`: ≥2 negative
  signals/window reset an accion's streak (like a denial); positive reinforces
  at most +1 and only where a human already started trust (streak ≥1); negative
  wins conflicts; high-risk + protected-root actions untouchable; idempotent per
  UTC day via watch_state. Attribution: signals name only responder episodes, so
  the nearest concrete accion within ±30min is used; unattributable → skipped,
  never guessed. Registered as the `apply_feedback` action (allowlist no-approval
  hygiene tier), daily in the maintenance seed, owned by the ORACLE monk.
- **2.3 forge drill**: `lhassa tools propose disk_report` ran the WHOLE pipe
  live. DeepSeek authored tool+test, quarantine (unshare) ran it, segura ✅,
  correcta ✅ (its test passed), mejor ✗ (no baseline). Deliberate outcome: the
  gate was NOT weakened; a net-new CLI proposal has nothing to prove "better"
  than; the first real graduation must come from the repetition detector, which
  knows the pattern's cost (the baseline). Pipeline proven, door stays strict.
- **3.1 deliver seam** (`0b8abf6`): chat action results go through
  `org/deliver.py` (kind="action-result", root hop logged; degrade to plain send
  only if the seam fails). Last work-product surface-direct send gone.
- **3.2 ORG_LIVE pilot**: one live delegation through `temple` answered with a
  real think and exposed a LEDGER BUG: the claude/codex CLIs report an
  API-equivalent cost estimate that was recorded as real spend ($0.57/hop).
  Fixed in the same commit: subscription CLI completions cost $0.0 BY DESIGN;
  tokens kept as observability.
- **Test hermeticity**: embedding tests pinned `embedder_backend="hash"`: a
  LIVE local Ollama must never change test outcomes.


## 2026-07-21 (tarde); Plan "kilometraje", Ola 0+1 (subagentes en paralelo)

`doc/plan.md` adopted: the audit showed the architecture is mature but the
learning loops lack MILEAGE; so waves 0-1 make existing loops live instead of
building new ones. Executed with four parallel Sonnet subagents on disjoint
file territories; two items turned out ALREADY BUILT by prior work (thread
continuity; `capabilities.is_followup`/`continue_followup`, commit 12c3e99;
knowledge context layer 6, commit 974d028) and were verified, not duplicated.

- **0.2 cache diagnostics** (`e34bf51`): `answer_cache.lookup(stats=…)` exposes
  best-similarity/scanned on every miss; the telegram think episode records
  `cache-miss best=0.83 scanned=12`: days of these decide whether the 0-hits
  problem is the 0.90 threshold, traffic, or the hashing embedder.
- **0.3 mileage telemetry** (same commit): the digest's "📈 My mileage" section
 ; thinks + cost, cache hit-rate, $0 work, real self-improvement findings;
  over 24h/7d windows. The evening digest answers "is it learning?" unasked.
- **1.1 real embeddings**: `RemoteEmbedder` speaks Ollama's native `/api/embed`
  (with `/api/embeddings` fallback); `get_embedder` gains an `embedder_backend`
  gate (auto/ollama/hash, defensive `getattr`); embedder `fingerprint` +
  `needs_reindex` guard against mixing hash- and real-vectors; `lhassa memory
  reindex` re-embeds facts + answer cache. The legacy default
  (`embeddings_model=nomic-embed-text` @ `embeddings_url` → Ollama's OpenAI
  surface) already works once the daemon is up, so defaults were kept.
  KNOWN GAP: `answer_cache` rows carry no model stamp; no live mismatch guard
  until a schema migration adds one; `reindex` brute-forces it meanwhile.
  Owner enable: `ollama serve` + `ollama pull nomic-embed-text` + `lhassa
  memory reindex`.


Running log of the architectural/operational decisions for the Lhassa "brain"
build. The source of truth for the architecture is `doc/lhassa-brief.md` (a
living doc; correct it after each phase per §4). This file records the choices
made along the way. Newest first.

## 2026-07-21. The chat knows itself (self-card, modality taxonomy, [[action: …]] muscle)

Driven by a real Telegram session: asked "what are you running on?" the brain
shrugged; asked how its own search worked it explained correctly only by luck;
asked to operate, it had no hands. Three fixes, all deterministic/$0, none
touching the tool-less invariant:

- **Self-card** (`notify/selfcard.py`): every chat think now carries a ~500-token
  block rendered live from `Config` + the registries; version, instance, the
  chat seat (provider/model), heartbeat seat, fallback chain, CLIs on PATH, and
  the registered action names split run-now vs approval-gated. Best-effort per
  line, never raises, never includes key material (tested).
- **Execution-modality taxonomy** (static half of the card): the model is told
  what ACTIONS, PTC scripts, API think, subscription CLIs, markers and org
  delegation ARE (distinct mechanisms with distinct cost/gate shapes) so it
  can explain itself and reason about which route fits a request.
- **`[[action: <name> key=value …]]` marker** (`telegram_inbound._apply_action_marker`):
  the third marker, mirroring research: chat stays tool-less; the marker mints a
  signed task (same HUMANO/telegram origin as `/goal`: allowlist + approval
  frontier unchanged), a no-approval action runs immediately via `run_task_now`
  and reports its episode `resultado` back over Telegram; approval-gated ones
  park with an honest "/approvals" ack. Unknown/refused actions get an honest
  line, never a silent drop. Acks are written only after the mint succeeded.

**Same day, follow-up; deterministic file hands + the protected tier + /config.**
"Reading a file needs no AI" made real: `actionpack/_files.py` adds `list_dir` /
`read_file`: read-only, $0, allowlisted without approval because the frontier is
the path, not the action: only inside `config.knowledge_roots`, hard ignore-list
(secret-shaped names) enforced even there, traversal resolved before the check,
chat-sized output caps. A SECOND tier, `LHASSA_PROTECTED_ROOTS`, is the owner's
"careful" zone: `security/approval.needs_approval` grew input (d); a file action
whose `path` resolves inside a protected root ALWAYS parks an approval (fail-closed
on any resolution error, never lowered by the autonomy knob; the tap is the
verification), and `describe()` now surfaces the `path` param so the owner sees
WHICH file they're approving. `/config` (Telegram) is the new CLI-parity door onto
the settings service (same EDITABLE allowlist/coercion/guards as `lhassa config` +
the panel; secrets masked, not settable); `knowledge_roots` and `protected_roots`
joined EDITABLE, so the owner can say `/config knowledge_roots ~/Documents` from
chat and the very next `[[action: read_file …]]` honors it; no restart, because
the set applies inside the poller process itself.

**And natural-language config**: `actionpack/_config.py:set_config`: the owner
says "agrega ~/Documents a tus carpetas de lectura", the model emits
`[[action: set_config key=knowledge_roots value=…]]`, and the action applies it
through `settings.set`: the SAME EDITABLE allowlist/coercion/guards as the
panel, `/config` and the CLI; secrets are unreachable by construction (not
EDITABLE); the result line always states old → new so the episode and the chat
follow-up show exactly what changed. No new marker machinery: the generic
`[[action:]]` seam + one instruction sentence + the self-card listing the keys.

Also today: **DeepSeek V4 Flash became the metered cloud seat** (replacing groq;
CLIs still first everywhere); provider, pricing, settings and voice branch in
commit `66d6d34`; live-smoked end to end. Note V4 Flash spends reasoning tokens
as output: a tiny `max_tokens` can yield an empty reply (512/768 budgets are fine).

## 2026-07-06. The brain that knows its owner (Olas 1+2: memory, one clock, remote hands)

Two waves of parallel subagents (one interrupted mid-flight by a session limit and
finished by hand; the salvage was committed incrementally so nothing was lost twice):

- **The chat remembers** (`1e72796`): rolling per-chat history threads into every
  Telegram think (multi-turn conversations keep their thread); every N turns the
  model may flag ONE durable owner fact with `[[remember: …]]` → a deduped `dato`
  in the grande. `lhassa onboard` (five questions → pinned datos + directivas) and
  `lhassa memory add` seed the founding facts. The grande stops being empty.
- **Instant research** (same commit): a no-approval chat-minted task now runs
  immediately via `core/dispatch.py:run_task_now`: the heartbeat's runner, early;
  a failure leaves the task pending so the beat keeps the promise. Research
  follow-ups arrive in seconds instead of minutes.
- **The detector proposes only the automatable** (`3d643b2`): judgments
  (`responder`, `dept:* think`, any think-decision) are excluded by predicate; a
  DENIED pattern is never re-proposed (a denial is a human decision); repeated
  mechanical action chains become `mint_script` proposals carrying a deterministic
  draft PTC script; the judgment prompt finally advertises the `script` key.
- **One clock** (`3639630`): reminders mint `send_reminder` tasks into the tasks
  table (heartbeat delivers via notify); the legacy 30s poller is a deprecation
  shim; a one-shot idempotent migration moved pending legacy rows (marked, never
  deleted); `/api/scheduled` reads the tasks table with the old response shape.
- **Parity batch 2** (`68a8441`): `/org`, `/fleet`, `/version` from chat.
- **Remote hands** (`bc2d1c8`): `/code <task>` parks a `code_run` approval with an
  inline Approve/Deny keyboard; one tap launches through the dashboard's own
  `_stream_run` seam (same AgentRun tracking), reports start + completion with the
  output tail. Write access to the owner's files NEVER runs without the tap.

Live validations: a reminder traveled mint → tasks table → runner → notification;
the detector's five real denied shapes are pinned as regression fixtures.

## 2026-07-06. Personal secretary layer (digest check-ins, agenda, today/tomorrow)

Extended the deterministic digest from "Lhassa reports itself" into "Lhassa also
checks in with the owner" without adding token spend. `LHASSA_DIGEST_PERSONAL_CHECKIN`
adds a $0 personal agenda + morning/evening check-in; `send_digest` parks an
`owner_prompts` row for six hours, and the Telegram poller captures the next plain
reply as a daily check-in fact (`personal_checkin` / `daily_checkin`) before it
falls through to brain chat. Explicit `/checkin <text>` remains available. The
same reusable `personal/agenda.py` powers `/today`, `/tomorrow`, digest agenda
sections and the dashboard Overview's **Today with you** card. The core choice:
personal memory starts append-only in the grande as facts, with a small
`owner_prompts` table only for pending questions; this keeps auditability and
avoids silently treating all chat as state. The `personal` seed formalizes the
hierarchy: a stable `personal` department under Lhassa, with separate
`personal-morning-briefing` and `personal-evening-briefing` flows so the order of
execution can vary by time of day without changing department identity.

## 2026-07-05. The narrative: flag, pillars, voice book, bilingual README

Studied how Hermes *tells* its story (one hammered claim, features as promises,
pain-point-titled sections, user-stories social proof) and built Lhassa's own
narrative; the craft, not the form. Decisions: audience is **public from day one
with the owner as first user** (honest claims only); **bilingual** (README.md EN +
README.es.md); the flag is **"Build the brain, rent the body"** with the owner's
priorities leading the pillars ($0 routine day → subscriptions before APIs → it
forges its own hands → sovereign → no lock-in → 1..N org); tone between
**mítico-sobrio and cálido-personal** with the Tibetan imagery governed by explicit
rules (one touch per section, never replacing a technical noun, homage not doctrine).
Artifacts: `doc/narrative.md` (the voice book; source of truth for all public text),
rewritten `README.md` + new `README.es.md` (identity head, pillars table with the
real ledger as proof. Jul 4: ~720 wake-ups, 5 thinks, $0.0031 —, "a day in the
life" told tick by tick, the gates as a rite of passage), `assets/lhassa-mark.svg`
(the stupa extracted from the dashboard). Unique narrative devices claimed: the
day-in-the-life ticker, the ledger-as-proof, the gates-as-pilgrimage.

## 2026-07-05. Operative-UX pass + the Hermes study (recall / curator / PTC / portability)

A day of operator-driven fixes followed by a deliberate competitive study of Nous
Research's **Hermes Agent** (cloned to `~/Code/hermes-agent`, full architecture map in
the session log). Verdict recorded: Hermes validates the thesis from the opposite side;
it built the most complete *body* (20+ surfaces, packaging, tools) with no deterministic
$0 plane and OS-only security; Lhassa owns the *nervous system* (do-and-sleep, allowlist/
quarantine/gates, the org). Three Hermes ideas were adopted, made Lhassa-shaped, and
implemented by four parallel Sonnet subagents with disjoint file territories:

- **Recall (FTS5 episodic memory)**: `episodes_fts` external-content index + triggers +
  backfill in `core/db.py` (defensive; a build without FTS5 degrades); `search_episodes`
  (BM25, hostile-input-safe, automation episodes *demoted* not hidden. Hermes' recall-
  blindness lesson); `GET /api/episodes/search`; Telegram `/recall`; and a 5th, lowest-
  priority context layer in `memory/load.py` so a think's `query` pulls the brain's own
  past experiences into context ($0, truncates first).
- **Skills curator** (`skills/curator.py`): usage telemetry in an atomic `.usage.json`
  sidecar (bumped on skill read, best-effort); deterministic $0 lifecycle active→stale
  (`LHASSA_SKILL_STALE_DAYS` 30) →archived (`LHASSA_SKILL_ARCHIVE_DAYS` 90) with tar.gz
  backup first, move to `.archive/`, NEVER delete, pinned exempt, `restore()`; weekly
  `curate_skills` action in the `maintenance` seed, skipped while an agent run is active.
- **PTC. Programmatic Tool Calling** (`agents/ptc.py` + the `run_script` action): one
  LLM turn writes a Python script that orchestrates N deterministic ACTIONS over a
  JSON-lines Unix-socket RPC; the script runs inside the quarantine sandbox (AF_UNIX
  survives the network namespace; proven in a real `unshare -rn` test) and the PARENT
  dispatches every call through the signed-Task + allowlist seam (unknown/disallowed/
  needs-approval → refused, never run). Caps: 300s, 50 calls, 50KB/10KB output.
  `run_script` itself is allowlisted with `requires_approval=True` (run_command-class risk).
- **Portability**: `doc/portability.md` audit (most couplings already degrade; real gaps:
  headless CLI-subscription auth, no voice-less extra in pyproject); docker-compose gained
  the missing `monitor` role. Standing constraint recorded: laptop today, VPS tomorrow;
  no laptop-only couplings in new work.

Earlier the same day (operator-driven fixes, same session):

- **Telegram was dead end-to-end**: `lhassa-telegram.service` lacked `~/.local/bin` on
  PATH (claude CLI invisible → silent chain skip) while `settings.env` pinned
  `LHASSA_MODEL_CHEAP=claude-sonnet-4-6`, poisoning the Groq gateway link (404). Fixed:
  PATH added to the telegram/heartbeat/monitor units; stale model overrides removed;
  `distill_memory` (memory/consolidate) now resolves its provider via
  `router.pick_provider(hard=False)` (the heartbeat seat) instead of hardwiring the
  gateway.
- **Chat-speed seat split**: new `LHASSA_TELEGRAM_PROVIDER` (settings-editable) pins the
  Telegram CHAT door's provider (set to `default`/Groq; ~1.5s replies vs ~9s cold CLI
  boot) without touching what flows/hard work think with; fallback chain unchanged.
- **Monitor signal hygiene**: `_failures_from_episodes` no longer counts the monitor's
  own episodes ("alerting on its own reflection") nor prose of successful thinks; a
  freshness window (`LHASSA_MONITOR_FAILURE_WINDOW_H`, 24h) ages failures out; watcher
  alerts diff by episode *identity* (not count) and NAME what is new; no more
  "(total N)" reading as if the whole backlog just failed.
- **Digest personality**: template rewritten in Lhassa's first-person voice (still $0,
  local-timezone times, failures named); optional `digest_style=persona` re-voices it
  through the cheap gateway model (`digest_language`, degrades to plain; the digest
  always arrives).
- **Approvals became legible**: one deterministic `security/approval.describe(row)` →
  `{title, detail}` rendered by /approvals (Telegram), the dashboard queue/Inbox, and
  the digest; "Make 'responder' automatic · repeated ×41 · $0.26 spent" instead of ids.

## 2026-07-03. Intelligence pass: deterministic-first brain across four fronts (Etapas 1-4)

Full-brain study (6 parallel subagent audits: core loop, LLM/cost, memory, org/flows,
Telegram, self-improvement) found the recurring pattern: the pieces existed, individually
tested, but were never wired into the hot path. Four stages implemented in parallel
(1 in-tree + 3 isolated worktrees), merged in order:

- **Etapa 1; reflexes + spend judgment** (`8629e75`): `core/policy.py`, a deterministic
  REFLEXES registry between watchlist and judgment (job_review auto-triage, repo_changed →
  sync_repo for `LHASSA_AUTO_SYNC_REPOS`, health warn → notify; default think/minimal).
  Tiered context in `_judge` (minimal=600tk/full=2000tk; `query` + `active_tags` finally fed
  so tag/semantic layers fire). Deferred tasks now retry with backoff (`sweep_deferred_tasks`,
  max 3 then rechazada). `fallback_chain` moved to `llm/router.py` and walked by the heartbeat
  on ProviderNotConfigured/BudgetExceeded ($0 CLI subs first). `gateway.spend_summary()` +
  soft budget pct (default 0.8) downgrade away from the smart model; `check_spend` action.
- **Etapa 2; operative day via Telegram** (`3d40489`): deterministic $0 digest
  (`notify/digest.py` + `send_digest` action + `digest` seed at 08:00/21:00);
  parse_mode/inline keyboards in the Telegram channel; `/approvals` `/approve` `/deny`
  commands + callback-button approvals on watcher alerts; `/watch` `/watches` `/unwatch`
  `/goal` author watch rules and recurring goals from chat; health notifications routed
  through the channel registry (one precise new layer exception, documented).
- **Etapa 3; real delegation** (`f34ea94`): the judgment `delegate` key now mints `org_run`
  tasks through the mint_task security path (cap 3); `org_run` action lazily runs
  `run_goal` (offline/$0 unless LHASSA_ORG_LIVE); monitor findings become minted work under
  `LHASSA_MONITOR_DELEGATE` (fingerprint-deduped); coordinator fans out to top-3 departments
  on ties/explicit multi with offline deterministic synthesis (live = one extra think);
  org inbox drained via a new extensible watchlist sense (`watchlist.register_check` +
  `org/inbox_sense.py`: core stays org-unaware, no new layer exception).
- **Etapa 4; visible self-improvement** (`3da06c4`): `agents/repetition.py` ($0 SQL
  detector of repeated costly thinks → parks a `mint_action` approval + notifies, deduped
  via watch_state); `agents/author.py` (routine-model tool authoring →
  `create_candidate` → gates → `tool_graduation` approval; `lhassa tools propose`);
  `/api/tools/{n}/graduate` now refuses ungated tools (409); `review_tools` action applies
  should_retire/retire; `new_facts` dispatch dedup; `maintenance` seed (distill_memory +
  detect_repetition daily, review_tools weekly).

1333 tests green, lint clean, architecture guard intact. Known gaps left on purpose:
`bump_tool_usage` still has no real execution seam (no self-written tool executes yet);
coordinator fan-out is single-level per hop. CORRECTION to the audit: legacy
`memory/store.py`/`session.py` is NOT orphaned; it still powers `/api/scheduled`,
`/api/memory` reads, `scheduler.py` reminders, the voice pipeline and the schedule
tool handlers; retiring it needs a real migration onto the grande, not a deletion.

## 2026-07-02. Follow-up: kill the api/misc accumulator + make the ccusage dashboard read fast & real

Two small, behavior-preserving finishes to the refactor pass, both surfaced by a second review
+ live smoke (the automated tests + e2e mock these paths, so only a real `curl` caught them).

- **No more `misc` accumulator.** Finishing the server.py split had left an `api/misc_routes.py`
  (464 lines) bundling 7 unrelated singletons. Split into `activity_routes` (agents/scheduled/
  audit), `reports_routes` (reports/projections), `fleet_routes` (fleet), and `overview_routes`
  (`/api/overview` + the `build_overview` aggregate + the 11 `_overview_*` helpers); `misc_routes`
  deleted; server.py's `build_overview` re-export repointed to `overview_routes`; the overview
  Config-module-namespace test seam preserved. 1071 tests unchanged, all moved routes smoke-200
  live. `server.py` is now a clean integrator with per-domain routers and no catch-all. (`65766a8`)

- **ccusage: from a 20-30s dashboard hang to real data in ~3.7s.** A live smoke found `/api/
  projections` hanging ~20-30s: `read_ccusage` applied its timeout PER candidate command and tried
  a slow `npx ccusage@latest` cold-start, and (the real culprit) ccusage was **fetching model
  pricing over the network on every run**. Fixes, in layers: (1) `read_ccusage` timeout is now a
  TOTAL budget (a deadline across candidates), never ~2× (`3909d37`); (2) an `allow_npx=False` the
  dashboard auto-load callers pass, so a missing binary degrades in ~5ms instead of a cold npx
  download (`552281b`); (3) the ccusage commands run **`--offline`** (cached pricing), which drops
  a full run from 20s+ to ~3.7s (`8abe74c`). The `ccusage` binary is installed **user-level**
  (`npm install -g --prefix ~/.local ccusage` → `~/.local/bin`, on the app service's PATH); with
  it present the dashboard shows genuine cross-harness spend (`available: true`, real
  `total_cost_usd`), and stays instant-degrade when absent. Lesson logged: **live smoke earns its
  keep**: the test/e2e mocks never exercised the real CLI.

## 2026-07-02. Reviewer-driven refactor & reproducibility pass: split the giants, seed registry, e2e net

A hygiene arc (no new features): make the codebase reproducible on a fresh machine, shrink the
biggest modules into thin compositions, and add an automated frontend safety net; all
**behavior-preserving** (proven by an unchanged **1071-test** backend suite + a green e2e +
per-panel live browser verification). Driven by a reviewer's prioritised list; the guiding rule
throughout: pure structural moves, gates green after each step, one small committable slice at a
time.

**Reproducibility hardening.** `.env.example` gained the ~22 missing knobs (telegram, org-live,
heartbeat/claude/CLI providers, CV dir/flow/tectonic, monitor_*), grouped + commented with
defaults; `scripts/install.sh` and the `Makefile` now install + enable the `lhassa-monitor` and
`lhassa-health` units too (all units use `%h`, no hardcoded paths); README + `doc/install.md`
document `uv`/`node`/`tectonic` (the last optional. CV→PDF else `.tex` fallback). (`c324c17`,
`d4e1bd5`)

**Seed registry (elastic bootstrap).** `lhassa/seeds/`: a `Seed` unit + a `SEEDS` Registry
(`@register_seed`, plugins-discoverable like the other swap points) with `list_seeds`/`apply_seed`/
`apply_all` and `lhassa seed list|apply <name>|apply-all`. The `cv` seed reproduces a fresh env's
interns department + 6 worker brains + the monitor role + the canonical `flow-zvzpry` (captured
verbatim from the live definition), idempotent + non-destructive. Future pipelines drop in as new
seeds. So `lhassa seed apply cv` bootstraps the whole CV pipeline on a clean machine; the owner
only supplies their real `cv_largo.tex`. (`083f81a`)

**Split the giants (behavior-preserving, each browser- or smoke-verified).** The recurring shape:
extract cohesive pieces into folder-neighbour sub-modules, leave the original as a thin composition
that re-exports / lazy-imports EXACTLY as before, prove it with the unchanged suite + a live check.
- `api/server.py` **1464 → 227** lines, a pure integrator (app + auth middleware + CORS + `/ws` +
  static + `run_api_server` + 13 `include_router`). Two passes: runtime/llm/approval/checkin
  (`1cceb2a`), then skills/org/memory/config/tools/misc (`7163d8c`); 0 inline HTTP routes left,
  all moved routes smoke-200 live, test-monkeypatch seams preserved (overview reads Config via the
  module namespace; export/import + `build_overview` re-exported from `server` for the existing
  test/CLI importers).
- `tools/registry.py` **981 → 128**: `TOOLS` → `tools/schemas.py`, handlers → `tools/handlers/{web,
  shell,files,agents,schedule,outlook,github,vision,monitor,jobs}.py` + `_shared`; `registry.py`
  re-exports every previously-importable symbol via `__all__` so callers/tests are untouched. The
  one architecture `ALLOWED_EXCEPTIONS` (voice.notifier edge) moved to `handlers/agents.py`.
  (`88adff6`)
- Dashboard: `App.jsx` **420 → 124** (hooks `useWebSocketEvents`/`useSectionRouting` + layout
  `NavRail`/`MobileNav`/`PanelShell` + `lib/sections.js`; the WS reconnect + hash routing byte-for-
  byte, `60f8dba`); then the three big React-Flow/feature panels, each **live-verified in Chrome**
  before commit: `OrgFlow` **915 → 372** (`b968db0`: render, click→inspector, dept-box drag),
  `WorkflowEditor` **944 → 387** (`8ecdf35`: deep-link open, vertical layout, HYBRID inspector),
  `BrainsPanel` **1399 → 151** (`47bc2e8`: inspector incl. skills+Undo/limits/monitor role, create
  form, Flow/Tree/Templates switch). Every lazy-import contract + toast call preserved verbatim.

**Automated frontend net.** Ran the previously-unrun Playwright e2e (browsers already installed);
it immediately earned its keep; a stale assertion failed because the toast system now also surfaces
the "Import complete" message (role=status) alongside the panel copy (role=main), so an unscoped
`getByText` matched two nodes. Scoped the two assertions to `role=main`; **3 e2e green**, which also
confirms the dashboard survives all the panel refactors. (`c834fe6`)

**Verdict + remaining debt.** The reviewer's "dominant risk" (`server.py`) is resolved; tools and
the original big panels are no longer concentration points. What's left is **normal growth debt, to
split on friction, not preemptively**: `OverviewPanel` (668), `SkillsPanel` (534), `SettingsPanel`
(474), `AgenticPanel` (478), `index.css` (899). `useBrainsData` (481) is a cohesive data hook; kept
whole on purpose. `[SUPUESTO]`: the panel splits were verified by manual browser click-through +
build/lint + the 3 e2e; broader automated panel coverage would let future frontend refactors skip
the manual pass.

## 2026-07-01. Live CV pipeline over Telegram, a governed-flow hierarchy & the constant monitor role

The CV work-flow went from an offline skeleton to a real, live, gate-guarded product a person
drives from Telegram; and, from that concrete case, two STANDING architectural principles were
established: **every flow runs through the org hierarchy**, and **"monitor" is a first-class,
constant role**. All shipped green (final: **1061 tests**, ruff clean) in focused commits.

**Live CV pipeline (real output).** Running `flow-zvzpry` live surfaced real defects, each fixed:
- **Per-task token budget**: the org think was capped at 512 output tokens, truncating the CV.
  A task node's optional `max_tokens` (free-form config) now threads engine → executor →
  `provider.complete`; the CV tasks get room (finalizer 8000, cv-writer 6000). (`19e84dc`)
- **`cv.clean_latex()`** strips a model's prose/```latex fences → a compile-ready `.tex`. (`19e84dc`)
- **Claude CLI provider** was handed the router's default (a groq model name) as `--model` and
  404'd, so `heartbeat_provider=claude` silently fell back to groq. Now it only forwards a Claude
  model/alias (claude*/opus/sonnet/haiku), else `claude_model` / the CLI default; new
  `LHASSA_CLAUDE_MODEL` / `LHASSA_CLAUDE_BIN`; `_CLI_TIMEOUT` is configurable
  (`LHASSA_CLI_TIMEOUT`, default 300s) because a full-page generation runs past two minutes. (`df9d8f3`)
- Result: a complete, valid 2-page LaTeX CV tailored to a role via **claude · sonnet-4.6** (run
  offline-forced through the CLI, no groq TPM cap). Length rule tightened (aim ~1.5 pages, hard max 2).
- **Contact-preservation gate (an objective, LLM-free árbitro)**: a live run had the model INVENT
  the candidate's email + phone despite a "do not invent" prompt. `agents/verify.py` gains a
  `contact_preserved` verifier: every email + phone (formatting-agnostic) in the base CV must appear
  verbatim in the output, else the gate FAILS and the path stops before export. The engine threads
  `base_cv` into a gate's verify `context`; the CV flow's final gate runs it. So a tailored CV can
  never ship altered contact details. (`7872321`)

**Telegram as the CV surface.** `notify/telegram_inbound.py` bridges a chat to the flow:
- `cv/extract.py` `extract_text` (reads `.tex`/`.txt`, extracts `.pdf` via **pypdf**); `run_flow`
  gained a `base_cv` override; `notify/telegram_api.py` getFile/download + `sendDocument`. (`287dd75`)
- The base CV lives on disk (`~/.lhassa/cv/cv_largo.tex`) and rarely changes, so the frequent action
  is a **command that needs only the job posting**, and changing the base is explicit; **no
  per-upload button prompts** (a design the owner iterated to): `/cv <posting>` (acks, tailors the
  on-disk base, replies with the **compiled PDF** via `cv.compile_pdf`/tectonic, `.tex` fallback);
  `/cvnewbase` (arms the chat; the next uploaded file replaces the base, backing up the old);
  `/cvbase` (shows the base on file). A caption-less document with no arm → a one-off tailor
  (hold, then the follow-up text is the posting). Commands publish to the bot `/` menu. New config
  `LHASSA_TECTONIC_BIN`. Owner-gated, never raises into the poll loop. (`2aaf92e`, `f97ec50`)
- The poller runs in LIVE CV mode via service env (`LHASSA_ORG_LIVE=1` + claude sonnet-4.6). (`b97b7fc`)

**PRINCIPLE 1; every flow runs through the hierarchy (`org/delegate.py`, `352cf9f`).** A flow is
no longer run bare. `delegate_flow(flow, …)` resolves the flow's **owning department** (pinned-brain
→ department table lookup; interns for the CV flow), walks the command path `lhassa → general →
interns`, logs one **deterministic, $0, never-LLM** `org-delegate` Episode, then runs the flow (lazy
import; no org↔flows cycle). It **never blocks the work**: a topology failure degrades to a direct
run. The Telegram bridge and `POST /api/flows/{id}/run` both call it (run_flow stays the internal
engine); this is the standing pattern for ALL flows, current and future. The hierarchy hops are
deterministic on purpose; governance + a full audit trail at **no extra token cost**; the only spend
stays the flow's pinned worker brains.

**PRINCIPLE 2; "monitor" is a first-class, constant role.**
- `org/monitor.py` (`54c5bd1`): a reusable capability; `gather_signals()` (read-only, $0: recent
  episodes, health digest, flows, approvals, failures) + `monitor_pass()` → `{observations, risks,
  improvements, delegations}` (offline stub $0; live reasons via `brain_executor`). `make_brain_monitor()`
  makes ANY brain a monitor; applied to the interns brain (role → `monitor`; "everyone reports to it").
  `lhassa org monitor` CLI.
- `org/watcher.py` (`941353c`): the always-on **$0 "ear"**. `detect_events()` diffs two `gather_signals`
  snapshots (new failures / health-worsened / new approvals / run start+finish; transitions only);
  `watch_loop()` has an **elastic cadence**: idle `LHASSA_MONITOR_IDLE_INTERVAL` (default **300s**),
  active (something running, via `state.agent_runs`) `LHASSA_MONITOR_ACTIVE_INTERVAL` (default **15s**);
  `react()` ALWAYS logs a monitor Episode and pushes a Telegram alert only on IMPORTANT events
  (failures / health-alert / approvals), **de-duped** so a standing condition alerts on the transition,
  not every tick. Idle is $0; a token is only ever spent under `monitor_think` + `LHASSA_ORG_LIVE`.
  Config `LHASSA_MONITOR_*` (enabled/idle/active/department/notify/think, enabled default OFF) +
  `scripts/systemd/lhassa-monitor.service` (opt-in). Because the delegation + flow + org paths all
  write to the same episode/signal plane the watcher reads, the monitor sees the WHOLE hierarchy.

**Together**: a flow fired from anywhere → Lhassa delegates down the hierarchy (episode) → the owning
department runs it (episodes per brain) → the constant, cheap monitor watches that trail and reacts
only when something happens. Sovereignty + observability + cost-awareness in one loop.

**`[SUPUESTO]` / to enable:** the monitor service is shipped but NOT enabled (owner runs
`systemctl --user enable --now lhassa-monitor.service`). A live monitor *think* needs
`LHASSA_MONITOR_THINK=1` + `LHASSA_ORG_LIVE=1`; default is $0. Company auto-naming from prose isn't
wired (tag `@ Company` / `company: X`); Telegram's ~4096-char single-message limit bounds a pasted
posting. Saving a PDF as the base stores its extracted text (a `.tex` keeps real LaTeX).

## 2026-07-01. Dashboard as a product: Matte Obsidian redesign, usability pass, CV work-flow & run control

A long UX/product arc turning the dashboard into a real command surface, plus two backend
capabilities. All shipped green (final: **991 tests**, ruff clean, dashboard build + lint clean)
and committed in focused commits on top of the aiosqlite-vendor / API-auth hardening.

- **Vendoring + API hardening (`a27e94f`):** the local `aiosqlite` shim (a sqlite3 wrapper that
  avoids the upstream worker-thread stalls here) moved from a root `aiosqlite/` package that
  **shadowed the real dependency** to `lhassa/_vendor/aiosqlite.py` with explicit imports; plus a
  require-local-or-token API middleware (non-local `/api` needs `LHASSA_API_TOKEN`; loopback allowed)
  + CORS restriction, and a `conftest` `ASGISyncTestClient` (Starlette's sync TestClient hangs here).
- **Visual; "Matte Obsidian" (`7f667a5`):** matured the dark theme to flat matte surfaces (no
  gradients/glow/blur), hairline borders, an editorial **Fraunces** serif for display, **gold-led
  with the monk-red as a real co-accent** (finial, active-nav tick, admin ring); stripped the
  decorative sidebar chrome. Inspiration: quiet-luxury (AP/Cartier) restraint, not more chrome.
- **Layout & responsive:** bigger fluid org-flow canvas; the brain inspector is now a dismissible
  **popup** (right dock desktop / bottom sheet mobile); a whole-page **responsive** pass (hamburger
  drawer, stacking grids, touch sizing) toward mobile use. React-Flow canvases are **lazy-loaded**,
  cutting the initial bundle ~513 → ~312 kB.
- **Usability pass (12 findings from a live click-through):** section **deep-linking** via the URL
  hash incl. `#flows/<id>` (Back/Forward) + section persistence; a **global toast** system; a **⌘K
  command palette**; two-step delete confirms; discoverable reparenting (right-click "Move under…");
  empty-state CTAs; client-side filters on dense lists; palette-token contrast fixes; flow-editor
  **auto-wire + disconnected-node warnings** and a vertical "Tidy" layout; "Create & add another"
  for brains; focus-refetch of the org canvas; auto-fit that doesn't hide fresh nodes; fixed the
  long-standing WebSocket reconnect lint bug (handleEvent `useCallback` + `connectRef`). Renamed the
  Spanish specialist display name `optimizador` → **`optimizer`** (`30535bf`).
- **Draggable department blocks (`7f667a5`):** dept boxes are now draggable and carry their brains
  as a unit (per-department offset persisted to `localStorage`, with a Reset control), **without
  breaking reparent-by-drag** (offset keeps member nodes on absolute coords).
- **`#10` optimistic + Undo (`8c3a0c7`):** brain move / leaf-delete / flow-delete / skill-remove
  apply instantly with an **Undo toast** that reconstructs the prior state (rollback on failure);
  a subtree/force delete stays two-step-confirmed and non-undoable.
- **`#11` run control (`26835d1`):** the agentic Run surface can now **Stop** a running task (track
  by id + process group, SIGTERM→grace→SIGKILL via `local_cli.stop_run`, idempotent), shows a
  **status history** (`running/done/failed/stopped`) and **Copy output**. New `POST
  /api/code/{run_id}/stop`; `AgentRun` gained `cli` + a `stopped` status.
- **CV work-flow: base-CV input + `.tex` export (`e84ed02`):** new modular file-I/O seam
  **`lhassa/cv/`** (config `LHASSA_CV_DIR`, default `~/.lhassa/cv`; `load_base`, `slug_company`,
  `short_id`, atomic `export` → `out/{id}_{company}.tex`). The flow engine injects `{base_cv}` +
  `{company}` into task prompts and gained an **`output` type `"tex"`** that writes the artifact;
  `resolve_company` = run-override → context → `untitled`; `POST /api/flows/{id}/run` accepts an
  optional `company`. The "Impossible-to-reject CV" flow (`flow-zvzpry`, in `~/.lhassa/flows.json`)
  now builds on `cv_largo.tex`, tailors to **≤2 pages/role preserving structure**, and exports a
  `.tex`. Offline dry-run ($0) verified end-to-end (writes real files with stub content); a REAL
  optimized `.tex` needs a live run (`LHASSA_ORG_LIVE=1` + a model) over the user's real base CV.

**Dogfooding:** built the whole thing as a new user via the live UI + API; created an **Interns**
department + a 6-brain CV pipeline (jd-analyst → researcher → cv-writer → ats-evaluator → gate →
critic → finalizer → gate → output) and validated the flow offline, which is what surfaced the 12
usability findings above. **`[SUPUESTO]`:** org/flow live-update is focus-refetch, not a WS push;
run tracking is in-memory (not restart-persistent); the flow-editor deep-link and drag/touch feel
were verified by construction, not on a real mobile device.

## 2026-06-30. Elastic & modular hardening pass (A–E) + Telegram live + org topology

**Owner principle (now a standing constraint):** Lhassa must be **fully elastic and highly
modular**: every swap point behind an interface + registry, capabilities **drop-in** (add a
module without editing core), and the org layer must run **1 brain OR N brains** with a
**user-defined hierarchy**. Done as 5 slices + config surface (`org_departments`,
`notify_channels`, `plugins_dir`, `org_topology_file`):
- **A (keystone)** `registry.py` (`Registry[T]`: register as call/decorator, get/get_or_none/
  NotRegistered, as_mapping live view, duplicate-protection) + `plugins.py` (`load_plugins`
  imports from `plugins_dir` AND the `lhassa.plugins` entry-point group; self-registration with
  no core edit; idempotent, never raises). Adopted in `core/actions.py` (public API preserved;
  `ACTIONS` is now a live read-only view; `register_action` drop-in seam).
- **B (channels)** `notify/channels.py`: `Channel` protocol + registry (`@register_channel`);
  `TelegramChannel`/`DesktopChannel`/`LogChannel`; `dispatch.notify()` fans out over
  `active_channels(config)` from `notify_channels`. **Fixed the Telegram inbound**: answers with a
  tool-less brain think (memory + routine opencode provider, `despertar_por='telegram'`) instead
  of the 26-tool GROQ agent that emitted malformed tool calls (`tool_use_failed` 400s).
- **B2 (commands)** `notify/commands.py`: Registry-backed slash-command handlers
  (`@register_command` drop-in): `/start`, `/help` (lists registered commands live), `/status`
  (provider+model+health+today's spend, **LLM-free**). Inbound dispatches commands before the brain.
- **C (boundaries)** `tests/test_architecture.py`: an `ast`-based guard (no new dependency)
  forbidding layer-crossing imports (spine/interfaces/adapters ↛ shells; harness ⟂ llm; keystone
  imports no feature module), rules measured from the real graph, with self-tests proving teeth.
  `doc/architecture-layers.md` documents the layers.
- **E (org topology)** `org/topology.py`: `BrainNode`/`Topology` (validated tree) + `load/save`,
  config-driven (`org_topology_file`), **standalone 1 brain up to arbitrary depth**;
  `coordinator.run_goal()` walks the tree (typed TASK_ASSIGN down, **only leaves execute**, result
  bubbles up; RBAC by coordinate-vs-execute so new roles need no RBAC edit); `lhassa org`
  tree/list/add/remove/route/run to create & manage the chart. `GeneralBrain`/`DepartmentBrain`
  unchanged.

**Telegram (live):** new bot **@Lhassa_bot** (old one revoked), token + chat id in `.env`
(gitignored); outbound verified (real delivery); inbound answers via opencode (no tool errors);
bot name/description/short-description/commands set in **English** via the Bot API; a
`scripts/systemd/lhassa-telegram.service` user unit is ready to enable. **Avatar** must be set via
@BotFather `/setuserpic` (not exposable via the Bot API).

**Verified:** **718 tests** (full suite ×2, stable; the prior telegram test-isolation flake is
gone), ruff clean, `lhassa --help` lists `org`, dashboard builds. **Live smokes:** a 3-level org
chart routed goals to the correct leaves (coordinators never executed); a single-brain chart
executed directly; `/help` + `/status` rendered offline; an inbound poll answered a real Telegram
message via opencode at $0. **Backlog / `[SUPUESTO]`:** adopt the registry in the remaining
registries (providers/harnesses/verifiers/specialists) as they're touched (continuous); `/status`
reads the gateway ledger path/schema directly; route ties break by child declaration order.

## 2026-06-30. Agentic run + typed relationships + work-flows + chat-via-CLI (+ docs)

A large feature arc, all committed and live, full suite green (974 tests). Backends were built as
self-contained NEW modules + APIRouters (no server.py contention) and mounted at integration.
- **Chat via local CLIs + `lhassa code`**: the chat (CLI/voice/agent) can run through claude/codex/
  opencode; the owner's subscription, NO API key (`llm_provider=claude` → `run_once` routes to the
  CLI ModelProvider, tool-less; honors an explicit Chat model). The Settings panel gained **Chat
  provider + Chat model** (a separate seat from the heartbeat) and now REJECTS an API provider with
  no key (the bug that broke chat). NEW `lhassa code "<task>"` runs an AGENTIC task through the CLI
  (edits files in the cwd) and **streams live** (a `LocalCliAgent.on_line` sink).
- **Agentic run surface** (`9284651`/`8b82b90`): `POST /api/code` launches a CLI run streamed over
  the existing WebSocket; the dashboard **Run** section watches it live; `lhassa-app.service` gained
  `~/.local/bin` on PATH so it finds the claude CLI.
- **Typed org RELATIONSHIPS** (`b52c1c2`/`dad07d0`): an overlay graph on the command tree;
  `reads`/`notifies`/`reviews` (directed) + `collaborates` (symmetric); `permits()` runtime seam;
  `/api/org/relationships`; the Brains canvas draws dashed per-kind edges (legend + toggles) and the
  inspector adds/removes them.
- **Work-FLOWS** (`998c834`/`d90f5e1`): `lhassa/flows/` model+store+engine (`trigger→task→gate→
  output`+branch DAG; **hybrid** task routing; pin a brain else auto-route by skill; data threads
  along edges; offline/$0 unless `LHASSA_ORG_LIVE`) + `/api/flows` + an n8n-style **Flows** editor.
  Live smoke: create→run (`completed`, trigger✓→task✓→output✓)→delete.
- **English seed rename** (`6edaf3b`): optimizacion/documentacion/finanzas → optimization/
  documentation/finance across code + tests (+ migrated the live org.json).
- **Test isolation fix** (`345b4be`): `config.py` no longer loads `~/.lhassa/settings.env` under
  tests (conftest sets `LHASSA_NO_RUNTIME_OVERRIDES=1`); the owner's live `LHASSA_LLM_PROVIDER=claude`
  was leaking into the suite.
- **Inspector/flow fixes**: render the Lhassa admin node; inspector edits persist (limits cleared by
  omission, not null); per-brain skill RECOMMENDATIONS by name/department; skills show only the
  brain's assigned set; parent dropdown pre-selected; department dropdown + create; department group
  boxes on the canvas.
- **Docs** (`55c8181`/`49e37af`): README/CLAUDE/architecture refreshed + NEW `doc/cli.md`;
  relationships + flows corrected from "reserved" to shipped; the 9 dashboard sections + data dir.

## 2026-06-30. Product/UX batch: skills, Lhassa-as-root, org flow, CLI palette, settings, templates, Lhasa reskin

A big autonomous product/UX push. All committed, gates green (full suite 849 → 876), no AI
attribution. Lhassa is the city's identity now; visually and organizationally.
- **First-class skills** (`3323996`): `lhassa/skills/`: SKILL.md format (YAML frontmatter +
  instructions, the skills.md / awesome-agent-skills convention) + JSON form, `SkillStore`
  (~/.lhassa/skills), AI create/improve (degrade without a model), offline `recommend` with a
  seed catalog, `brains_with_skill`/`assign_to_brain`. `lhassa skills` + `/api/skills/*`.
- **Lhassa = permanent root administrator** (`54701bb`): `config.root_admin_id` (default
  "lhassa") always roots the org; it's never removed/demoted; `promote` makes a brain a top-level
  child of Lhassa (never unseats it); `default_topology`/`load_topology` pin/migrate the admin;
  `describe()` exposes `admin`/`is_admin`.
- **n8n-style org FLOW editor + skills panel** (`95ada50`): React Flow canvas; nodes=brains,
  drag-to-reparent (→ move), inspector edit/promote/delete, route preview; the Lhassa admin node
  is anchored (no source handle, not draggable, can't be moved/deleted). SkillsPanel drives the
  skills API.
- **CLI redesign** (`05296d7`): `_boot.quiet_logging()` kills loguru's default DEBUG sink (no more
  pipecat/Tools-registered/gateway-DEBUG spam in the REPL) before the heavy imports; `theme.py` is
  the **Lhasa palette**: temple GOLD, monk-robe RED (sparing), snow WHITE, sage GREEN, stone DIM;
  styled prompt `lhassa ›`, cost footer, `/help`. Override via `LHASSA_CLI_LOG_LEVEL`.
- **Dashboard reskin to the Lhasa palette** (`a0bb2d6`): warm-gold elegant dark UI (same hexes as
  the CLI), Inter + JetBrains Mono, tracked labels, dual-rail nav with a gold indicator, the org
  admin node in gold+red; a Tailwind `@theme` remap folds all panels into one warm family (no teal).
- **Settings service + flow templates** (`b4d960a`): `settings.py` (an EDITABLE allowlist; `set`
  writes `~/.lhassa/settings.env`: loaded after `.env` with override, secrets in `.env` untouched;
  and applies to the live Config; `effective()` masks secrets + exposes options/restart hints) +
  `org/templates.py` (use-case presets built UNDER Lhassa: code-review, bug-investigation,
  refactor-migration, incident-response, and a multi-level **software-factory** delivery→{frontend,
  backend,qa,devops}). `lhassa config`, `lhassa org templates|apply`, `GET/PATCH /api/config`,
  `GET/POST /api/org/templates…`.
- **Settings panel + templates picker** (`9218ba5`): a Settings section (per-type controls, masked
  secrets, restart-hint pills, red Budget&Safety group) + a Templates gallery (apply → adopt chart,
  Software Factory highlighted), in the Lhasa design.

**Live:** the dashboard (http://127.0.0.1:7766, served by `lhassa-app.service`) now has Brains
(flow/tree/templates) · Skills · Settings · Reports · Fleet · Activity, in the elegant Lhasa
identity. A global `~/.local/bin/lhassa` launcher was added so the CLI runs from anywhere. Live
smokes: edit a setting → settings.env + live apply; apply software-factory → 5 brains under the
Lhassa admin; create+assign a skill; the REPL is clean and colored.

## 2026-06-30. Capability batch: fallback chain, capabilities, Docker, org management, Warp dashboard, installer

A large autonomous batch turning Lhassa into a more capable, portable, always-on tool. All
committed, gates green (full suite grew 745 → 805), no AI attribution.
- **Provider fallback CHAIN** (`f04ab2a`): `ClaudeCodeProvider` (`claude --print --output-format
  json`) + `CodexProvider` (`codex exec --json …`), registered via `register_provider`; verified
  with live calls. `notify/telegram_inbound._fallback_chain` walks `config.fallback_providers`
  (`claude,codex,default`), skipping the primary's kind and uninstalled CLIs; so chat is
  `opencode → claude → codex → cloud`, keeping the owner on subscriptions before paying cloud.
  This fixed the Telegram bot answering "I can't reach my model" (opencode boots a runtime per
  call and flakes under the long-running poller; earlier fix `b36de30` added the cloud fallback).
- **`lhassa capabilities` / `doctor`** (`6917f25`): introspects every keystone registry +
  gated config + loaded plugins (LLM-free, never-raises); surfaces what's wired and on/off.
- **Docker** (`fb80a22`): multi-stage image (node builds dashboard → python:3.13 runtime,
  non-root, `HOME=/data` volume, port 7766, healthcheck), `docker-compose.yml` (api/heartbeat/
  telegram roles via `LHASSA_RUN`), `doc/deployment.md`. Build + run verified (API 200, beat $0).
- **Brain/org MANAGEMENT** (`5d3a6ba`): `BrainNode` gains `skills` + `limits`; `org/management.py`
  add/update/remove/`set_parent` (who commands whom) / `set_root` (who commands everyone) /
  set_skills/set_limits/`describe`; `lhassa org` add/move/promote/set-skills/set-limits/show/skills;
  `/api/org/topology|skills|route|brains[/{id}/move|promote]`. Live API smoke passed (create →
  re-parent → promote → route preview). The owner now creates/manages brains + hierarchy by CLI + API.
- **Dashboard redesign** (`b6b9be6`): Warp/Oz-inspired dark theme (design tokens, teal accent,
  mono for IDs/metrics, sidebar of "blocks") + `BrainsPanel`/`OrgTree`: visualize the hierarchy
  and drive the org API (create/edit/move/promote/delete + route preview). Existing panels restyled.
- **One-command installer** (`5ed00bc`): `scripts/install.sh` (preflight → uv sync + dashboard
  build → docker build+smoke → install systemd USER services → `loginctl enable-linger` for boot),
  `uninstall.sh`, `scripts/systemd/lhassa-app.service` (no-voice API+dashboard+scheduler via
  `docker/run_api.py`), `doc/install.md`.

**Now running (background + on boot):** ran `install.sh --no-docker`: `lhassa-app.service`
(dashboard+API+scheduler on 127.0.0.1:7766), `lhassa-heartbeat.timer`, and
`lhassa-telegram.service` are **enabled + active**, lingering on, so they start at boot before
login. Verified live: `GET /` → 200 (title "Lhassa"), `/api/org/topology` → 200.

**Telegram (live):** the bot answers reliably via the fallback chain; name/description/commands
set in English; the inbound poller runs as the systemd service.

## 2026-06-30. Phase 7 slice 2 (departments think) + heartbeat wired to opencode

**Ops:** the heartbeat now thinks via **opencode**: set in `~/Code/lhassa/.env`
(`LHASSA_HEARTBEAT_PROVIDER=opencode`, `LHASSA_OPENCODE_BIN=/usr/bin/opencode`: absolute
because the systemd user PATH is minimal, `LHASSA_OPENCODE_MODEL=opencode/deepseek-v4-flash-free`).
No systemd edit needed: each beat is a fresh process that re-reads `.env`. `.env` is
gitignored (secrets stay local). The routine path resolves to `PseudonymizingProvider(
OpenCodeProvider)`: opencode with PII scrubbing (opencode is cloud), **$0** on the free tier.
A `scripts/systemd/lhassa-telegram.service` user unit was added for the inbound poller
(credential-gated; install/enable when the owner sets a bot token).

**Phase 7 slice 2** (departments actually think; the general routes among many):
- `org/executor.py` `brain_executor()` runs a department task through the REAL one-turn
  brain path (identity + layered memory + router routine provider + `provider.complete`),
  the department's identity in the system prompt, logs a `despertar_por='org'` episode,
  degrades to a structured **$0** result on `ProviderNotConfigured`/budget (never raises;
  a failure becomes a `TASK_RESULT`, not a crash). The live model call is **gated behind
  `LHASSA_ORG_LIVE`** (default off → offline-deterministic) so slice-1 tests stay offline;
  the established turn-on-with-creds convention (subagent's call; honors the don't-break-
  tests/offline constraints).
- `org/coordinator.py`: `DepartmentBrain` defaults to `brain_executor` (still injectable);
  `GeneralBrain.assign_auto()` routes then assigns (the general routes, never executes).
- `org/routing.py`: deterministic table-driven registry (optimizacion/documentacion/
  finanzas) + `route()` with a documented fallback; **no LLM**. `org/__main__.py` offline
  two-brain demo.

**Verified:** 617 tests, ruff clean, `python -m lhassa.org` runs offline ($0). **Live smoke
(`LHASSA_ORG_LIVE=1`):** the general routed a goal → the `optimizacion` department **thought
for real via opencode** (free tier, **$0**), returned a genuine answer + a typed `TASK_RESULT`
correlated by `corr_id`. Two brains thinking end to end.

**Next Phase-7 candidates:** a second live department in the demo; cross-department commons
policy enforcement during a think; `STATUS_REQUEST`/`STATUS_REPORT` flow; an API/dashboard
view of the org message queue. **Backlog:** point a real department at `LHASSA_ORG_LIVE=1`
in production once desired.

## 2026-06-30. Phase 6 cont. (heartbeat via opencode, voice, Telegram) + Phase 7 START

**Decision:** the owner has **no Ollama/Zen** but has the **opencode CLI** → the routine
heartbeat thinks through **opencode** (their existing creds), default the free tier
`opencode/deepseek-v4-flash-free`. Also: **start Phase 7 now**: the owner explicitly
authorized it (overriding the brief's "not yet"); built as the minimal two-brain skeleton.
4 slices, disjoint ownership, gates green (config surface added up front):
- **opencode heartbeat** `OpenCodeProvider` (`opencode run --pure --format json -m …`;
  router routine→opencode). **`--pure` matters:** a beat dropped from **>120s → ~8s**
  (without it opencode hangs on plugin/MCP startup). **Live-verified schema** (replaced the
  `[SUPUESTO]`): assistant text at `part.text` on a `text` event; totals nested under
  `step_finish` `part.tokens`/`part.cost` (the first parser read top-level only → fixed,
  + a regression test). Live smoke: free-tier round-trip → `pong`, tokens 11153/3, **$0**.
- **voice** `voice/brain_bridge.py`: spoken turns answered by the brain (identity +
  layered memory + episode `voz`), pipecat frames emitted via a lazy processor; swapped in
  only when `voice_enabled`. Default path unchanged. (Live audio needs Deepgram/Cartesia.)
- **Telegram** `notify/dispatch.py` (fan-out) + `notify/telegram_inbound.py` (getUpdates
  poller; honors only the configured chat; answers via `cli.agent.run_once`; `python -m`
  entrypoint). Credential-gated; no token → no network. (Live needs a bot token.)
- **Phase 7 `org/`** (foundation, golden rules enforced + smoke-verified on real SQLite):
  typed messages (`MessageKind` closed; free prose rejected) · read-only **commons** (only
  `general` writes; one shared row) · **RBAC** (deny-by-default, cross-department isolation)
  · **GeneralBrain coordinates, never executes** (no handle/executor) while DepartmentBrain
  executes via an injectable executor and logs a `despertar_por='org'` episode (audit plane)
  · **multi-person approval** (proposer≠approver for high-risk → `SelfApprovalError`) ·
  **two brains, never six**. `org_messages`/`org_commons` tables added to `db.py`.

**602 tests** (529→+73), ruff clean, imports/help ok, dashboard builds.

**Turn-on-with-creds / continuous:** point the systemd heartbeat at opencode by setting
`LHASSA_HEARTBEAT_PROVIDER=opencode` (or leave `auto`: opencode is auto-preferred when no
`local_model_url`); voice needs Deepgram/Cartesia keys + `LHASSA_VOICE_ENABLED=1`; Telegram
needs a bot token + chat id (+ `LHASSA_TELEGRAM_POLL=1` for inbound). **Backlog / `[SUPUESTO]`:**
wire the org default executor to the live one-turn brain path (`core.step`); the injectable
`executor` is the seam; opencode `--pure` means org/MCP tools aren't available to a beat
(fine for routine thinking; delegated code work still uses the harnesses); the live pipecat
frame wiring of the voice bridge and the Telegram `getUpdates` shape are unverified without
creds. **Casa/MCP** (home integrations) remains the open Phase-6 continuous item.

## 2026-06-29 (Phase 6 (Madurez)) first push: local heartbeat, memory layer 4, rich interfaces

Phase 6 is **continuous** (no single DoD). This first push lands the autonomous,
offline-verifiable pieces; voice + home/casa MCP are noted as the remaining
continuous items (they need audio creds / per-home servers to smoke honestly).
Built (3 slices, gates green, file ownership disjoint; config surface added up front):
- **6.A** `llm/provider.py` `LocalModelProvider` (OpenAI-compatible local endpoint,
  no API key, **cost $0**, `zero_retention`+`is_local`; unreachable→`ProviderNotConfigured`
  → existing degradation path) + `privacy.py` (deterministic stdlib PII scrubber,
  reversible placeholders, never raises) + `PseudonymizingProvider` (wraps a remote
  provider: scrub before send, de-scrub after) + `router` routine→local preference
  (`heartbeat_provider` auto/local/cloud), hard→cloud, cloud path pseudonymized unless
  the provider is local (**the hook relaxes for local**: §3-Fase 6).
- **6.B** `memory/embeddings.py` (pluggable `Embedder`: `HashingEmbedder` deterministic
  offline default + optional `OpenAICompatEmbedder` degrading to hashing; no numpy) +
  `db.py` `fact_embeddings` table/helpers + `memory/load.py` **layer 4** (semantic
  `Context.semantic_facts`, lowest `PRIORITY` so it truncates first and never touches the
  identity core; `load(query=,config=)` + explicit `index_facts()`). **Off by default**;
  existing `load()` is byte-for-byte unchanged.
- **6.C** `cli/` power subcommands (`tools/approvals/approve/deny/report/memory/fleet`,
  each `--json`) preserving the default `lhassa "prompt"`/piped/REPL via a fallback
  `TyperGroup` + `notify/telegram.py` (credential-gated, no token→no network, never
  raises) + `/api/reports`·`/api/projections`·`/api/fleet` + dashboard Reports/Fleet panels.

**DoD-style live smoke** (offline tests + a smoke against a **real in-process
OpenAI-compatible socket server**): the routine path thinks on the **local** model at
**$0** with PII **un-scrubbed** (data never leaves the box; hook relaxed); the hard/cloud
path is wrapped in `PseudonymizingProvider` and PII is scrubbed→de-scrubbed round-trip;
memory **layer 4 ON** recovers an overheating fact that shares **no tag** with the query
("why is my computer warm and slow") and ranks it first, while **layer 4 OFF** yields no
semantic layer. **529 tests** (460→+69), ruff clean, `lhassa --help` lists 7 subcommands,
dashboard builds.

**Deferred / continuous (Phase 6 is ongoing):** **voice** (skeleton `voice/pipeline.py`
exists; end-to-end wiring needs Deepgram/Cartesia creds + audio to smoke) and **home/casa
MCP** integrations (per-home servers) are the remaining items. **Turn-on-with-creds:**
the local heartbeat needs a running local endpoint (`LHASSA_LOCAL_MODEL_URL`, e.g. Ollama);
layer 4 needs `LHASSA_MEMORY_LAYER4=1` (+ optional `LHASSA_EMBEDDING_MODEL`); Telegram needs
a bot token+chat id. **Backlog / `[SUPUESTO]`:** the cloud embedder + real-endpoint JSON
shapes (Ollama `usage`/`choices`, OpenAI `/embeddings`) are assumed, not yet live-verified;
the heartbeat is not yet pointed at a local model in the systemd unit (config default `auto`
with an empty URL = unchanged behavior); a free-text CLI prompt whose first word is exactly
a subcommand name routes to that subcommand unless quoted.

## 2026-06-29. Phase 5 complete (self-improvement 2b in quarantine)

The brain can write its own tools and graduate them only through objective gates,
caged. Built (2 slices, gates green):
- **5.A** `security/quarantine.py` (`run_in_quarantine`: strongest-available sandbox
  via `unshare`/`firejail`/`bwrap` for **no-network**, a from-scratch **scrubbed env**
  so no secret leaks, temp cwd, `killpg` on timeout; `str`→`bash -c`, `list`→argv) +
  `agents/gates.py`: the **four gates of 2b**, deny-by-default, stop-at-first-failure:
  1 **Segura** (runs caged) · 2 **Correcta** (a command/tests verifier exits 0) ·
  3 **Mejor** (a `MetricVerifier` beats a concrete baseline; optimización is
  lower-is-better) · 4 **Retiro** (continuous: `should_retire` demotes a graduated
  tool that fails in production or goes unused). Gates **reuse** the Phase-3 verifiers
  and **never call an LLM**.
- **5.B** `agents/specialists/` (**skills, NOT fine-tuning**: `Specialist` = base model
  + instructions + tools + `gate_spec`, no model id anywhere; registry keyed by domain,
  first = `optimizacion`; `build_specialist_prompt` = persona identity + domain skill) +
  `agents/versioning.py` (**local-git** tool versioning: `create_candidate`→branch,
  **`approve` = merge** routed through the approval queue as a `tool_graduation`,
  `rollback` = `git revert`; `_git` never raises and degrades gracefully when git is
  missing; GitHub-remote PR/merge are **token-gated no-op hooks**, never reached
  offline) + `/api/tools` + graduate endpoint + `QuarantinePanel` + `tools_repo_dir`.

**DoD met** (offline tests + a live smoke with real git/SQLite/quarantine): the brain
writes a tool → candidate on its own branch → a good candidate **graduates** (segura+
correcta+mejor) while a worse one is **rejected at gate 3 with a reason** and an
incorrect one **at gate 2** (gate 3 never runs); graduation is an approval-queue
**merge** into `main` (a resolved `aprobada` `tool_graduation` row, content on `main`);
a graduated tool failing 3/4 in production (or unused) **retires** to cuarentena; and a
**git revert** rolls it back. "Mejor" is proven by a number (42 < 100), not asserted.
460 tests, ruff clean, dashboard builds.

**Deferred (token-gated, non-blocking):** the GitHub-remote path (push branch + open/
merge a real PR) is wired as a no-op hook behind `Config.github_token`: turns on with
a new token (old one revoked). **Backlog:** more specialist domains are just new
registry rows; the retirement supervisor's "went unused" grace window is `[SUPUESTO]`.

## 2026-06-29. Phase 4 complete (hands: MCP + harnesses)

Decisions: 2nd typed task = **`tracking_postulaciones`**; the owner has all three
harnesses (opencode/Claude Code/Codex). Built (2 slices, gates green):
- **4.A** `harness/adapter.py` (`HarnessAdapter` Protocol + `RunEvent` + stream-json/
  plain parse; OpenCode/ClaudeCode/Codex adapters auto-detected via `which`; argv
  flags marked `[SUPUESTO]`) + `harness/runner.py` (`launch` fire-and-forget records a
  jobs row + PID, streams events, accrues cost; `supervise` reaps each tick) + router
  `pick_harness` (cheapest available: free<metered<subscription; `LHASSA_HARNESS`
  override) + `step` delegate branch (launch + episode notes harness/job/PID, no block).
- **4.B** `tools/mcp_client.py` (`mcp==1.28.1`; loads `~/.config/lhassa/mcp.json`,
  registers namespaced `mcp__<server>__<tool>` tools onto the LLM; defensive) +
  `postulaciones` table + `tracking_postulaciones` deterministic action (offline core,
  optional guarded MCP top-up) + `track_postulaciones` registry tool.

**DoD met** (offline + live runner smoke): an MCP tool is invocable; the brain delegates
to any of the three harnesses and supervises PID/cost live (a real subprocess launched,
streamed, reaped to `done` with cost $0.0021); adding an integration is config, not code.
421 tests, ruff clean.

**Backlog:** cross-restart job supervision by PID-from-disk (the reaper uses the
in-process registry; a launched-before-restart job isn't reaped after a brain restart);
validate the adapter argv `[SUPUESTO]` flags with a live harness smoke (spends; owner's
go); a real opencode/claude/codex delegation smoke is available on request.

## 2026-06-29. Phase 3 complete (the arbiter)

Built (2 slices, gates green):
- **3.A** `agents/verify.py`: objective verifiers (`command/tests/lint/health/regex/
  metric`), **never call an LLM** (no provider/gateway import; asserted in tests) +
  `make_verifier`/`verify` dispatcher; wired into `core/step.py`'s judgment branch via
  `router.cascade(verify=…)` (failed verification escalates cheap→smart once; the
  episode records `verificado: passed/failed`). Reused by Phase-5 gates.
- **3.B** `security/approval.py` (HITL-**per-flow** `needs_approval`: per-action risk
  + origin trust + a high-risk always-approve set; parks pending in the `approvals`
  table) + `security/autonomy.py` (a streak knob: a HUMANO action approved N× becomes
  automatic; a denial resets it; high-risk never auto-promotes; **never promotes an
  `Origin`'s trust**: contagion-only) + `local_agent` approval gate + `/api/approvals`
  + `ApprovalQueuePanel`.

**DoD met** (offline + live smoke): a `mundo`/sensitive action waits for the owner's
OK; a repeated `humano` action becomes automatic (threshold 3); move-money always
requires approval even with a streak; a denial resets the streak; the verifier never
calls an LLM. 369 tests, ruff clean, dashboard builds.

Note: 2 pre-existing eslint warnings in `App.jsx handleEvent` (untouched code); not
introduced here; `npm run build` is clean.

## 2026-06-29. Phase 2 complete (economy + router)

Decision: the user has the **opencode CLI but no Zen** → the first `ModelProvider`
wraps the existing `.env` key via the gateway (`GatewayProvider`); `OpenCodeZenProvider`
is a ready stub (swap-in when Zen creds exist, per invariant #3). opencode-as-harness
is Phase 4.

Built (3 slices, gates green):
- **2.A** `ModelProvider` Protocol + `GatewayProvider`/`OpenCodeZenProvider`,
  `llm/router.py` (two axes, `pick_provider`/`pick_model`, cascade cheap→verify→
  escalate, `$think`/`$justdo`), and `core/step.py`'s judgment branch wired (load
  context → router → provider; `BudgetExceeded`/`ProviderNotConfigured` degrade to a
  deferred episode, no crash).
- **2.B** gateway **kill-switch** (`KillSwitchActive` ⊂ `BudgetExceeded`, refuses
  before any call) + persisted runtime budget override + `llm/ccusage.py` (defensive
  `ccusage --json` parser/runner) + `/api/llm/limits` & `/api/llm/ccusage` + dashboard
  controls (budget setter, kill-switch toggle, cross-harness breakdown).
- **2.C** `memory/consolidate.py` **semantic consolidation** (cheap model via the
  provider/gateway: cluster-summarize + detect contradictions; complements the
  mechanical LRU; only touches the chica; threshold-driven).

**DoD met:** routine routes to the cheap model; the ledger shows the split (real
per-model pricing, tested); **crossing the cap stops**: verified live (kill-switch
raises, provider called 0×). 321 tests, ruff clean, dashboard builds.

**[SUPUESTO] to validate later:** ccusage JSON field names (marked in code); and the
live "a day of heartbeats costs cents" smoke needs real cheap-model calls (deferred;
won't spend without the owner's go). **Backlog:** swap to opencode Zen when available.

## 2026-06-29. Phase 1 complete (memory)

Built additively (old `memory/store.py` untouched), 2 slices, behind gates:
- **1.A** `core/db.py` facts table (Apéndice A, append-only, instance_id) +
  `memory/facts.py` (`Fact` dato/directiva, contradiction = newest+confianza) +
  `memory/load.py` (layered `load(token_budget)`: pinned+directivas always →
  recency → active-tags; truncate by priority; deterministic token estimate; **no
  embeddings / layer-4**) + `memory/seed.py` (Apéndice B seed facts).
- **1.B** `memory/chica.py` (persistent working cache; **threshold-LRU
  `consolidate`** that never evicts pinned/directivas; `regenerate` from the
  grande) + `memory/backup.py` (WAL-safe online `backup`/`restore`/`verify`).

**DoD met** (offline tests + live SQLite smoke): recover a fact from "days ago" by
tag · chica fills → consolidates without overflow (23→5, pinned kept) · kill the
chica → regenerates from the grande · backup → verify True (corrupt → False) →
restore round-trips. 272 tests, ruff clean.

**Deferred to Phase 2 (per the brief):** semantic memory consolidation (summarize
similar items / detect contradictions via a cheap LLM); Phase 1 is mechanical LRU
only. **Backlog:** reconcile the old `memory/store.py` (voice session facts/
sessions) with the new typed-facts model.

## 2026-06-29. Phase 0 complete (the spine)

Built additively over the existing skeleton, 5 vertical slices, all behind gates
(no AI attribution, pytest green, ruff clean):
- **0.1** `core/db.py` (SQLite WAL "grande", append-only, `instance_id` on every
  table/query) + `core/episode.py` (structured Episode from the first commit).
- **0.2** `core/step.py` + `core/watchlist.py` + `core/actions.py`: the
  deterministic do-and-sleep loop (zero-token watchlist → fixed action → episode);
  the `else` branch is the Phase-2 router seam.
- **0.4** `security/`: signed `Task`, `TrustLevel`/`Origin` (degrade=min, never
  promote), Ed25519 signing, deny-by-default allowlist (= §2.6) with HITL-per-flow.
- **0.3** `/checkin` + `/report` + `remote/local_agent.py`: check-in-not-push with
  desired_state reconciliation; the allowlist gate runs on the machine.
- **0.5** `core/heartbeat.py` + systemd `lhassa-heartbeat.{service,timer}` +
  parametrized identity (`Identity`/`load_identity`, org hook #2; directives loaded
  as identity; `PERSONA_BLOCK` kept stable) + the end-to-end DoD test.

**DoD met** (offline tests + a live in-process smoke with real Ed25519/SQLite/git):
timer pulses → check-in → reconcile → signed `sync_repo` → allowlist gate → action
runs → report → two episodes explain *why* → state persists across a DB reopen →
every row carries `instance_id`. 229 tests, ruff clean.

**Living-doc correction (§4/§7):** the brief lists `OpenCodeAdapter`/`ModelProvider`
under Phase 0, but Phase 0's loop is deterministic-only and never exercises them;
so they're **deferred to Phase 2 (ModelProvider, the router/think path) and Phase 4
(HarnessAdapter)**, where they're actually used. The three org hooks are in place.

Open Phase-0 polish (backlog, not blocking): `desired_state` should map repo names
→ real paths (the smoke's `sync_repo` failed gracefully because the objetivo wasn't
resolved to a path); single-writer staging buffer (§2.7) is a later refinement.

## 2026-06-29; adopting the implementation brief

- **Brief adopted** as the plan: Lhassa becomes a persistent "brain" (JARVIS) with
  its own identity that orchestrates models/harnesses behind interfaces; "build
  the brain, rent the body." Execution model: *do-and-sleep* (`state + step()`
  woken by a systemd heartbeat / API). See `doc/lhassa-brief.md`.
- **Existing code is the skeleton; extend, don't rewrite.** What's already built
  maps onto the brief: `llm/gateway.py` (Phase 2 choke point), `run_ai_cli` +
  `claude_code` (→ HarnessAdapter, Phase 4), `tools/privileged.py` allowlist (→
  `security/allowlist.py` + Ed25519, Phase 0/3), `health`/`scheduler`/`state`/
  `api`/`cli`/`persona` (Phase 0/6). Net-new and FIRST: `core/step.py` +
  `watchlist.py` + structured episode + `instance_id` + check-in (Phase 0).
- **The old `doc/roadmap.md` is superseded** by the brief and re-sequenced; its
  A1 (tools-aware gateway) shipped as commit 290eac9 (a valid Phase-2 piece). The
  rest is re-baselined onto the brief's Phase 0→7.

### Decisions (this session)
1. **Host:** develop **locally first** (the brain runs on the laptop) with all
   seams ready (instance_id, check-in, systemd) to migrate to a VPS without
   surgery. VPS later.
2. **Heartbeat ModelProvider:** start with **opencode Zen** (free/cheap), with
   pseudonymisation as a mandatory hook while the heartbeat uses free models;
   migrate to a local ModelProvider later (Phase 6).
3. **Repo folder:** renamed `~/Code/potts` → `~/Code/lhassa` (the package was
   already `lhassa`).
4. **Governance/pace:** I drive **Phase 0 autonomously** (subagents + gates +
   commits), checking in with the owner at each phase's **DoD** before advancing.

### Open / pending (asked, to confirm when their phase arrives)
- Harness subscriptions available (Claude Code / Codex / opencode).
- Machine network: WireGuard vs Tailscale.
- Telegram notifications (needs a bot token).
- GitHub account/org + a **new** token (old one revoked) for tool versioning (Phase 5).
- Adopt **ccusage** for cross-harness accounting.
- Seed facts (Appendix B); confirm they're the owner's; `instance_id = inst_personal`.
- Second typed task (Phase 4): calendar_event / agendar_fechas / generar_documentacion /
  tracking_postulaciones.
- `[SUPUESTO]` items to validate with live smoke runs (Zen training on free data;
  Managed Agent Harness as sandbox; heartbeat provider ToS/rate-limits).
