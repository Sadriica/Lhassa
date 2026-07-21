# Lhassa — decisions log

## 2026-07-21 (noche) — Olas 2+3: la consciencia siente, la jerarquía sin deuda

- **2.1 adaptive thresholds** (`9010114`): `effective_threshold(base, population,
  floor=2)` = `max(floor, min(base, ceil(population/base)))` — full strictness
  only at population ≥ base²; a young brain fires on 3 repeats, a mature one
  stays strict; explicit args still win; findings carry `threshold_used`;
  cadence can relax to 2 occurrences, never 1.
- **2.2 feedback → autonomy**: `security/autonomy.apply_feedback` — ≥2 negative
  signals/window reset an accion's streak (like a denial); positive reinforces
  at most +1 and only where a human already started trust (streak ≥1); negative
  wins conflicts; high-risk + protected-root actions untouchable; idempotent per
  UTC day via watch_state. Attribution: signals name only responder episodes, so
  the nearest concrete accion within ±30min is used; unattributable → skipped,
  never guessed. Registered as the `apply_feedback` action (allowlist no-approval
  hygiene tier), daily in the maintenance seed, owned by the ORACLE monk.
- **2.3 forge drill**: `lhassa tools propose disk_report` ran the WHOLE pipe
  live — DeepSeek authored tool+test, quarantine (unshare) ran it, segura ✅,
  correcta ✅ (its test passed), mejor ✗ (no baseline). Deliberate outcome: the
  gate was NOT weakened — a net-new CLI proposal has nothing to prove "better"
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
- **Test hermeticity**: embedding tests pinned `embedder_backend="hash"` — a
  LIVE local Ollama must never change test outcomes.


## 2026-07-21 (tarde) — Plan "kilometraje", Ola 0+1 (subagentes en paralelo)

`doc/plan.md` adopted: the audit showed the architecture is mature but the
learning loops lack MILEAGE — so waves 0-1 make existing loops live instead of
building new ones. Executed with four parallel Sonnet subagents on disjoint
file territories; two items turned out ALREADY BUILT by prior work (thread
continuity — `capabilities.is_followup`/`continue_followup`, commit 12c3e99;
knowledge context layer 6, commit 974d028) and were verified, not duplicated.

- **0.2 cache diagnostics** (`e34bf51`): `answer_cache.lookup(stats=…)` exposes
  best-similarity/scanned on every miss; the telegram think episode records
  `cache-miss best=0.83 scanned=12` — days of these decide whether the 0-hits
  problem is the 0.90 threshold, traffic, or the hashing embedder.
- **0.3 mileage telemetry** (same commit): the digest's "📈 My mileage" section
  — thinks + cost, cache hit-rate, $0 work, real self-improvement findings —
  over 24h/7d windows. The evening digest answers "is it learning?" unasked.
- **1.1 real embeddings**: `RemoteEmbedder` speaks Ollama's native `/api/embed`
  (with `/api/embeddings` fallback); `get_embedder` gains an `embedder_backend`
  gate (auto/ollama/hash, defensive `getattr`); embedder `fingerprint` +
  `needs_reindex` guard against mixing hash- and real-vectors; `lhassa memory
  reindex` re-embeds facts + answer cache. The legacy default
  (`embeddings_model=nomic-embed-text` @ `embeddings_url` → Ollama's OpenAI
  surface) already works once the daemon is up, so defaults were kept.
  KNOWN GAP: `answer_cache` rows carry no model stamp — no live mismatch guard
  until a schema migration adds one; `reindex` brute-forces it meanwhile.
  Owner enable: `ollama serve` + `ollama pull nomic-embed-text` + `lhassa
  memory reindex`.


Running log of the architectural/operational decisions for the Lhassa "brain"
build. The source of truth for the architecture is `doc/lhassa-brief.md` (a
living doc — correct it after each phase per §4). This file records the choices
made along the way. Newest first.

## 2026-07-21 — The chat knows itself (self-card, modality taxonomy, [[action: …]] muscle)

Driven by a real Telegram session: asked "what are you running on?" the brain
shrugged; asked how its own search worked it explained correctly only by luck;
asked to operate, it had no hands. Three fixes, all deterministic/$0, none
touching the tool-less invariant:

- **Self-card** (`notify/selfcard.py`): every chat think now carries a ~500-token
  block rendered live from `Config` + the registries — version, instance, the
  chat seat (provider/model), heartbeat seat, fallback chain, CLIs on PATH, and
  the registered action names split run-now vs approval-gated. Best-effort per
  line, never raises, never includes key material (tested).
- **Execution-modality taxonomy** (static half of the card): the model is told
  what ACTIONS, PTC scripts, API think, subscription CLIs, markers and org
  delegation ARE — distinct mechanisms with distinct cost/gate shapes — so it
  can explain itself and reason about which route fits a request.
- **`[[action: <name> key=value …]]` marker** (`telegram_inbound._apply_action_marker`):
  the third marker, mirroring research: chat stays tool-less; the marker mints a
  signed task (same HUMANO/telegram origin as `/goal` — allowlist + approval
  frontier unchanged), a no-approval action runs immediately via `run_task_now`
  and reports its episode `resultado` back over Telegram; approval-gated ones
  park with an honest "/approvals" ack. Unknown/refused actions get an honest
  line, never a silent drop. Acks are written only after the mint succeeded.

**Same day, follow-up — deterministic file hands + the protected tier + /config.**
"Reading a file needs no AI" made real: `actionpack/_files.py` adds `list_dir` /
`read_file` — read-only, $0, allowlisted without approval because the frontier is
the path, not the action: only inside `config.knowledge_roots`, hard ignore-list
(secret-shaped names) enforced even there, traversal resolved before the check,
chat-sized output caps. A SECOND tier, `LHASSA_PROTECTED_ROOTS`, is the owner's
"careful" zone: `security/approval.needs_approval` grew input (d) — a file action
whose `path` resolves inside a protected root ALWAYS parks an approval (fail-closed
on any resolution error, never lowered by the autonomy knob — the tap is the
verification), and `describe()` now surfaces the `path` param so the owner sees
WHICH file they're approving. `/config` (Telegram) is the new CLI-parity door onto
the settings service (same EDITABLE allowlist/coercion/guards as `lhassa config` +
the panel; secrets masked, not settable); `knowledge_roots` and `protected_roots`
joined EDITABLE, so the owner can say `/config knowledge_roots ~/Documents` from
chat and the very next `[[action: read_file …]]` honors it — no restart, because
the set applies inside the poller process itself.

**And natural-language config**: `actionpack/_config.py:set_config` — the owner
says "agrega ~/Documents a tus carpetas de lectura", the model emits
`[[action: set_config key=knowledge_roots value=…]]`, and the action applies it
through `settings.set` — the SAME EDITABLE allowlist/coercion/guards as the
panel, `/config` and the CLI; secrets are unreachable by construction (not
EDITABLE); the result line always states old → new so the episode and the chat
follow-up show exactly what changed. No new marker machinery: the generic
`[[action:]]` seam + one instruction sentence + the self-card listing the keys.

Also today: **DeepSeek V4 Flash became the metered cloud seat** (replacing groq;
CLIs still first everywhere) — provider, pricing, settings and voice branch in
commit `66d6d34`; live-smoked end to end. Note V4 Flash spends reasoning tokens
as output: a tiny `max_tokens` can yield an empty reply (512/768 budgets are fine).

## 2026-07-06 — The brain that knows its owner (Olas 1+2: memory, one clock, remote hands)

Two waves of parallel subagents (one interrupted mid-flight by a session limit and
finished by hand — the salvage was committed incrementally so nothing was lost twice):

- **The chat remembers** (`1e72796`): rolling per-chat history threads into every
  Telegram think (multi-turn conversations keep their thread); every N turns the
  model may flag ONE durable owner fact with `[[remember: …]]` → a deduped `dato`
  in the grande. `lhassa onboard` (five questions → pinned datos + directivas) and
  `lhassa memory add` seed the founding facts. The grande stops being empty.
- **Instant research** (same commit): a no-approval chat-minted task now runs
  immediately via `core/dispatch.py:run_task_now` — the heartbeat's runner, early;
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

## 2026-07-06 — Personal secretary layer (digest check-ins, agenda, today/tomorrow)

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

## 2026-07-05 — The narrative: flag, pillars, voice book, bilingual README

Studied how Hermes *tells* its story (one hammered claim, features as promises,
pain-point-titled sections, user-stories social proof) and built Lhassa's own
narrative — the craft, not the form. Decisions: audience is **public from day one
with the owner as first user** (honest claims only); **bilingual** (README.md EN +
README.es.md); the flag is **"Build the brain, rent the body"** with the owner's
priorities leading the pillars ($0 routine day → subscriptions before APIs → it
forges its own hands → sovereign → no lock-in → 1..N org); tone between
**mítico-sobrio and cálido-personal** with the Tibetan imagery governed by explicit
rules (one touch per section, never replacing a technical noun, homage not doctrine).
Artifacts: `doc/narrative.md` (the voice book — source of truth for all public text),
rewritten `README.md` + new `README.es.md` (identity head, pillars table with the
real ledger as proof — Jul 4: ~720 wake-ups, 5 thinks, $0.0031 —, "a day in the
life" told tick by tick, the gates as a rite of passage), `assets/lhassa-mark.svg`
(the stupa extracted from the dashboard). Unique narrative devices claimed: the
day-in-the-life ticker, the ledger-as-proof, the gates-as-pilgrimage.

## 2026-07-05 — Operative-UX pass + the Hermes study (recall / curator / PTC / portability)

A day of operator-driven fixes followed by a deliberate competitive study of Nous
Research's **Hermes Agent** (cloned to `~/Code/hermes-agent`, full architecture map in
the session log). Verdict recorded: Hermes validates the thesis from the opposite side —
it built the most complete *body* (20+ surfaces, packaging, tools) with no deterministic
$0 plane and OS-only security; Lhassa owns the *nervous system* (do-and-sleep, allowlist/
quarantine/gates, the org). Three Hermes ideas were adopted, made Lhassa-shaped, and
implemented by four parallel Sonnet subagents with disjoint file territories:

- **Recall (FTS5 episodic memory)**: `episodes_fts` external-content index + triggers +
  backfill in `core/db.py` (defensive — a build without FTS5 degrades); `search_episodes`
  (BM25, hostile-input-safe, automation episodes *demoted* not hidden — Hermes' recall-
  blindness lesson); `GET /api/episodes/search`; Telegram `/recall`; and a 5th, lowest-
  priority context layer in `memory/load.py` so a think's `query` pulls the brain's own
  past experiences into context ($0, truncates first).
- **Skills curator** (`skills/curator.py`): usage telemetry in an atomic `.usage.json`
  sidecar (bumped on skill read, best-effort); deterministic $0 lifecycle active→stale
  (`LHASSA_SKILL_STALE_DAYS` 30) →archived (`LHASSA_SKILL_ARCHIVE_DAYS` 90) with tar.gz
  backup first, move to `.archive/`, NEVER delete, pinned exempt, `restore()`; weekly
  `curate_skills` action in the `maintenance` seed, skipped while an agent run is active.
- **PTC — Programmatic Tool Calling** (`agents/ptc.py` + the `run_script` action): one
  LLM turn writes a Python script that orchestrates N deterministic ACTIONS over a
  JSON-lines Unix-socket RPC; the script runs inside the quarantine sandbox (AF_UNIX
  survives the network namespace — proven in a real `unshare -rn` test) and the PARENT
  dispatches every call through the signed-Task + allowlist seam (unknown/disallowed/
  needs-approval → refused, never run). Caps: 300s, 50 calls, 50KB/10KB output.
  `run_script` itself is allowlisted with `requires_approval=True` (run_command-class risk).
- **Portability**: `doc/portability.md` audit (most couplings already degrade; real gaps:
  headless CLI-subscription auth, no voice-less extra in pyproject); docker-compose gained
  the missing `monitor` role. Standing constraint recorded: laptop today, VPS tomorrow —
  no laptop-only couplings in new work.

Earlier the same day (operator-driven fixes, same session):

- **Telegram was dead end-to-end**: `lhassa-telegram.service` lacked `~/.local/bin` on
  PATH (claude CLI invisible → silent chain skip) while `settings.env` pinned
  `LHASSA_MODEL_CHEAP=claude-sonnet-4-6`, poisoning the Groq gateway link (404). Fixed:
  PATH added to the telegram/heartbeat/monitor units; stale model overrides removed;
  `distill_memory` (memory/consolidate) now resolves its provider via
  `router.pick_provider(hard=False)` — the heartbeat seat — instead of hardwiring the
  gateway.
- **Chat-speed seat split**: new `LHASSA_TELEGRAM_PROVIDER` (settings-editable) pins the
  Telegram CHAT door's provider (set to `default`/Groq — ~1.5s replies vs ~9s cold CLI
  boot) without touching what flows/hard work think with; fallback chain unchanged.
- **Monitor signal hygiene**: `_failures_from_episodes` no longer counts the monitor's
  own episodes ("alerting on its own reflection") nor prose of successful thinks; a
  freshness window (`LHASSA_MONITOR_FAILURE_WINDOW_H`, 24h) ages failures out; watcher
  alerts diff by episode *identity* (not count) and NAME what is new — no more
  "(total N)" reading as if the whole backlog just failed.
- **Digest personality**: template rewritten in Lhassa's first-person voice (still $0,
  local-timezone times, failures named); optional `digest_style=persona` re-voices it
  through the cheap gateway model (`digest_language`, degrades to plain — the digest
  always arrives).
- **Approvals became legible**: one deterministic `security/approval.describe(row)` →
  `{title, detail}` rendered by /approvals (Telegram), the dashboard queue/Inbox, and
  the digest — "Make 'responder' automatic · repeated ×41 · $0.26 spent" instead of ids.

## 2026-07-03 — Intelligence pass: deterministic-first brain across four fronts (Etapas 1-4)

Full-brain study (6 parallel subagent audits: core loop, LLM/cost, memory, org/flows,
Telegram, self-improvement) found the recurring pattern: the pieces existed, individually
tested, but were never wired into the hot path. Four stages implemented in parallel
(1 in-tree + 3 isolated worktrees), merged in order:

- **Etapa 1 — reflexes + spend judgment** (`8629e75`): `core/policy.py`, a deterministic
  REFLEXES registry between watchlist and judgment (job_review auto-triage, repo_changed →
  sync_repo for `LHASSA_AUTO_SYNC_REPOS`, health warn → notify; default think/minimal).
  Tiered context in `_judge` (minimal=600tk/full=2000tk; `query` + `active_tags` finally fed
  so tag/semantic layers fire). Deferred tasks now retry with backoff (`sweep_deferred_tasks`,
  max 3 then rechazada). `fallback_chain` moved to `llm/router.py` and walked by the heartbeat
  on ProviderNotConfigured/BudgetExceeded ($0 CLI subs first). `gateway.spend_summary()` +
  soft budget pct (default 0.8) downgrade away from the smart model; `check_spend` action.
- **Etapa 2 — operative day via Telegram** (`3d40489`): deterministic $0 digest
  (`notify/digest.py` + `send_digest` action + `digest` seed at 08:00/21:00);
  parse_mode/inline keyboards in the Telegram channel; `/approvals` `/approve` `/deny`
  commands + callback-button approvals on watcher alerts; `/watch` `/watches` `/unwatch`
  `/goal` author watch rules and recurring goals from chat; health notifications routed
  through the channel registry (one precise new layer exception, documented).
- **Etapa 3 — real delegation** (`f34ea94`): the judgment `delegate` key now mints `org_run`
  tasks through the mint_task security path (cap 3); `org_run` action lazily runs
  `run_goal` (offline/$0 unless LHASSA_ORG_LIVE); monitor findings become minted work under
  `LHASSA_MONITOR_DELEGATE` (fingerprint-deduped); coordinator fans out to top-3 departments
  on ties/explicit multi with offline deterministic synthesis (live = one extra think);
  org inbox drained via a new extensible watchlist sense (`watchlist.register_check` +
  `org/inbox_sense.py` — core stays org-unaware, no new layer exception).
- **Etapa 4 — visible self-improvement** (`3da06c4`): `agents/repetition.py` ($0 SQL
  detector of repeated costly thinks → parks a `mint_action` approval + notifies, deduped
  via watch_state); `agents/author.py` (routine-model tool authoring →
  `create_candidate` → gates → `tool_graduation` approval; `lhassa tools propose`);
  `/api/tools/{n}/graduate` now refuses ungated tools (409); `review_tools` action applies
  should_retire/retire; `new_facts` dispatch dedup; `maintenance` seed (distill_memory +
  detect_repetition daily, review_tools weekly).

1333 tests green, lint clean, architecture guard intact. Known gaps left on purpose:
`bump_tool_usage` still has no real execution seam (no self-written tool executes yet);
coordinator fan-out is single-level per hop. CORRECTION to the audit: legacy
`memory/store.py`/`session.py` is NOT orphaned — it still powers `/api/scheduled`,
`/api/memory` reads, `scheduler.py` reminders, the voice pipeline and the schedule
tool handlers; retiring it needs a real migration onto the grande, not a deletion.

## 2026-07-02 — Follow-up: kill the api/misc accumulator + make the ccusage dashboard read fast & real

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
  a slow `npx ccusage@latest` cold-start, and — the real culprit — ccusage was **fetching model
  pricing over the network on every run**. Fixes, in layers: (1) `read_ccusage` timeout is now a
  TOTAL budget (a deadline across candidates), never ~2× (`3909d37`); (2) an `allow_npx=False` the
  dashboard auto-load callers pass, so a missing binary degrades in ~5ms instead of a cold npx
  download (`552281b`); (3) the ccusage commands run **`--offline`** (cached pricing), which drops
  a full run from 20s+ to ~3.7s (`8abe74c`). The `ccusage` binary is installed **user-level**
  (`npm install -g --prefix ~/.local ccusage` → `~/.local/bin`, on the app service's PATH); with
  it present the dashboard shows genuine cross-harness spend (`available: true`, real
  `total_cost_usd`), and stays instant-degrade when absent. Lesson logged: **live smoke earns its
  keep** — the test/e2e mocks never exercised the real CLI.

## 2026-07-02 — Reviewer-driven refactor & reproducibility pass: split the giants, seed registry, e2e net

A hygiene arc (no new features): make the codebase reproducible on a fresh machine, shrink the
biggest modules into thin compositions, and add an automated frontend safety net — all
**behavior-preserving** (proven by an unchanged **1071-test** backend suite + a green e2e +
per-panel live browser verification). Driven by a reviewer's prioritised list; the guiding rule
throughout: pure structural moves, gates green after each step, one small committable slice at a
time.

**Reproducibility hardening.** `.env.example` gained the ~22 missing knobs (telegram, org-live,
heartbeat/claude/CLI providers, CV dir/flow/tectonic, monitor_*), grouped + commented with
defaults; `scripts/install.sh` and the `Makefile` now install + enable the `lhassa-monitor` and
`lhassa-health` units too (all units use `%h`, no hardcoded paths); README + `doc/install.md`
document `uv`/`node`/`tectonic` (the last optional — CV→PDF else `.tex` fallback). (`c324c17`,
`d4e1bd5`)

**Seed registry (elastic bootstrap).** `lhassa/seeds/` — a `Seed` unit + a `SEEDS` Registry
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
  (`1cceb2a`), then skills/org/memory/config/tools/misc (`7163d8c`) — 0 inline HTTP routes left,
  all moved routes smoke-200 live, test-monkeypatch seams preserved (overview reads Config via the
  module namespace; export/import + `build_overview` re-exported from `server` for the existing
  test/CLI importers).
- `tools/registry.py` **981 → 128** — `TOOLS` → `tools/schemas.py`, handlers → `tools/handlers/{web,
  shell,files,agents,schedule,outlook,github,vision,monitor,jobs}.py` + `_shared`; `registry.py`
  re-exports every previously-importable symbol via `__all__` so callers/tests are untouched. The
  one architecture `ALLOWED_EXCEPTIONS` (voice.notifier edge) moved to `handlers/agents.py`.
  (`88adff6`)
- Dashboard: `App.jsx` **420 → 124** (hooks `useWebSocketEvents`/`useSectionRouting` + layout
  `NavRail`/`MobileNav`/`PanelShell` + `lib/sections.js`; the WS reconnect + hash routing byte-for-
  byte, `60f8dba`); then the three big React-Flow/feature panels, each **live-verified in Chrome**
  before commit: `OrgFlow` **915 → 372** (`b968db0` — render, click→inspector, dept-box drag),
  `WorkflowEditor` **944 → 387** (`8ecdf35` — deep-link open, vertical layout, HYBRID inspector),
  `BrainsPanel` **1399 → 151** (`47bc2e8` — inspector incl. skills+Undo/limits/monitor role, create
  form, Flow/Tree/Templates switch). Every lazy-import contract + toast call preserved verbatim.

**Automated frontend net.** Ran the previously-unrun Playwright e2e (browsers already installed);
it immediately earned its keep — a stale assertion failed because the toast system now also surfaces
the "Import complete" message (role=status) alongside the panel copy (role=main), so an unscoped
`getByText` matched two nodes. Scoped the two assertions to `role=main`; **3 e2e green**, which also
confirms the dashboard survives all the panel refactors. (`c834fe6`)

**Verdict + remaining debt.** The reviewer's "dominant risk" (`server.py`) is resolved; tools and
the original big panels are no longer concentration points. What's left is **normal growth debt, to
split on friction, not preemptively**: `OverviewPanel` (668), `SkillsPanel` (534), `SettingsPanel`
(474), `AgenticPanel` (478), `index.css` (899). `useBrainsData` (481) is a cohesive data hook — kept
whole on purpose. `[SUPUESTO]`: the panel splits were verified by manual browser click-through +
build/lint + the 3 e2e; broader automated panel coverage would let future frontend refactors skip
the manual pass.

## 2026-07-01 — Live CV pipeline over Telegram, a governed-flow hierarchy & the constant monitor role

The CV work-flow went from an offline skeleton to a real, live, gate-guarded product a person
drives from Telegram — and, from that concrete case, two STANDING architectural principles were
established: **every flow runs through the org hierarchy**, and **"monitor" is a first-class,
constant role**. All shipped green (final: **1061 tests**, ruff clean) in focused commits.

**Live CV pipeline (real output).** Running `flow-zvzpry` live surfaced real defects, each fixed:
- **Per-task token budget** — the org think was capped at 512 output tokens, truncating the CV.
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
- **Contact-preservation gate (an objective, LLM-free árbitro)** — a live run had the model INVENT
  the candidate's email + phone despite a "do not invent" prompt. `agents/verify.py` gains a
  `contact_preserved` verifier: every email + phone (formatting-agnostic) in the base CV must appear
  verbatim in the output, else the gate FAILS and the path stops before export. The engine threads
  `base_cv` into a gate's verify `context`; the CV flow's final gate runs it. So a tailored CV can
  never ship altered contact details. (`7872321`)

**Telegram as the CV surface.** `notify/telegram_inbound.py` bridges a chat to the flow:
- `cv/extract.py` `extract_text` (reads `.tex`/`.txt`, extracts `.pdf` via **pypdf**); `run_flow`
  gained a `base_cv` override; `notify/telegram_api.py` getFile/download + `sendDocument`. (`287dd75`)
- The base CV lives on disk (`~/.lhassa/cv/cv_largo.tex`) and rarely changes, so the frequent action
  is a **command that needs only the job posting**, and changing the base is explicit — **no
  per-upload button prompts** (a design the owner iterated to): `/cv <posting>` (acks, tailors the
  on-disk base, replies with the **compiled PDF** via `cv.compile_pdf`/tectonic, `.tex` fallback);
  `/cvnewbase` (arms the chat — the next uploaded file replaces the base, backing up the old);
  `/cvbase` (shows the base on file). A caption-less document with no arm → a one-off tailor
  (hold, then the follow-up text is the posting). Commands publish to the bot `/` menu. New config
  `LHASSA_TECTONIC_BIN`. Owner-gated, never raises into the poll loop. (`2aaf92e`, `f97ec50`)
- The poller runs in LIVE CV mode via service env (`LHASSA_ORG_LIVE=1` + claude sonnet-4.6). (`b97b7fc`)

**PRINCIPLE 1 — every flow runs through the hierarchy (`org/delegate.py`, `352cf9f`).** A flow is
no longer run bare. `delegate_flow(flow, …)` resolves the flow's **owning department** (pinned-brain
→ department table lookup — interns for the CV flow), walks the command path `lhassa → general →
interns`, logs one **deterministic, $0, never-LLM** `org-delegate` Episode, then runs the flow (lazy
import — no org↔flows cycle). It **never blocks the work**: a topology failure degrades to a direct
run. The Telegram bridge and `POST /api/flows/{id}/run` both call it (run_flow stays the internal
engine); this is the standing pattern for ALL flows, current and future. The hierarchy hops are
deterministic on purpose — governance + a full audit trail at **no extra token cost**; the only spend
stays the flow's pinned worker brains.

**PRINCIPLE 2 — "monitor" is a first-class, constant role.**
- `org/monitor.py` (`54c5bd1`): a reusable capability — `gather_signals()` (read-only, $0: recent
  episodes, health digest, flows, approvals, failures) + `monitor_pass()` → `{observations, risks,
  improvements, delegations}` (offline stub $0; live reasons via `brain_executor`). `make_brain_monitor()`
  makes ANY brain a monitor; applied to the interns brain (role → `monitor`; "everyone reports to it").
  `lhassa org monitor` CLI.
- `org/watcher.py` (`941353c`): the always-on **$0 "ear"**. `detect_events()` diffs two `gather_signals`
  snapshots (new failures / health-worsened / new approvals / run start+finish — transitions only);
  `watch_loop()` has an **elastic cadence** — idle `LHASSA_MONITOR_IDLE_INTERVAL` (default **300s**),
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

## 2026-07-01 — Dashboard as a product: Matte Obsidian redesign, usability pass, CV work-flow & run control

A long UX/product arc turning the dashboard into a real command surface, plus two backend
capabilities. All shipped green (final: **991 tests**, ruff clean, dashboard build + lint clean)
and committed in focused commits on top of the aiosqlite-vendor / API-auth hardening.

- **Vendoring + API hardening (`a27e94f`):** the local `aiosqlite` shim (a sqlite3 wrapper that
  avoids the upstream worker-thread stalls here) moved from a root `aiosqlite/` package that
  **shadowed the real dependency** to `lhassa/_vendor/aiosqlite.py` with explicit imports; plus a
  require-local-or-token API middleware (non-local `/api` needs `LHASSA_API_TOKEN`; loopback allowed)
  + CORS restriction, and a `conftest` `ASGISyncTestClient` (Starlette's sync TestClient hangs here).
- **Visual — "Matte Obsidian" (`7f667a5`):** matured the dark theme to flat matte surfaces (no
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

**Dogfooding:** built the whole thing as a new user via the live UI + API — created an **Interns**
department + a 6-brain CV pipeline (jd-analyst → researcher → cv-writer → ats-evaluator → gate →
critic → finalizer → gate → output) and validated the flow offline, which is what surfaced the 12
usability findings above. **`[SUPUESTO]`:** org/flow live-update is focus-refetch, not a WS push;
run tracking is in-memory (not restart-persistent); the flow-editor deep-link and drag/touch feel
were verified by construction, not on a real mobile device.

## 2026-06-30 — Elastic & modular hardening pass (A–E) + Telegram live + org topology

**Owner principle (now a standing constraint):** Lhassa must be **fully elastic and highly
modular** — every swap point behind an interface + registry, capabilities **drop-in** (add a
module without editing core), and the org layer must run **1 brain OR N brains** with a
**user-defined hierarchy**. Done as 5 slices + config surface (`org_departments`,
`notify_channels`, `plugins_dir`, `org_topology_file`):
- **A (keystone)** `registry.py` (`Registry[T]`: register as call/decorator, get/get_or_none/
  NotRegistered, as_mapping live view, duplicate-protection) + `plugins.py` (`load_plugins`
  imports from `plugins_dir` AND the `lhassa.plugins` entry-point group — self-registration with
  no core edit; idempotent, never raises). Adopted in `core/actions.py` (public API preserved;
  `ACTIONS` is now a live read-only view; `register_action` drop-in seam).
- **B (channels)** `notify/channels.py` — `Channel` protocol + registry (`@register_channel`);
  `TelegramChannel`/`DesktopChannel`/`LogChannel`; `dispatch.notify()` fans out over
  `active_channels(config)` from `notify_channels`. **Fixed the Telegram inbound**: answers with a
  tool-less brain think (memory + routine opencode provider, `despertar_por='telegram'`) instead
  of the 26-tool GROQ agent that emitted malformed tool calls (`tool_use_failed` 400s).
- **B2 (commands)** `notify/commands.py` — Registry-backed slash-command handlers
  (`@register_command` drop-in): `/start`, `/help` (lists registered commands live), `/status`
  (provider+model+health+today's spend, **LLM-free**). Inbound dispatches commands before the brain.
- **C (boundaries)** `tests/test_architecture.py` — an `ast`-based guard (no new dependency)
  forbidding layer-crossing imports (spine/interfaces/adapters ↛ shells; harness ⟂ llm; keystone
  imports no feature module), rules measured from the real graph, with self-tests proving teeth.
  `doc/architecture-layers.md` documents the layers.
- **E (org topology)** `org/topology.py` — `BrainNode`/`Topology` (validated tree) + `load/save`,
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

**Verified:** **718 tests** (full suite ×2, stable — the prior telegram test-isolation flake is
gone), ruff clean, `lhassa --help` lists `org`, dashboard builds. **Live smokes:** a 3-level org
chart routed goals to the correct leaves (coordinators never executed); a single-brain chart
executed directly; `/help` + `/status` rendered offline; an inbound poll answered a real Telegram
message via opencode at $0. **Backlog / `[SUPUESTO]`:** adopt the registry in the remaining
registries (providers/harnesses/verifiers/specialists) as they're touched (continuous); `/status`
reads the gateway ledger path/schema directly; route ties break by child declaration order.

## 2026-06-30 — Agentic run + typed relationships + work-flows + chat-via-CLI (+ docs)

A large feature arc, all committed and live, full suite green (974 tests). Backends were built as
self-contained NEW modules + APIRouters (no server.py contention) and mounted at integration.
- **Chat via local CLIs + `lhassa code`**: the chat (CLI/voice/agent) can run through claude/codex/
  opencode — the owner's subscription, NO API key (`llm_provider=claude` → `run_once` routes to the
  CLI ModelProvider, tool-less; honors an explicit Chat model). The Settings panel gained **Chat
  provider + Chat model** (a separate seat from the heartbeat) and now REJECTS an API provider with
  no key (the bug that broke chat). NEW `lhassa code "<task>"` runs an AGENTIC task through the CLI
  (edits files in the cwd) and **streams live** (a `LocalCliAgent.on_line` sink).
- **Agentic run surface** (`9284651`/`8b82b90`): `POST /api/code` launches a CLI run streamed over
  the existing WebSocket; the dashboard **Run** section watches it live; `lhassa-app.service` gained
  `~/.local/bin` on PATH so it finds the claude CLI.
- **Typed org RELATIONSHIPS** (`b52c1c2`/`dad07d0`): an overlay graph on the command tree —
  `reads`/`notifies`/`reviews` (directed) + `collaborates` (symmetric); `permits()` runtime seam;
  `/api/org/relationships`; the Brains canvas draws dashed per-kind edges (legend + toggles) and the
  inspector adds/removes them.
- **Work-FLOWS** (`998c834`/`d90f5e1`): `lhassa/flows/` model+store+engine (`trigger→task→gate→
  output`+branch DAG; **hybrid** task routing — pin a brain else auto-route by skill; data threads
  along edges; offline/$0 unless `LHASSA_ORG_LIVE`) + `/api/flows` + an n8n-style **Flows** editor.
  Live smoke: create→run (`completed`, trigger✓→task✓→output✓)→delete.
- **English seed rename** (`6edaf3b`): optimizacion/documentacion/finanzas → optimization/
  documentation/finance across code + tests (+ migrated the live org.json).
- **Test isolation fix** (`345b4be`): `config.py` no longer loads `~/.lhassa/settings.env` under
  tests (conftest sets `LHASSA_NO_RUNTIME_OVERRIDES=1`) — the owner's live `LHASSA_LLM_PROVIDER=claude`
  was leaking into the suite.
- **Inspector/flow fixes**: render the Lhassa admin node; inspector edits persist (limits cleared by
  omission, not null); per-brain skill RECOMMENDATIONS by name/department; skills show only the
  brain's assigned set; parent dropdown pre-selected; department dropdown + create; department group
  boxes on the canvas.
- **Docs** (`55c8181`/`49e37af`): README/CLAUDE/architecture refreshed + NEW `doc/cli.md`;
  relationships + flows corrected from "reserved" to shipped; the 9 dashboard sections + data dir.

## 2026-06-30 — Product/UX batch: skills, Lhassa-as-root, org flow, CLI palette, settings, templates, Lhasa reskin

A big autonomous product/UX push. All committed, gates green (full suite 849 → 876), no AI
attribution. Lhassa is the city's identity now — visually and organizationally.
- **First-class skills** (`3323996`): `lhassa/skills/` — SKILL.md format (YAML frontmatter +
  instructions, the skills.md / awesome-agent-skills convention) + JSON form, `SkillStore`
  (~/.lhassa/skills), AI create/improve (degrade without a model), offline `recommend` with a
  seed catalog, `brains_with_skill`/`assign_to_brain`. `lhassa skills` + `/api/skills/*`.
- **Lhassa = permanent root administrator** (`54701bb`): `config.root_admin_id` (default
  "lhassa") always roots the org; it's never removed/demoted; `promote` makes a brain a top-level
  child of Lhassa (never unseats it); `default_topology`/`load_topology` pin/migrate the admin;
  `describe()` exposes `admin`/`is_admin`.
- **n8n-style org FLOW editor + skills panel** (`95ada50`): React Flow canvas — nodes=brains,
  drag-to-reparent (→ move), inspector edit/promote/delete, route preview; the Lhassa admin node
  is anchored (no source handle, not draggable, can't be moved/deleted). SkillsPanel drives the
  skills API.
- **CLI redesign** (`05296d7`): `_boot.quiet_logging()` kills loguru's default DEBUG sink (no more
  pipecat/Tools-registered/gateway-DEBUG spam in the REPL) before the heavy imports; `theme.py` is
  the **Lhasa palette** — temple GOLD, monk-robe RED (sparing), snow WHITE, sage GREEN, stone DIM;
  styled prompt `lhassa ›`, cost footer, `/help`. Override via `LHASSA_CLI_LOG_LEVEL`.
- **Dashboard reskin to the Lhasa palette** (`a0bb2d6`): warm-gold elegant dark UI (same hexes as
  the CLI), Inter + JetBrains Mono, tracked labels, dual-rail nav with a gold indicator, the org
  admin node in gold+red; a Tailwind `@theme` remap folds all panels into one warm family (no teal).
- **Settings service + flow templates** (`b4d960a`): `settings.py` (an EDITABLE allowlist; `set`
  writes `~/.lhassa/settings.env` — loaded after `.env` with override, secrets in `.env` untouched —
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

## 2026-06-30 — Capability batch: fallback chain, capabilities, Docker, org management, Warp dashboard, installer

A large autonomous batch turning Lhassa into a more capable, portable, always-on tool. All
committed, gates green (full suite grew 745 → 805), no AI attribution.
- **Provider fallback CHAIN** (`f04ab2a`): `ClaudeCodeProvider` (`claude --print --output-format
  json`) + `CodexProvider` (`codex exec --json …`), registered via `register_provider`; verified
  with live calls. `notify/telegram_inbound._fallback_chain` walks `config.fallback_providers`
  (`claude,codex,default`), skipping the primary's kind and uninstalled CLIs — so chat is
  `opencode → claude → codex → cloud`, keeping the owner on subscriptions before paying cloud.
  This fixed the Telegram bot answering "I can't reach my model" (opencode boots a runtime per
  call and flakes under the long-running poller — earlier fix `b36de30` added the cloud fallback).
- **`lhassa capabilities` / `doctor`** (`6917f25`): introspects every keystone registry +
  gated config + loaded plugins (LLM-free, never-raises) — surfaces what's wired and on/off.
- **Docker** (`fb80a22`): multi-stage image (node builds dashboard → python:3.13 runtime,
  non-root, `HOME=/data` volume, port 7766, healthcheck), `docker-compose.yml` (api/heartbeat/
  telegram roles via `LHASSA_RUN`), `doc/deployment.md`. Build + run verified (API 200, beat $0).
- **Brain/org MANAGEMENT** (`5d3a6ba`): `BrainNode` gains `skills` + `limits`; `org/management.py`
  add/update/remove/`set_parent` (who commands whom) / `set_root` (who commands everyone) /
  set_skills/set_limits/`describe`; `lhassa org` add/move/promote/set-skills/set-limits/show/skills;
  `/api/org/topology|skills|route|brains[/{id}/move|promote]`. Live API smoke passed (create →
  re-parent → promote → route preview). The owner now creates/manages brains + hierarchy by CLI + API.
- **Dashboard redesign** (`b6b9be6`): Warp/Oz-inspired dark theme (design tokens, teal accent,
  mono for IDs/metrics, sidebar of "blocks") + `BrainsPanel`/`OrgTree` — visualize the hierarchy
  and drive the org API (create/edit/move/promote/delete + route preview). Existing panels restyled.
- **One-command installer** (`5ed00bc`): `scripts/install.sh` (preflight → uv sync + dashboard
  build → docker build+smoke → install systemd USER services → `loginctl enable-linger` for boot),
  `uninstall.sh`, `scripts/systemd/lhassa-app.service` (no-voice API+dashboard+scheduler via
  `docker/run_api.py`), `doc/install.md`.

**Now running (background + on boot):** ran `install.sh --no-docker` — `lhassa-app.service`
(dashboard+API+scheduler on 127.0.0.1:7766), `lhassa-heartbeat.timer`, and
`lhassa-telegram.service` are **enabled + active**, lingering on, so they start at boot before
login. Verified live: `GET /` → 200 (title "Lhassa"), `/api/org/topology` → 200.

**Telegram (live):** the bot answers reliably via the fallback chain; name/description/commands
set in English; the inbound poller runs as the systemd service.

## 2026-06-30 — Phase 7 slice 2 (departments think) + heartbeat wired to opencode

**Ops:** the heartbeat now thinks via **opencode** — set in `~/Code/lhassa/.env`
(`LHASSA_HEARTBEAT_PROVIDER=opencode`, `LHASSA_OPENCODE_BIN=/usr/bin/opencode` — absolute
because the systemd user PATH is minimal, `LHASSA_OPENCODE_MODEL=opencode/deepseek-v4-flash-free`).
No systemd edit needed: each beat is a fresh process that re-reads `.env`. `.env` is
gitignored (secrets stay local). The routine path resolves to `PseudonymizingProvider(
OpenCodeProvider)` — opencode with PII scrubbing (opencode is cloud), **$0** on the free tier.
A `scripts/systemd/lhassa-telegram.service` user unit was added for the inbound poller
(credential-gated; install/enable when the owner sets a bot token).

**Phase 7 slice 2** (departments actually think; the general routes among many):
- `org/executor.py` `brain_executor()` runs a department task through the REAL one-turn
  brain path (identity + layered memory + router routine provider + `provider.complete`),
  the department's identity in the system prompt, logs a `despertar_por='org'` episode,
  degrades to a structured **$0** result on `ProviderNotConfigured`/budget (never raises —
  a failure becomes a `TASK_RESULT`, not a crash). The live model call is **gated behind
  `LHASSA_ORG_LIVE`** (default off → offline-deterministic) so slice-1 tests stay offline —
  the established turn-on-with-creds convention (subagent's call; honors the don't-break-
  tests/offline constraints).
- `org/coordinator.py`: `DepartmentBrain` defaults to `brain_executor` (still injectable);
  `GeneralBrain.assign_auto()` routes then assigns (the general routes, never executes).
- `org/routing.py`: deterministic table-driven registry (optimizacion/documentacion/
  finanzas) + `route()` with a documented fallback — **no LLM**. `org/__main__.py` offline
  two-brain demo.

**Verified:** 617 tests, ruff clean, `python -m lhassa.org` runs offline ($0). **Live smoke
(`LHASSA_ORG_LIVE=1`):** the general routed a goal → the `optimizacion` department **thought
for real via opencode** (free tier, **$0**), returned a genuine answer + a typed `TASK_RESULT`
correlated by `corr_id`. Two brains thinking end to end.

**Next Phase-7 candidates:** a second live department in the demo; cross-department commons
policy enforcement during a think; `STATUS_REQUEST`/`STATUS_REPORT` flow; an API/dashboard
view of the org message queue. **Backlog:** point a real department at `LHASSA_ORG_LIVE=1`
in production once desired.

## 2026-06-30 — Phase 6 cont. (heartbeat via opencode, voice, Telegram) + Phase 7 START

**Decision:** the owner has **no Ollama/Zen** but has the **opencode CLI** → the routine
heartbeat thinks through **opencode** (their existing creds), default the free tier
`opencode/deepseek-v4-flash-free`. Also: **start Phase 7 now** — the owner explicitly
authorized it (overriding the brief's "not yet"); built as the minimal two-brain skeleton.
4 slices, disjoint ownership, gates green (config surface added up front):
- **opencode heartbeat** `OpenCodeProvider` (`opencode run --pure --format json -m …`;
  router routine→opencode). **`--pure` matters:** a beat dropped from **>120s → ~8s**
  (without it opencode hangs on plugin/MCP startup). **Live-verified schema** (replaced the
  `[SUPUESTO]`): assistant text at `part.text` on a `text` event; totals nested under
  `step_finish` `part.tokens`/`part.cost` (the first parser read top-level only → fixed,
  + a regression test). Live smoke: free-tier round-trip → `pong`, tokens 11153/3, **$0**.
- **voice** `voice/brain_bridge.py` — spoken turns answered by the brain (identity +
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
`LHASSA_HEARTBEAT_PROVIDER=opencode` (or leave `auto` — opencode is auto-preferred when no
`local_model_url`); voice needs Deepgram/Cartesia keys + `LHASSA_VOICE_ENABLED=1`; Telegram
needs a bot token + chat id (+ `LHASSA_TELEGRAM_POLL=1` for inbound). **Backlog / `[SUPUESTO]`:**
wire the org default executor to the live one-turn brain path (`core.step`) — the injectable
`executor` is the seam; opencode `--pure` means org/MCP tools aren't available to a beat
(fine for routine thinking; delegated code work still uses the harnesses); the live pipecat
frame wiring of the voice bridge and the Telegram `getUpdates` shape are unverified without
creds. **Casa/MCP** (home integrations) remains the open Phase-6 continuous item.

## 2026-06-29 — Phase 6 (Madurez) — first push: local heartbeat, memory layer 4, rich interfaces

Phase 6 is **continuous** (no single DoD). This first push lands the autonomous,
offline-verifiable pieces; voice + home/casa MCP are noted as the remaining
continuous items (they need audio creds / per-home servers to smoke honestly).
Built (3 slices, gates green, file ownership disjoint — config surface added up front):
- **6.A** `llm/provider.py` `LocalModelProvider` (OpenAI-compatible local endpoint,
  no API key, **cost $0**, `zero_retention`+`is_local`; unreachable→`ProviderNotConfigured`
  → existing degradation path) + `privacy.py` (deterministic stdlib PII scrubber,
  reversible placeholders, never raises) + `PseudonymizingProvider` (wraps a remote
  provider: scrub before send, de-scrub after) + `router` routine→local preference
  (`heartbeat_provider` auto/local/cloud), hard→cloud, cloud path pseudonymized unless
  the provider is local (**the hook relaxes for local** — §3-Fase 6).
- **6.B** `memory/embeddings.py` (pluggable `Embedder`: `HashingEmbedder` deterministic
  offline default + optional `OpenAICompatEmbedder` degrading to hashing; no numpy) +
  `db.py` `fact_embeddings` table/helpers + `memory/load.py` **layer 4** (semantic
  `Context.semantic_facts`, lowest `PRIORITY` so it truncates first and never touches the
  identity core; `load(query=,config=)` + explicit `index_facts()`). **Off by default** —
  existing `load()` is byte-for-byte unchanged.
- **6.C** `cli/` power subcommands (`tools/approvals/approve/deny/report/memory/fleet`,
  each `--json`) preserving the default `lhassa "prompt"`/piped/REPL via a fallback
  `TyperGroup` + `notify/telegram.py` (credential-gated, no token→no network, never
  raises) + `/api/reports`·`/api/projections`·`/api/fleet` + dashboard Reports/Fleet panels.

**DoD-style live smoke** (offline tests + a smoke against a **real in-process
OpenAI-compatible socket server**): the routine path thinks on the **local** model at
**$0** with PII **un-scrubbed** (data never leaves the box — hook relaxed); the hard/cloud
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

## 2026-06-29 — Phase 5 complete (self-improvement 2b in quarantine)

The brain can write its own tools and graduate them only through objective gates,
caged. Built (2 slices, gates green):
- **5.A** `security/quarantine.py` (`run_in_quarantine`: strongest-available sandbox
  via `unshare`/`firejail`/`bwrap` for **no-network**, a from-scratch **scrubbed env**
  so no secret leaks, temp cwd, `killpg` on timeout; `str`→`bash -c`, `list`→argv) +
  `agents/gates.py` — the **four gates of 2b**, deny-by-default, stop-at-first-failure:
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
merge a real PR) is wired as a no-op hook behind `Config.github_token` — turns on with
a new token (old one revoked). **Backlog:** more specialist domains are just new
registry rows; the retirement supervisor's "went unused" grace window is `[SUPUESTO]`.

## 2026-06-29 — Phase 4 complete (hands: MCP + harnesses)

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
in-process registry — a launched-before-restart job isn't reaped after a brain restart);
validate the adapter argv `[SUPUESTO]` flags with a live harness smoke (spends — owner's
go); a real opencode/claude/codex delegation smoke is available on request.

## 2026-06-29 — Phase 3 complete (the arbiter)

Built (2 slices, gates green):
- **3.A** `agents/verify.py` — objective verifiers (`command/tests/lint/health/regex/
  metric`), **never call an LLM** (no provider/gateway import; asserted in tests) +
  `make_verifier`/`verify` dispatcher; wired into `core/step.py`'s judgment branch via
  `router.cascade(verify=…)` (failed verification escalates cheap→smart once; the
  episode records `verificado: passed/failed`). Reused by Phase-5 gates.
- **3.B** `security/approval.py` (HITL-**per-flow** `needs_approval`: per-action risk
  + origin trust + a high-risk always-approve set; parks pending in the `approvals`
  table) + `security/autonomy.py` (a streak knob: a HUMANO action approved N× becomes
  automatic; a denial resets it; high-risk never auto-promotes; **never promotes an
  `Origin`'s trust** — contagion-only) + `local_agent` approval gate + `/api/approvals`
  + `ApprovalQueuePanel`.

**DoD met** (offline + live smoke): a `mundo`/sensitive action waits for the owner's
OK; a repeated `humano` action becomes automatic (threshold 3); move-money always
requires approval even with a streak; a denial resets the streak; the verifier never
calls an LLM. 369 tests, ruff clean, dashboard builds.

Note: 2 pre-existing eslint warnings in `App.jsx handleEvent` (untouched code) — not
introduced here; `npm run build` is clean.

## 2026-06-29 — Phase 2 complete (economy + router)

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
per-model pricing, tested); **crossing the cap stops** — verified live (kill-switch
raises, provider called 0×). 321 tests, ruff clean, dashboard builds.

**[SUPUESTO] to validate later:** ccusage JSON field names (marked in code); and the
live "a day of heartbeats costs cents" smoke needs real cheap-model calls (deferred —
won't spend without the owner's go). **Backlog:** swap to opencode Zen when available.

## 2026-06-29 — Phase 1 complete (memory)

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
similar items / detect contradictions via a cheap LLM) — Phase 1 is mechanical LRU
only. **Backlog:** reconcile the old `memory/store.py` (voice session facts/
sessions) with the new typed-facts model.

## 2026-06-29 — Phase 0 complete (the spine)

Built additively over the existing skeleton, 5 vertical slices, all behind gates
(no AI attribution, pytest green, ruff clean):
- **0.1** `core/db.py` (SQLite WAL "grande", append-only, `instance_id` on every
  table/query) + `core/episode.py` (structured Episode from the first commit).
- **0.2** `core/step.py` + `core/watchlist.py` + `core/actions.py` — the
  deterministic do-and-sleep loop (zero-token watchlist → fixed action → episode);
  the `else` branch is the Phase-2 router seam.
- **0.4** `security/`: signed `Task`, `TrustLevel`/`Origin` (degrade=min, never
  promote), Ed25519 signing, deny-by-default allowlist (= §2.6) with HITL-per-flow.
- **0.3** `/checkin` + `/report` + `remote/local_agent.py` — check-in-not-push with
  desired_state reconciliation; the allowlist gate runs on the machine.
- **0.5** `core/heartbeat.py` + systemd `lhassa-heartbeat.{service,timer}` +
  parametrized identity (`Identity`/`load_identity`, org hook #2; directives loaded
  as identity; `PERSONA_BLOCK` kept stable) + the end-to-end DoD test.

**DoD met** (offline tests + a live in-process smoke with real Ed25519/SQLite/git):
timer pulses → check-in → reconcile → signed `sync_repo` → allowlist gate → action
runs → report → two episodes explain *why* → state persists across a DB reopen →
every row carries `instance_id`. 229 tests, ruff clean.

**Living-doc correction (§4/§7):** the brief lists `OpenCodeAdapter`/`ModelProvider`
under Phase 0, but Phase 0's loop is deterministic-only and never exercises them —
so they're **deferred to Phase 2 (ModelProvider, the router/think path) and Phase 4
(HarnessAdapter)**, where they're actually used. The three org hooks are in place.

Open Phase-0 polish (backlog, not blocking): `desired_state` should map repo names
→ real paths (the smoke's `sync_repo` failed gracefully because the objetivo wasn't
resolved to a path); single-writer staging buffer (§2.7) is a later refinement.

## 2026-06-29 — adopting the implementation brief

- **Brief adopted** as the plan: Lhassa becomes a persistent "brain" (JARVIS) with
  its own identity that orchestrates models/harnesses behind interfaces — "build
  the brain, rent the body." Execution model: *do-and-sleep* (`state + step()`
  woken by a systemd heartbeat / API). See `doc/lhassa-brief.md`.
- **Existing code is the skeleton — extend, don't rewrite.** What's already built
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
- Seed facts (Appendix B) — confirm they're the owner's; `instance_id = inst_personal`.
- Second typed task (Phase 4): calendar_event / agendar_fechas / generar_documentacion /
  tracking_postulaciones.
- `[SUPUESTO]` items to validate with live smoke runs (Zen training on free data;
  Managed Agent Harness as sandbox; heartbeat provider ToS/rate-limits).
