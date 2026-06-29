# Memory

Potts remembers you across sessions. After each conversation, it automatically extracts facts about you and saves a summary. The next time you start Potts, that context is injected into the system prompt so the assistant picks up where it left off.

---

## Two Types of Memory

### Facts

Facts are persistent key/value pairs about you and your preferences. They're extracted automatically at the end of every session by sending the full conversation to the LLM with this instruction:

> "Extract new or updated facts about the user (name, job, projects, preferences, OS, etc.) and return JSON."

Examples of facts Potts might store:

| Key | Value |
|---|---|
| `name` | Alice |
| `job` | Software engineer |
| `current_project` | Potts voice assistant |
| `operating_system` | Arch Linux |
| `preferred_language` | Python |
| `timezone` | Europe/Madrid |

Facts are stored with a `key` → `value` schema. If a fact key already exists, it's updated (upserted) — the new value replaces the old one. Keys are `snake_case`.

Facts are **persistent** — they survive restarts and accumulate over many sessions. Once Potts knows your name, it always knows your name.

### Session Summaries

A session summary is a single sentence describing what was discussed in one conversation. Examples:

- "Discussed the Tailwind v4 upgrade and set a reminder to review the pull request."
- "Helped debug a Python asyncio issue and explained context managers."
- "Checked emails, created a calendar event for Friday, and searched for miso soup recipes."

The **5 most recent session summaries** are injected into the system prompt at startup, giving Potts a sense of recent conversational history without overwhelming the context window.

---

## Where Memory Is Stored

```
~/.potts/memory.db
```

This is an **SQLite** database. Three tables:

```sql
-- Persistent facts about the user
CREATE TABLE facts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    key      TEXT NOT NULL UNIQUE,
    value    TEXT NOT NULL,
    updated  TEXT NOT NULL       -- ISO 8601 UTC timestamp
);

-- One-sentence summaries of past sessions
CREATE TABLE sessions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date     TEXT NOT NULL,      -- ISO 8601 UTC timestamp
    summary  TEXT NOT NULL
);

-- Scheduled reminders (see scheduler.md)
CREATE TABLE scheduled_tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    run_at     TEXT NOT NULL,    -- ISO 8601 UTC
    message    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
```

The `~/.potts/` directory is created automatically on first run.

---

## How Memory Is Injected at Startup

When Potts starts, it reads facts and the 5 most recent sessions from the database and builds a memory block:

```
## What you know about the user
- name: Alice
- job: Software engineer
- current_project: Potts voice assistant
- operating_system: Arch Linux

## Recent conversations
- [2026-03-20]: Helped debug a Python asyncio deadlock and explained event loops.
- [2026-03-22]: Searched for miso soup recipes and set a 6pm dinner reminder.
- [2026-03-25]: Discussed the trade-offs between PostgreSQL and SQLite for local apps.
- [2026-03-27]: Created a calendar event for Friday and reviewed the nginx config.
- [2026-03-28]: Checked emails and delegated a code refactor to the code agent.
```

This block is appended to the system prompt. Potts "knows" this information at the start of every session without you having to repeat yourself.

On the very first session (empty database), no memory block is injected and Potts logs:

```
No prior memory — first session.
```

---

## When Memory Is Saved

Memory is saved **when Potts shuts down** (Ctrl+C or any clean exit). The sequence is:

1. The voice pipeline stops
2. The full conversation history (user and assistant messages) is gathered
3. A Groq LLM call is made with the extraction prompt
4. Facts are upserted into the database
5. A session summary is saved

**Sessions shorter than 2 real exchanges are skipped** — Potts won't burn an API call to remember a one-word wake-word test.

If the session save fails (e.g. network timeout), Potts logs a warning but does not crash:

```
12:45:14 | WARNING  | Could not save session: [error details]
```

---

## Viewing Memory in the Dashboard

The dashboard at `http://localhost:7766` has two memory-related panels:

### Memory / Facts panel (right column)

Shows all stored facts as a table with Key, Value, and last-updated timestamp. Refreshes when you load the page. Useful for auditing what Potts knows about you.

### Session History panel (left column)

Shows the 20 most recent session summaries in reverse chronological order, with dates. Gives you a quick log of past conversations.

---

## Managing Memory Manually

Since memory is a plain SQLite database, you can inspect or edit it directly:

```bash
# Open the database
sqlite3 ~/.potts/memory.db

# See all facts
SELECT * FROM facts;

# Delete a specific fact
DELETE FROM facts WHERE key = 'old_project';

# See recent sessions
SELECT date, summary FROM sessions ORDER BY id DESC LIMIT 10;

# Wipe all memory and start fresh
DELETE FROM facts;
DELETE FROM sessions;
```

Or simply delete the database entirely:

```bash
rm ~/.potts/memory.db
```

Potts will recreate it on next startup.

---

## Privacy Notes

- Memory is stored only on your machine in `~/.potts/memory.db`.
- Facts and session summaries are sent to the Groq API once (at shutdown) for extraction, and then stored locally.
- No memory data is sent to Deepgram or Cartesia.
- The memory context is sent to Groq with every LLM call during a session (as part of the system prompt).
