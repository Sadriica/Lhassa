# Lhassa — CLI reference

The `lhassa` command is the terminal surface of the brain. With **no subcommand**
it is a **chat** turn through Lhassa's tool-calling loop; with a subcommand it
drives a specific capability. Almost every read command is offline against the
local brain DB and supports `--json` for machine-readable output.

```bash
lhassa "summarize this repo"     # one-shot from an argument
echo "data" | lhassa             # one-shot from piped stdin
lhassa                           # interactive REPL (TTY) — prompt: lhassa ›
lhassa "..." --json              # {response, model, tokens, cost}
```

> A free-text prompt whose **first word is exactly a subcommand name** routes to
> that subcommand — quote it (`lhassa "report on X"`) to force a chat turn.

The REPL/chat uses the **chat provider** (`llm_provider` / `model`), which may be
an API provider (groq/anthropic/openai/openrouter/cerebras — needs its key in
`.env`) or a **local CLI** run on your subscription with **no API key**
(`claude` / `codex` / `opencode`) or `ollama`. Switch it live with
`lhassa config set llm_provider claude`. This is separate from the background
**heartbeat** model (`heartbeat_provider`).

The CLI is themed in the **Lhasa palette** — temple gold, monk-robe red (sparing),
snow white, sage green. Override the REPL's log verbosity with
`LHASSA_CLI_LOG_LEVEL` (default `WARNING`; `OFF` silences it).

---

## Chat & agentic

| Command | Purpose | Example |
|---|---|---|
| *(default)* | A chat turn — answers via the tool-calling loop | `lhassa "what's overheating my laptop?"` |
| `code <task>` | Run an **agentic** coding task through a local AI CLI (claude/codex/opencode) on your subscription — it can read/edit files and run tools in the **current directory**, streams live output | `lhassa code "add a --verbose flag to main.py"` |

`code` auto-detects an installed CLI; force one with `--cli`:

```bash
lhassa code "refactor the parser" --cli codex
```

## Observe the brain

| Command | Purpose | Example |
|---|---|---|
| `status` (alias `overview`) | The whole brain at a glance — brains, skills, pending approvals, today's cost, health, recent activity. Offline + LLM-free | `lhassa status` |
| `watch` | Tail recent activity (re-renders the latest episodes; Ctrl-C to stop) | `lhassa watch --interval 3 --n 12` |
| `report` | Cost/usage summary for a window (gateway ledger + optional ccusage) | `lhassa report --days 7 --json` |
| `memory <query>` | Search durable facts (substring or tag match) | `lhassa memory temperatura --json` |
| `tools` | List the self-written tools and their gate/usage state | `lhassa tools` |
| `tools propose <name>` | Have the routine model author a candidate tool, run it through quarantine + the gates, and park a graduation approval on a full pass | `lhassa tools propose clean_downloads --desc "sort ~/Downloads by type"` |
| `fleet` | Known machines with their last check-in and desired state | `lhassa fleet` |
| `version` | Version, Python runtime, and a few key toggles | `lhassa version` |

`watch --once` renders a single frame and exits.

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
| `beat` | Run **one** heartbeat tick — exactly what the systemd timer runs. Deterministic, $0; advances the brain one step | `lhassa beat` |

## Capabilities / health

| Command | Purpose | Example |
|---|---|---|
| `capabilities` (alias `doctor`) | What is wired and what is on/off — every registry + gated config + loaded plugins. LLM-free, never raises | `lhassa doctor` |

---

## `lhassa config` — runtime settings

Reads and changes an **allowlist** of runtime settings, persisting to
`~/.lhassa/settings.env` (loaded after `.env` with override; **secrets stay in
`.env`** and are never written here) and applying to the live `Config`.

| Subcommand | Purpose | Example |
|---|---|---|
| `list` | List every editable setting, grouped, secrets masked | `lhassa config list` |
| `get <key>` | One setting's value, default, and overridden flag | `lhassa config get heartbeat_provider` |
| `set <key> <value>` | Change a setting (persist + apply live) | `lhassa config set llm_provider claude` |

Editable keys (groups): **Heartbeat** `heartbeat_provider`, `opencode_model`,
`fallback_providers` · **Models** `llm_provider`, `model`, `model_cheap`,
`model_smart`, `router_mode` · **Budget & Safety** `llm_kill_switch`,
`llm_daily_token_budget`, `pseudonymize_remote` · **Memory**
`memory_layer4_enabled` · **Surfaces** `voice_enabled`, `telegram_poll_enabled`,
`notify_channels` · **Org** `org_departments`. Settings consumed only by the
heartbeat take effect on the next beat; others carry a restart hint.

---

## `lhassa org` — the brain hierarchy (1..N brains)

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
| `move <id> <parent>` | Re-parent a brain — change who commands whom | `lhassa org move qa backend` |
| `promote <id>` | Promote to a top-level coordinator under the administrator | `lhassa org promote backend` |
| `set-skills <id> <csv>` | Define the skills a brain handles (replaces the set) | `lhassa org set-skills backend "api,db"` |
| `set-limits <id> <spec>` | Set a brain's limitations (JSON or `k=v`) | `lhassa org set-limits backend budget_usd=1.0` |
| `skills` | List the skills that CAN be assigned (specialist registry domains) | `lhassa org skills` |
| `route <goal>` | Show WHERE a goal would route (root → … → leaf), no execution | `lhassa org route "optimize the build"` |
| `run <goal>` | Run a goal end-to-end (offline unless `LHASSA_ORG_LIVE=1`) | `lhassa org run "optimize the build"` |
| `templates` | List the use-case org templates | `lhassa org templates` |
| `apply <id>` | Apply a template — create its brains under Lhassa (idempotent) | `lhassa org apply software-factory` |

Templates: `code-review` (2), `bug-investigation` (2), `refactor-migration` (3),
`incident-response` (4), `software-factory` (5 — delivery over frontend/backend/
qa/devops).

---

## `lhassa skills` — first-class skills

A skill is **3-layer**: a NAME on a brain (intent) → a `SKILL.md` in
`~/.lhassa/skills/` (capability, YAML frontmatter + instructions) → loaded into
the brain's prompt at think time (effect). Assigning a skill offers to install it.

| Subcommand | Purpose | Example |
|---|---|---|
| `list` | List every skill in the store | `lhassa skills list` |
| `show <name>` | A skill's frontmatter + instructions + resources | `lhassa skills show code-review` |
| `create <name>` | Create a skill — blank, or drafted with `--ai` from `--from-brief` | `lhassa skills create triage --ai --from-brief "first-response incident triage"` |
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

## Not (yet) in the CLI — dashboard + API only

Two org surfaces have **no dedicated `lhassa` subcommand yet**; drive them from the
dashboard or the REST API:

- **Typed relationships** — the overlay graph on the command tree (edge kinds
  `reads` / `notifies` / `reviews` / `collaborates`). Stored at
  `~/.lhassa/relationships.json` (`LHASSA_ORG_RELATIONSHIPS_FILE`). API:
  `GET /api/org/relationships` (+ `/kinds`), `POST`/`DELETE /api/org/relationships`.
  On the dashboard the **Brains** canvas draws them and the inspector edits them.
- **Work-flows** — n8n-style DAGs wiring `trigger → task → gate → output` (+ branch).
  Stored at `~/.lhassa/flows.json` (`LHASSA_FLOWS_FILE`). API: `GET/POST /api/flows`,
  `GET/PUT/PATCH/DELETE /api/flows/{id}`, `POST /api/flows/{id}/run` (offline/stubbed
  unless `LHASSA_ORG_LIVE`). The dashboard **Flows** section is the editor.

The agentic run surface (`POST /api/code`, the dashboard **Run** section) **does**
have a CLI door — `lhassa code "<task>"`, documented above.

---

See also: [`architecture.md`](architecture.md) for the layers and data dir,
[`lhassa-brief.md`](lhassa-brief.md) for the spec, and the `LHASSA_*` config
table in the [README](../README.md).
</content>
</invoke>
