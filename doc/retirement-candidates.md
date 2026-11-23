# Retirement candidates (backlog, not a decision)

The utility tribunal (`review_capabilities`, weekly) proposed retiring dozens of
registered capabilities. The owner closed the whole queue on 2026-11-24 and kept
every capability. Five of them looked like real candidates for a future cleanup:
no recorded use by anyone, no structural dependency beyond their own registration,
and an equivalent that already exists. They are listed here so a later review can
weigh them without re-deriving the evidence. **Nothing here is scheduled.**

Approving a `retire_capability` approval never removed anything (nothing consumes
it); retiring one of these means changing code: drop its registration in
`lhassa/actionpack/__init__.py`, its row in `lhassa/security/allowlist.py` and its
line in `lhassa/notify/phrasebook.py`, then fix the tests that name it.

| Capability | Recorded uses when proposed | What depends on it | Equivalent that exists |
|---|---|---|---|
| `git_status_repos` | 0 (proposed 2026-08-25) | its registration, allowlist row, phrasebook line and tests (`allowlist.py:128`) | the health digest already reports repos with uncommitted work |
| `list_approvals` | 0 (2026-08-25) | registration and allowlist row (`allowlist.py:153`) | `lhassa approvals`, `GET /api/approvals` |
| `ping_url` | 0 (2026-09-01) | `actionpack/_net.py` and its allowlist row (`allowlist.py:158`) | `web_fetch`, `run_command` |
| `system_snapshot` | 0 (2026-09-15) | `actionpack/_system.py` (one mention in a `host.py` docstring) | the health checks and `lhassa status` |
| `power_mode` | 0 (2026-09-01) | `actionpack/_system.py:112`, `tools/privileged.py`, `scripts/lhassa-sudoers` | the `power-mode` script itself (the status bar calls it directly) |

## Why this is a backlog and not a retirement

* Lhassa is a public product: "unused by its author in this instance" does not mean
  "unused". These five were chosen because nothing structural needs them, not
  because nobody could.
* Retiring `power_mode` also shrinks the privileged surface (it is the only action
  that shells out through the sudoers rule), which is a reason to do it first.
* A fresh record of use is worth having first: the CLI and chat doors that log a
  capability use only exist since 2026-09-28, so a zero from before that date proves
  little.

## Capabilities that looked dormant and are not

Most of the 37 proposals named capabilities that are structural (`add_fact`,
`add_task`, `org_run`, `run_script`, `set_task_state`, `sync_repo`) or the
maintenance machinery the heartbeat itself runs (`temple_run`, `send_digest`,
`notify`, `distill_memory`, `detect_repetition`), or capabilities the owner did use
more than 30 days earlier. The tribunal's `self_driven` label means "no owner use in
30 days", which is not "only Lhassa uses it". See the generator fix that separates
those states.
