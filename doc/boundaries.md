# Boundaries; what is public code and what is the owner's

Lhassa is public from day one, and its owner uses it for real life. Those two
facts coexist because of ONE line, drawn in the architecture and enforced here:

> **The repository is the brain. `~/.lhassa/` is the person.**

Everything in git is machinery anyone may run; everything the machinery learns,
schedules, tailors or remembers about its owner lives outside this repository,
on the owner's disk.

## What lives where

| Public (this repo) | Personal (`~/.lhassa/`, never in this repo) |
|---|---|
| The code: core, memory, security, org, flows engine, surfaces | `brain.db`: episodes, facts, tasks, approvals (the lived history) |
| **Seeds**: generic, re-runnable presets (`cv`, `digest`, `maintenance`, `personal`) | `flows.json`: the owner's actual work-flows, including customized ones |
| Docs, tests, the narrative | `org.json` / `relationships.json`: the owner's real org chart |
| `.env.example`: names of knobs, never values | `.env` + `settings.env`: credentials and runtime choices |
| The dashboard and CLI | `cv/`: the real CV and its tailored outputs |
| | `skills/`: installed skill content and its usage telemetry |
| | Ed25519 keys, backups, logs |

## Versioning the personal side

`~/.lhassa/` may be its own git repository, kept separate from this one and
private (or local only). The pattern that works: ignore everything by default
(`*`), then re-include only the living text (`flows.json`, `org.json`,
`audit.json`, `cv/`, `skills/`), and keep databases, keys, `settings.env`,
`calendars.json` and regenerable state out even if a broader rule would let
them in. Databases are backed up by the `backup_memory` action, not by git.
`~/.lhassa` can also be a symlink to wherever that repository lives; every
path Lhassa opens resolves through it.

## The rules (enforced, not aspirational)

1. **Seeds are presets, not possessions.** A seed may enter the repo only if it
   is generic; a template anyone can apply (`lhassa seed apply personal` gives
   *any* owner morning/evening briefings). The owner's customized instances of
   those flows live in `flows.json`, which never enters git. If a personal
   flow can't be generalized, it stays personal: author it in the dashboard or
   via the API, and it lives only on disk.
2. **Test fixtures use invented identities only.** Emails like
   `owner@example.edu`, phones like `+57 300 111 2222`. Real contact details,
   real employer names in fixtures are a bug; treat a violation like a leaked
   secret (fix + history rewrite before any push).
3. **Secrets never enter the tree.** `.env` is gitignored and has never been
   committed; docs show `gsk_...` placeholders only. The CI badge is the only
   external reference.
4. **`doc/decisions.md` records engineering, not life.** It may say "the CV
   work-flow shipped"; it must not carry the owner's contact details, employers
   applied to, chat ids or ledger identities. When in doubt, generalize.
5. **The example is the seed; the instance is yours.** When the owner builds
   something personal worth sharing, the path to public is: strip the personal
   specifics → register it as a seed (a preset) → the personal instance stays
   in `~/.lhassa/`.

## Pre-public checklist (repeat before flipping the repo visibility)

- `git grep` the tree AND history for: real emails, phone patterns, employer
  names, chat ids, key prefixes (`gsk_`, `ghp_`, `sk-`).
- Confirm `.env` and `~/.lhassa/` artifacts are absent from every commit.
- Decide the commit author identity (a personal email in `git log` is standard
  but public. GitHub's noreply address is the private alternative).
- Read `doc/decisions.md` once with a stranger's eyes.
