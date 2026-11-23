# lhassa review: deterministic pre-flight review

`lhassa review` checks what is about to leave your machine (commit messages,
authorship, added lines, text you are about to write) against a fixed set of
rules. It uses no model: the same input always gives the same findings, it costs
nothing, and it runs in milliseconds.

It has three doors onto one engine (`lhassa/review/`):

| Door | Entry point | Mode |
|---|---|---|
| Git hooks | `scripts/hooks/commit-msg`, `scripts/hooks/pre-push` | warn; exit 0 unless strict |
| Claude Code `PreToolUse` | `scripts/hooks/claude-pretooluse` | warn and log; fail open |
| CLI | `lhassa review commit-range RANGE`, `message FILE`, `text FILE` | report; exit 0 unless `--strict` |

The engine is stdlib-only (plus the secret shapes in `lhassa/privacy.py`), so
the hooks start fast and do not need the full runtime. They run with
`python3 -m lhassa.review` straight from the checkout, or with whatever
`LHASSA_REVIEW_CMD` says.

## Rules

| Rule | Severity | What it flags |
|---|---|---|
| `author.unexpected` | warn | author or committer not matching `allowed_authors` (only checked when that list is set) |
| `author.unwanted-email` | warn | author or committer email matching `unwanted_emails` |
| `ai.trailer` | critical | `Co-Authored-By` naming an assistant, or `Generated-by`-style trailers |
| `ai.mention` | critical | attribution phrases such as "Generated with ..." in a commit message |
| `emdash` | warn | U+2014 in a message or in added content |
| `secret.prefixed` | critical | `ghp_`, `sk-`, `AKIA`, `xox*` and similar prefixed keys |
| `secret.private-key` | critical | a private key header |
| `secret.long-token` | warn | a 32+ character token mixing upper, lower and digits (hashes and identifiers are skipped) |
| `name.forbidden` | warn | a personal name or nickname from your config (whole word, case-insensitive) |

AI attribution rules apply to commit messages only: a document may legitimately
talk about a model provider. A finding never echoes a secret or a configured
name; it gives the rule, severity, commit or file, and line.

## Configuration (yours, never in the repo)

Personal data stays out of the repository (see `doc/boundaries.md`). Everything
personal is read from `~/.lhassa/review/config.json` (override the file with
`LHASSA_REVIEW_CONFIG`, the directory with `LHASSA_REVIEW_DIR`):

```json
{
  "allowed_authors": ["you@example.org", "Your Name <you@example.org>"],
  "unwanted_emails": ["noreply@anthropic.com", "*@old-employer.example"],
  "forbidden_names": ["a nickname", "a legal name"],
  "ignore_paths": ["*.lock", "vendor/*"],
  "emdash_ignore_paths": ["lhassa/persona.py"],
  "secret_ignore": ["^ghp_EXAMPLE"],
  "block_on": []
}
```

`allowed_authors` and `unwanted_emails` are `fnmatch` patterns. `block_on` lists
rule ids whose critical findings may deny a Claude Code tool call; it is empty
by default, so nothing is ever blocked until you opt a rule in. A missing or
broken config file degrades to defaults and is noted in the log.

## The run log

Every run appends one JSON line to `~/.lhassa/review/review.jsonl` (directory
`0700`, file `0600`, append-only). The pre-push hook records every push it sees
(remote name, URL with credentials stripped, refs, whether a branch is new) even
when it finds nothing. A push made with `--no-verify` skips the hook, so it
cannot be seen; the absence of a record for a push is itself the signal.

## Using it from the CLI

```bash
lhassa review commit-range origin/main..HEAD
lhassa review message .git/COMMIT_EDITMSG
lhassa review text docs/draft.md --json
lhassa review commit-range origin/main..HEAD --strict --fail-on critical
```

## Install plan: git hooks (global, opt-in)

Nothing here is installed by the repo. Git only looks in one hooks directory, so
a global `core.hooksPath` hides every repo's own `.git/hooks`. The directory
`scripts/hooks/` handles that: each standard hook name that is not reviewed is a
symlink to `_passthrough`, and `commit-msg` and `pre-push` run the review and
then call the repo's own hook of the same name (`<git-common-dir>/hooks/<name>`)
with the same arguments and stdin, returning its exit status. Hooks that a repo
manages through its own `core.hooksPath` setting still win for that repo, since
a repo-local setting overrides the global one.

```bash
git config --global core.hooksPath <lhassa-checkout>/scripts/hooks
```

Strict mode is opt-in per shell or per repo: `LHASSA_REVIEW_STRICT=1`.
Reverse with `git config --global --unset core.hooksPath`.

## Install plan: Claude Code `PreToolUse`

Add one matcher group to the `PreToolUse` array of `~/.claude/settings.json`
(append; keep any existing groups):

```json
{
  "matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit",
  "hooks": [
    {
      "type": "command",
      "command": "<lhassa-checkout>/scripts/hooks/claude-pretooluse",
      "timeout": 20
    }
  ]
}
```

The adapter reads the hook JSON on stdin. For a `Bash` call containing
`git commit` it reviews the command text (the message), the identity git would
use and the staged additions; for `git push` it reviews the unpushed commits.
For `Write`, `Edit` and `MultiEdit` it reviews the new content.

Behaviour:

- Findings are returned as `additionalContext` for the model plus a
  `systemMessage` for you. The adapter deliberately returns no
  `permissionDecision`: `allow` would skip your permission prompt.
- It fails open. Broken JSON, a missing interpreter, a git error or any
  internal exception means exit 0 with no output (the call proceeds), and the
  error is logged.
- Only a critical finding whose rule is in `block_on` returns
  `permissionDecision: "deny"`.

Remove the matcher group to uninstall.

## Known limits

- Content rules run on added lines only, never on the whole file.
- `secret.long-token` skips all-hex strings, so a raw hex API key is missed.
- A legacy file that already contains dashes will trigger `emdash` on any new
  line with one; list such files in `emdash_ignore_paths`.
- The engine cannot see a commit made with `--no-verify`, or a push that never
  reaches the hook.
