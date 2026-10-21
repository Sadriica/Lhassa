# Lhassa. CLI reference

The `lhassa` command is the terminal surface of the brain. With **no subcommand**
it is a **chat** turn through Lhassa's tool-calling loop; with a subcommand it
drives a specific capability. Almost every read command is offline against the
local brain DB and supports `--json` for machine-readable output.

```bash
lhassa "summarize this repo"     # one-shot from an argument
echo "data" | lhassa             # one-shot from piped stdin
lhassa                           # interactive REPL (TTY); prompt: lhassa ›
lhassa "..." --json              # {response, model, tokens, cost}
```

> A free-text prompt whose **first word is exactly a subcommand name** routes to
> that subcommand; quote it (`lhassa "report on X"`) to force a chat turn.

The REPL/chat uses the **chat provider** (`llm_provider` / `model`), which may be
an API provider (groq/anthropic/openai/openrouter/cerebras; needs its key in
`.env`) or a **local CLI** run on your subscription with **no API key**
(`claude` / `codex` / `opencode`) or `ollama`. Switch it live with
`lhassa config set llm_provider claude`. This is separate from the background
**heartbeat** model (`heartbeat_provider`).

The bare `lhassa` REPL opens on a **welcome card**: the two model seats, today's
spend, the working directory, and what is waiting on you in the next 24 hours
(calendar events, approvals, reminders; Lhassa's own upkeep tasks stay out). Type
`/` for a menu of the REPL commands (`/status`, `/agenda`, `/approvals`,
`/approve <id>`, `/deny <id>`, `/skills`, `/tools`, `/usage`, `/reset`, `/clear`,
`/exit`). Input history persists in `repl_history` next to the brain DB (`↑`).
Under the prompt sits the same two-line status the TUI shows at its bottom
(`lhassa/views/status.py`): instance, heartbeat seat, chat model, today's token
budget as a bar with its UTC reset, cost, then org mode, approvals, tasks, last
beat and health. All of it is local reads, $0.

The CLI is themed in the **Lhasa palette**: temple gold, monk-robe red (sparing),
snow white, sage green. Override the REPL's log verbosity with
`LHASSA_CLI_LOG_LEVEL` (default `WARNING`; `OFF` silences it).

---

## Chat & agentic

| Command | Purpose | Example |
|---|---|---|
| *(default)* | A chat turn; answers via the tool-calling loop | `lhassa "what's overheating my laptop?"` |
| `code <task>` | Run an **agentic** coding task through a local AI CLI (claude/codex/opencode) on your subscription; it can read/edit files and run tools in the **current directory**, streams live output | `lhassa code "add a --verbose flag to main.py"` |

`code` auto-detects an installed CLI; force one with `--cli`:

```bash
lhassa code "refactor the parser" --cli codex
```

## Observe the brain

| Command | Purpose | Example |
|---|---|---|
| `status` (alias `overview`) | The whole brain at a glance; brains, skills, pending approvals, today's cost, health, recent activity. Offline + LLM-free | `lhassa status` |
| `watch` | Tail recent activity (re-renders the latest episodes; Ctrl-C to stop) | `lhassa watch --interval 3 --n 12` |
| `report` | Cost/usage summary for a window (gateway ledger + optional ccusage) | `lhassa report --days 7 --json` |
| `memory <query>` | Search durable facts (substring or tag match) | `lhassa memory temperatura --json` |
| `memory reindex` | Re-embed every fact and every cached answer-cache question with the CURRENT embedder; run after switching `LHASSA_EMBEDDER_BACKEND`/model | `lhassa memory reindex --json` |
| `tools` | List the self-written tools and their gate/usage state | `lhassa tools` |
| `tools propose <name>` | Have the routine model author a candidate tool, run it through quarantine + the gates, and park a graduation approval on a full pass | `lhassa tools propose clean_downloads --desc "sort ~/Downloads by type"` |
| `bench run` | Score one or more subjects against the LhassaBench fixtures, printing a pass-rate table | `lhassa bench run --subject claude --subject codex` |
| `bench history` | Recent `bench_runs` rows for a fixture/subject | `lhassa bench history --fixture codegen_fibonacci --limit 20` |
| `utility survey` | Which capabilities still SERVE the owner; owner-driven vs self-driven use per registered action, least-earned first | `lhassa utility survey --all --json` |
| `utility docket` | The retirement docket: unused or self-driven, unpinned, past the grace period | `lhassa utility docket` |
| `fleet` | Known machines with their last check-in and desired state | `lhassa fleet` |
| `version` | Version, Python runtime, and a few key toggles | `lhassa version` |

`watch --once` renders a single frame and exits.

**`lhassa utility`** is the counterweight to `lhassa capabilities`: that one
answers "what is wired?", this one answers "what is EARNING its place?". It is a
pure derived read over the episode log ($0, no LLM, no new storage; a capability
leaves an Episode). Each use is bucketed by wake source into **owner** (a human
reached for it) or **self** (Lhassa scheduled or delegated it to itself), plus a
pass over `tasks.origen_actor` so deferred work the owner asked for still counts
(`/remind` mints today and fires next week). A capability with only self-driven
use reports `self_driven`: running, but not earning its place. `survey` hides
actively owner-driven capabilities unless `--all`; `docket` lists only what is
proposed for retirement. Nothing is ever retired automatically: the weekly
`review_capabilities` action parks one `retire_capability` approval per
capability, once, and `LHASSA_UTILITY_PINNED` exempts anything you want kept
deliberately dormant.

`tools propose` without `--baseline` cannot graduate; this is BY DESIGN, not
a bug: gate 3 ("mejor") requires beating a concrete numeric baseline, and a
net-new candidate has nothing yet to prove itself better than. The first real
graduations come from the repetition detector's own proposals
(`agents/repetition.py`), which already know the pattern's cost and supply it
as the baseline automatically; `tools propose` stays the manual, stricter
door.

**`tools propose --max-turns N`** (default 3) makes the forge an iterating
loop rather than a single attempt: a failing gate's own detail (never a
self-judgment) threads into the *next* authoring turn as feedback, up to `N`
turns, each logged as its own Episode. One failure is deliberately
non-retryable; a "mejor" failure with no baseline/metric to compare against
stops the loop immediately, since no amount of feedback can conjure a
baseline that was never given. See `doc/tools.md` for the turn-by-turn detail.

**`lhassa bench run`** scores installed subjects (`--subject`/`-s`, repeatable;
default: whichever of `claude`/`codex`/`default` is actually runnable) against
LhassaBench's fixtures (`--fixture`/`-f`, repeatable; default: all 10) using
the SAME objective verifiers the árbitro uses elsewhere; never an LLM judge;
and prints a pass-rate/cost table. **`lhassa bench history`** lists past
`bench_runs` rows (`--fixture`, `--subject`, `--limit`, default 50).

## `lhassa project`: the workspace entity

A project is a named, TYPED unit of the owner's work (`kind`: `code` /
`research` / `ops` / `personal` / `generic`, open; `kind` is what the
standing-brains reflex keys off). Creation is an explicit owner act; the CLI,
a chat marker, or an approved proposal; never silent.

| Subcommand | Purpose | Example |
|---|---|---|
| `create <name>` | Create a project (refuses a duplicate active name) | `lhassa project create lhassa --kind code --path .` |
| `list` | List projects, newest first (`--all` includes archived, `--kind`) | `lhassa project list --kind code` |
| `show <ref>` | One project in full; id, name, kind, path, metadata | `lhassa project show lhassa` |
| `archive <ref>` | Archive (never deleted; excluded from active listings) | `lhassa project archive lhassa` |
| `runs <ref>` | Runs scoped to the project, with their evidence rung (`prepared`/`observed`/`verified`) | `lhassa project runs lhassa --limit 20` |
| `artifacts <ref>` | Artifacts delivered for the project, newest first | `lhassa project artifacts lhassa` |

`<ref>` resolves an id, an exact name, or a filesystem path that lives inside
the project's own `path` (the deepest-rooted project wins on a nested match).

A `code` project with no standing brains yet gets its review pair
(`code-review` by default; override via `LHASSA_KIND_TEMPLATES`) proposed
automatically by a $0 watchlist sense; see `doc/security.md` for the
approval gate that guards the attach.

## `lhassa tui`: the brain in a terminal

`lhassa tui` (the `lhassa-tui` pack, `packages/lhassa-tui`, module `lhassa.tui`;
absent when the pack is not installed) launches a Textual terminal app with eight tabs (Overview / Projects /
Runs / Approvals / Flows / Skills / Memory / Org), reading `lhassa/core/db/`,
`flows/store.py`, `skills/store.py` and `org/topology.py` **directly**: it works
with the API server down. Approve/deny on the Approvals tab go through the
same `security/approval.py:resolve()` seam the CLI/API use, never bypassed; running
a flow goes through `org/delegate.py:delegate_flow`. Table tabs refresh live and
skip the redraw when nothing changed. The palette follows `LHASSA_TUI_THEME`
(auto/dark/light).

| Key | Where | Action |
|---|---|---|
| `a` | Approvals tab | Approve the selected row |
| `d` | Approvals tab | Deny the selected row |
| `x` | Flows tab | Run the selected flow |
| `/` | table tabs | Filter rows (case-insensitive substring); `escape` clears it |
| `ctrl+p` | anywhere | Command palette (navigation and app actions, never mutations) |
| `?` / `F1` | anywhere | Keyboard help |
| `r` | any tab | Refresh |
| `q` | anywhere | Quit |

## Work, projects and data

| Command | Purpose | Example |
|---|---|---|
| `flows list/show/templates/apply` | Inspect stored work-flows; instantiate a gallery template under a fresh id | `lhassa flows apply research-brief` |
| `flow list/show/templates/create/edit/validate/run/watch/logs/runs/delete` | Author a flow (`create --template` or `--blank`, `edit` opens `$EDITOR`, `validate` exits 1 if invalid), run it THROUGH the org hierarchy (`run` uses `delegate_flow`, `--input` JSON, `--idempotency-key`), follow a durable run (`watch`, `logs`, `runs`) | `lhassa flow run <flow-id> --json` |
| `task add/list/done/reject` | Mint a standing goal or scheduled action (`--due`, `--every hourly\|daily\|weekly\|<seconds>`; `--think` lets any text through as a judgment task) | `lhassa task add send_digest --every daily` |
| `memory add/graph/entities/reindex` | Write a durable fact; `graph <term>` answers "what do I know about X?" from facts and episodes ($0) | `lhassa memory graph sezzle` |
| `knowledge ingest/search/clear` | Index the configured knowledge roots; offline cosine search; wipe the index | `lhassa knowledge search "thesis outline"` |
| `calendar add/list/remove` | Named `.ics` calendar feeds (urls shown masked) | `lhassa calendar add Work <ics-url>` |
| `artifacts list/show` | The artifact registry, the delivery chain's audit trail | `lhassa artifacts list` |
| `seed list/apply/apply-all` | Reproducible bootstrap seeds: `cv`, `digest`, `maintenance`, `temple`, `research`, `personal` | `lhassa seed apply maintenance` |
| `share publish/unpublish/grant/revoke/offerings/subscribe/unsubscribe/subscriptions` | Cross-instance sharing of skills, flows, templates and knowledge, deny-by-default | `lhassa share publish skill code-review` |
| `research draft/list/assessments/assessment/propose-change/build-change/apply-change` | The research line: evidence-cited note drafts (`draft <topic> --lang en\|es`) and the idea-assessment pipeline | `lhassa research draft earned-autonomy` |
| `integrity check/status/findings/contracts` | Run and inspect the deterministic conformance contracts (`doc/integrity.md`) | `lhassa integrity status` |
| `setup` | One wizard from a fresh clone: profile, scopes, seeds, services (`--yes`, `--profile`, `--dry-run`, `--no-services`) | `lhassa setup --yes --profile personal` |
| `onboard` | Interactive questionnaire (~5 questions) that seeds the first owner facts | `lhassa onboard` |

## Approvals (HITL)

| Command | Purpose | Example |
|---|---|---|
| `approvals` | List the pending approval queue (oldest first) | `lhassa approvals` |
| `approve <id>` | Approve a pending item (feeds the autonomy streak knob) | `lhassa approve 42` |
| `deny <id>` | Deny a pending item (resets its autonomy streak) | `lhassa deny 42` |

## Run the services

| Command | Purpose | Example |
|---|---|---|
| `serve` | Run the dashboard + REST API (Ctrl-C stops it) | `lhassa serve --host 127.0.0.1 --port 7766` |
| `beat` | Run **one** heartbeat tick; exactly what the systemd timer runs. Deterministic, $0; advances the brain one step | `lhassa beat` |

## Capabilities / health

| Command | Purpose | Example |
|---|---|---|
| `capabilities` (alias `doctor`) | What is wired and what is on/off; every registry + gated config + loaded plugins. LLM-free, never raises | `lhassa doctor` |

---

## `lhassa config`: runtime settings

Reads and changes an **allowlist** of runtime settings, persisting to
`~/.lhassa/settings.env` (loaded after `.env` with override; **secrets stay in
`.env`** and are never written here) and applying to the live `Config`.

| Subcommand | Purpose | Example |
|---|---|---|
| `list` | List every editable setting, grouped, secrets masked | `lhassa config list` |
| `get <key>` | One setting's value, default, and overridden flag | `lhassa config get heartbeat_provider` |
| `set <key> <value>` | Change a setting (persist + apply live) | `lhassa config set llm_provider claude` |

Editable keys (38, by group; `lhassa config list` is the authoritative list):
**Heartbeat** `heartbeat_provider`, `opencode_model`, `fallback_providers` ·
**Models** `llm_provider`, `model`, `model_cheap`, `model_smart`, `router_mode` ·
**Budget & Safety** `llm_kill_switch`, `llm_daily_token_budget`, `budget_soft_pct`,
`deferred_max_retries`, `pseudonymize_remote` · **Memory** `memory_layer4_enabled`,
`memory_distill_enabled`, `embeddings_url`, `embeddings_model`,
`context_token_budget_minimal`, `knowledge_roots`, `protected_roots`,
`answer_cache_enabled`, `answer_cache_threshold` · **Surfaces** `notify_style`,
`voice_enabled`, `telegram_poll_enabled`, `telegram_history_turns`,
`telegram_provider`, `tui_theme`, `calendar_ics_url`, `calendar_feed_ttl`,
`notify_channels`, `digest_style`, `notify_language`, `digest_language`,
`digest_personal_checkin` · **Watchlist** `auto_sync_repos` · **Org** `org_live`,
`org_departments`. Settings consumed only by the heartbeat take effect on the next
beat; others carry a restart hint.

---

## `lhassa org`: the brain hierarchy (1..N brains)

Lhassa is the permanent **root administrator** (`LHASSA_ROOT_ADMIN`, default
`lhassa`): it always roots the chart and is never removed or demoted. The
topology lives in `~/.lhassa/org.json`.

| Subcommand | Purpose | Example |
|---|---|---|
| `tree` | Print the hierarchy as a tree (root → … → leaves) | `lhassa org tree` |
| `list` | List every brain (node) | `lhassa org list` |
| `show <id>` | One brain's role/parent/department/skills/limits | `lhassa org show backend` |
| `add` | Create a brain and persist it | `lhassa org add --id qa --parent delivery --skills testing` |
| `remove <id>` | Remove a brain (refuses to orphan children or drop a populated root) | `lhassa org remove qa` |
| `move <id> <parent>` | Re-parent a brain; change who commands whom | `lhassa org move qa backend` |
| `promote <id>` | Promote to a top-level coordinator under the administrator | `lhassa org promote backend` |
| `set-skills <id> <csv>` | Define the skills a brain handles (replaces the set) | `lhassa org set-skills backend "api,db"` |
| `set-limits <id> <spec>` | Set a brain's limitations (JSON or `k=v`) | `lhassa org set-limits backend budget_usd=1.0` |
| `skills` | List the skills that CAN be assigned (specialist registry domains) | `lhassa org skills` |
| `route <goal>` | Show WHERE a goal would route (root → … → leaf), no execution | `lhassa org route "optimize the build"` |
| `run <goal>` | Run a goal end-to-end (offline unless `LHASSA_ORG_LIVE=1`) | `lhassa org run "optimize the build"` |
| `templates` | List the use-case org templates | `lhassa org templates` |
| `apply <id>` | Apply a template; create its brains under Lhassa (idempotent) | `lhassa org apply software-factory` |
| `monitor` | Run one monitoring pass (anomalies, risks, routed improvements); offline and $0 unless `LHASSA_ORG_LIVE=1` | `lhassa org monitor --json` |

Templates: `code-review` (2), `bug-investigation` (2), `refactor-migration` (3),
`incident-response` (4), `software-factory` (5; delivery over frontend/backend/
qa/devops).

---

## `lhassa skills`: first-class skills

A skill is **3-layer**: a NAME on a brain (intent) → a `SKILL.md` in
`~/.lhassa/skills/` (capability, YAML frontmatter + instructions) → loaded into
the brain's prompt at think time (effect). Assigning a skill offers to install it.

| Subcommand | Purpose | Example |
|---|---|---|
| `list` | List every skill in the store | `lhassa skills list` |
| `show <name>` | A skill's frontmatter + instructions + resources | `lhassa skills show code-review` |
| `create <name>` | Create a skill; blank, or drafted with `--ai` from `--from-brief` | `lhassa skills create triage --ai --from-brief "first-response incident triage"` |
| `improve <name>` | Improve a skill with AI (clearer instructions + description) | `lhassa skills improve triage` |
| `delete <name>` | Delete a skill from the store | `lhassa skills delete triage` |
| `assign <name> <brain>` | Add a skill to a brain's skill set | `lhassa skills assign code-review backend` |
| `who-has <name>` | Which brains have a skill | `lhassa skills who-has code-review` |
| `recommend [context]` | Recommend catalog skills not yet installed (offline, no model) | `lhassa skills recommend "python web app"` |
| `import <path>` | Import an external skill into the store | `lhassa skills import ./my-skill` |
| `export <name>` | Export a skill folder (`--to`, or report the stored path) | `lhassa skills export code-review --to ~/share` |
| `seed` | Seed the starter skills into the store (idempotent) | `lhassa skills seed` |

`--ai`/`improve` degrade gracefully when no model is available.

---

## Not (yet) in the CLI; dashboard + API only

One org surface has **no dedicated `lhassa` subcommand yet**; drive it from the
dashboard or the REST API:

- **Typed relationships**: the overlay graph on the command tree (edge kinds
  `reads` / `notifies` / `reviews` / `collaborates`, plus the instance-scoped
  `shares`, which `lhassa share grant` writes). Stored at
  `~/.lhassa/relationships.json` (`LHASSA_ORG_RELATIONSHIPS_FILE`). API:
  `GET /api/org/relationships` (+ `/kinds`), `POST`/`DELETE /api/org/relationships`.
  On the dashboard the **Brains** canvas draws them and the inspector edits them.

Work-flows (`lhassa flow`, `lhassa flows`) and the agentic run surface
(`lhassa code "<task>"`) have CLI doors, documented above.

---

See also: [`architecture.md`](architecture.md) for the layers and data dir,
[`lhassa-brief.md`](lhassa-brief.md) for the spec, and the `LHASSA_*` config
table in the [README](../README.md).
</content>
</invoke>
