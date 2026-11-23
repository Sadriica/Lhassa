# lhassa-ledger

**One append-only, auditable record of what every AI agent on a machine spent and touched.**

An owner now runs several agents at once: a personal assistant, a coding agent
in a worktree, an orchestrator driving a fleet. Each knows its own spend; none
knows the others'. The ledger is a host-neutral SQLite file plus thin adapters
that ride each host's own hook surface. It records. It has no opinion about how
you run agents.

This pack is a **standalone** distribution. Unlike the other packs it does not
depend on `lhassa` and ships no `lhassa.*` module, no entry point and no
registration: the library (`agent_ledger`) must load inside other people's
processes (a Claude Code hook, a Hermes gateway) with whatever Python they run.

```
agent_ledger/         the library: event, store, policy, pricing, query, cli  (stdlib only)
adapters/claude_code/ a PostToolUse hook: what the coding agent touched
adapters/hermes/      a Hermes plugin (post_api_request, pre/post_tool_call)
adapters/lhassa/      a backfill importer for Lhassa's gateway history
```

Rules the code holds to:

1. **It never raises into the host.** `Store.record()` returns False instead.
2. **It never fabricates a number.** A cost carries its source (`host`,
   `table`, `unknown`); every total states its own blind spot.
3. **It has no dependencies** and runs on Python 3.10+.
4. **It fails closed on privacy** (see "Never keep" below).
5. Tool *output* never enters the ledger. This is an audit trail, not a transcript.

## Use

```bash
uv sync --all-packages              # installs `ledger` with the rest of the workspace
ledger report --days 7              # spend by host, model, day (and machine, when reported)
ledger tail --limit 20
ledger pending                      # actions waiting on a human; exit 1 when any
ledger json --limit 100
```

The store is `~/.agent-ledger/ledger.db` (override with `AGENT_LEDGER_DB` or
`--db`). `python -m agent_ledger.cli` is the same entry point.

## Schema v2 (backward compatible)

`events` gains `instance_id` and `machine_id` (both `TEXT NOT NULL DEFAULT ''`).
Opening a `Store` migrates an older file in place with `ALTER TABLE ... ADD
COLUMN`, idempotently, and sets `meta.schema_version = 2`. No row is rewritten.

Compatibility with the previous writer is a tested guarantee: a v1 hook that
inserts with an explicit v1 column list keeps working against a migrated file
(the new columns default to `''`), and its `INSERT OR IGNORE` of the version
never downgrades it.

## Vocabulary

`kind` stays one of four values: `llm_call`, `tool_call`, `decision`, `note`.
New event types do not add kinds; they ride in `detail["type"]`:

| `detail.type` | `kind` | Meaning |
|---|---|---|
| `resource_sample` | `note` | a CPU, RAM, GPU or temperature sample |
| `file_event` | `tool_call` | a file the agent created, changed or read |
| `shell_cmd` | `tool_call` | a shell command |
| `window_focus` | `note` | the focused window changed |
| `policy_decision` | `decision` | a gate: allowed, denied or pending |
| `limit` | `decision` | a budget or rate limit was reached |

**Pending approvals.** `pending_event(host, tool, target, reason=...)` builds a
`decision` event with `outcome=pending`; `ledger pending` lists them. The
ledger never updates a row, so answering one is a second event:
`resolution_event(pending, "ok" | "denied")` names the original in
`detail.resolves`, and the pending list stops showing it.

## Never keep (fail-closed privacy)

`Store.record()` runs a policy before anything is written, for every host. If
`target`, or any path found anywhere in `detail` (shell commands are scanned
token by token), falls under a never-keep rule, the event is stored with
`target = "<withheld>"` and `detail = {"withheld": true, "reason": "<rule>"}`:
the fact that something happened survives, what it touched does not. If the
policy check itself fails for any reason, the event is withheld too.

Paths are normalized before matching: `~` and `$HOME` expanded, `..` collapsed,
symlinks resolved, and both the written and the resolved form are tested.

Built-in rules (they cannot be removed):

- directories: `~/.ssh`, `~/.gnupg`, `~/.lhassa` (also by realpath, since it may
  be a symlink to a private data folder), `~/.password-store`,
  `~/.local/share/gopass`, `~/.config/keepassxc`, `~/.config/Bitwarden`,
  `~/.config/1Password`, `~/.local/share/keyrings`
- any path with a `personal` component somewhere below a `lhassa` directory
- file names `.env`, `.env.*` and `*.kdbx`, anywhere

Extra rules go in a plain-text file named by `AGENT_LEDGER_NEVER_KEEP`. The file
can only add rules. A missing file adds nothing; an unreadable one withholds
everything until it is fixed.

```
# one rule per line
path: ~/work/secrets      # a directory (prefix match); globs allowed
name: *.pem               # a file-name glob
host: some-host           # do not keep anything from this host
session: abc123           # do not keep anything from this session
```

A bare line containing `/` or starting with `~` is a path; any other bare line
is a name glob.

**Retention.** The default is to keep everything forever, and nothing deletes
automatically. `host:` and `session:` rules mark a source as do-not-keep, and
path rules do the same for a folder: matching events are stored as the bare
trace described above. Automatic deletion is not implemented.

## Adapters

**Claude Code** (`adapters/claude_code/hook.py`) is a `PostToolUse` hook that
always exits 0 and prints nothing. It records the tool, its target, `cwd`, the
session, and `AGENT_LEDGER_MACHINE_ID` / `AGENT_LEDGER_INSTANCE_ID` when set.
`install-global.sh` adds it to `~/.claude/settings.json`; it is meant to be run
by the owner, not by an agent.

**Hermes** (`adapters/hermes/`, linked with `./install.sh`) records model calls
and tool calls from a Hermes gateway. **Lhassa** (`adapters/lhassa/import_ledger.py`)
is a one-off, idempotent backfill from the gateway's call history.

### Hook migration plan (not executed)

The machine's global hook currently points at the standalone checkout that this
pack was copied from, and it keeps working unchanged against a migrated
database (it simply leaves the v2 columns empty). Suggested order:

1. Land this pack; let any `Store` (the CLI, tests) migrate the live file.
2. Confirm the old hook still writes (`ledger tail` shows new rows).
3. Run `adapters/claude_code/install-global.sh --remove` from the old checkout,
   then `install-global.sh` from `packages/lhassa-ledger`, so the entry points
   at this pack's hook. From then on events get the never-keep policy and
   `machine_id` / `instance_id`.
4. Until step 3, events written by the old hook are **not** policy-filtered.

## Tests

```bash
uv run --all-packages --extra dev pytest packages/lhassa-ledger
```
