# Scheduler

**One scheduling system.** Ad-hoc reminders (`schedule_reminder` / `list_reminders`,
`tools/handlers/schedule.py`) mint `send_reminder` tasks straight into the same
`tasks` table (`core/db.py`: `add_task`/`load_pending_tasks`/`set_task_state`/
`sweep_deferred_tasks`) that backs the rest of the do-and-sleep brain; recurring
`/goal … --every …` commands, the `digest`/`maintenance` **seeds**
(`seeds.py`, `lhassa seed apply <name>`), and `sweep_deferred_tasks()`'s
backoff retries all mint into the exact same table. There is **no separate
polling loop**: the systemd **heartbeat** timer (`core/heartbeat.py` +
`lhassa-heartbeat.timer`) IS the due-sweep; every beat, `core/step.py` calls
`watchlist.check()` (`core/watchlist.py:_from_pending_tasks`), which finds
tasks whose `due_at` has passed, zero tokens, and runs the fixed `send_reminder`
action (`actionpack/_reminder.py`); deterministic, $0, a normal Episode.

You can set ad-hoc reminders from a live conversation (CLI/voice/Telegram) and
Lhassa will notify you at the right time via `notify.dispatch.notify()` (whatever
channels `LHASSA_NOTIFY_CHANNELS` configures; desktop, Telegram, log).

> **Legacy migration.** Before this pass, reminders lived in a separate
> `memory/store.py` `scheduled_tasks` table, fired by a standalone 30-second
> poller (`lhassa/scheduler.py`'s `run_scheduler()`, started by `main.py` and
> `docker/run_api.py`). That poller is now a deprecated no-op shim. On first
> startup after upgrade, `tools.handlers.schedule.migrate_legacy_reminders()`
> runs once (guarded by a `watch_state` marker, so it's a no-op on every
> subsequent start): it copies any still-`pending` legacy rows into the `tasks`
> table as `send_reminder` tasks (preserving name/message/run_at) and marks the
> legacy rows `status='migrated'`: they are never deleted, so `sqlite3
> ~/.lhassa/memory.db` and the memory export/import snapshot still see them.

---

## Setting Reminders (voice, CLI chat, Telegram)

### Relative time (in_minutes)

```
"Lhassa, remind me in 20 minutes to call John."
"Lhassa, set a reminder in 5 minutes; the pasta is boiling."
"Lhassa, remind me in 2 hours to take my medication."
"Lhassa, remind me in 1 hour to check the backup."
```

Lhassa confirms:
```
Lhassa: Reminder 'call John' set for in 20 minutes.
```

### Absolute time (at_time)

```
"Lhassa, remind me at 15:00 to join the standup."
"Lhassa, set a reminder at 8:30 tomorrow to take my vitamins."
"Lhassa, remind me at 9pm to watch the match."
```

Lhassa confirms:
```
Lhassa: Reminder 'standup' set for at 15:00.
```

### ISO 8601 format

You can also give a fully qualified ISO 8601 timestamp; useful when delegating from a script or the API:

```
at_time: "2026-04-01T14:00:00+02:00"
```

---

## Time Format Details

| Format | Example | Interpretation |
|---|---|---|
| `HH:MM` | `15:30` | Local time today (tomorrow if the time has already passed today) |
| ISO 8601 (no tz) | `2026-04-01T14:00:00` | Treated as local time, then converted to UTC |
| ISO 8601 (with tz) | `2026-04-01T14:00:00+02:00` | Exact UTC moment |

All times are stored internally as UTC ISO 8601 strings. Lhassa converts HH:MM to UTC using your system's local timezone.

---

## Notification Routing

When a reminder's `send_reminder` task fires, it fans out over
`notify.dispatch.notify()`: every channel `LHASSA_NOTIFY_CHANNELS` configures
and that is credential-gated/available gets it (`notify/channels.py`):

| Channel | Condition |
|---|---|
| `desktop` (`DesktopChannel`) | `notify-send` on `PATH` (Linux/libnotify toast) |
| `telegram` (`TelegramChannel`) | bot token + chat id configured |
| `log` (`LogChannel`) | always available; a log line, the last-resort fallback |

A channel failure is logged and swallowed; `notify()` never raises, and
delivery to at least one channel is enough for the task to be marked `hecha`.

### Example: desktop notification

```
Title: Lhassa. Reminder
Body: Join the standup.
```

---

## Listing Pending Reminders

```
You: Lhassa, what reminders do I have?
Lhassa: You have two pending reminders.
       Number one: call John at 14:20.
       Number two: check backup at 16:00.

You: Lhassa, any upcoming reminders?
Lhassa: No pending reminders.
```

The `list_reminders` tool reads pending `send_reminder` tasks (`estado ==
"pendiente"`) from the `tasks` table, soonest `due_at` first. Once the
heartbeat runs the task it moves to `hecha` and drops off the list.

---

## Viewing Scheduled Tasks in the Dashboard

The **Scheduled Tasks** panel (`ScheduledPanel.jsx`, under the dashboard's
**Activity** section) at `http://localhost:7766` shows pending `send_reminder`
tasks (last 50), including:

- Task name (the task's `objetivo`)
- Scheduled time (`due_at`)
- Message (`params.text`)
- Status (pending / done / cancelled)

The panel refreshes when you load the page. The WebSocket broadcasts a
`scheduled_task_fired` event when a task fires, which can be used by the
dashboard to refresh the panel.

---

## REST API

You can also query scheduled reminders directly:

```bash
# Pending send_reminder tasks (last 50), reshaped to the legacy response keys
curl http://localhost:7766/api/scheduled
```

Response format (unchanged for the dashboard):
```json
[
  {
    "id": "t_abc123",
    "name": "call John",
    "run_at": "2026-03-28T14:20:00+00:00",
    "message": "Time to call John",
    "status": "pending",
    "created_at": "2026-03-28T13:58:32+00:00"
  }
]
```

`/api/scheduled` reads the `tasks` table (`core/db.py`) filtered to `accion ==
"send_reminder"` and maps `estado` → `status` (`pendiente`/`esperando_aprobacion`/
`diferida` → `"pending"`, `hecha` → `"done"`, `rechazada` → `"cancelled"`).

---

## Limitations

- **Heartbeat granularity**: reminders fire on the next heartbeat beat after
  their `due_at`, not at the exact second (see `lhassa-heartbeat.timer`'s
  interval).
- **No recurring reminders from the voice/CLI/Telegram tool**: the
  `schedule_reminder` tool schema has no repeat parameter, so each reminder is
  one-shot. (The underlying `tasks` table and `mint_task` *do* support a
  `recurrence` interval; see `/goal … --every …` and the `digest`/
  `maintenance` seeds; a future revision could thread it through here.)
- **No cancellation by voice**: once set, a reminder can't be cancelled by
  speaking. Use `set_task_state(instance_id, task_id, "rechazada")` or the
  SQLite database directly, or wait for it to fire.
- **No sound if Lhassa is not running**: if you stop Lhassa (and its
  heartbeat) before a reminder fires, it won't trigger until the next beat
  after you restart it.

---

## Direct Database Access

Reminders live in `~/.lhassa/brain.db` (or wherever `LHASSA_BRAIN_DB_PATH`
points), in the `tasks` table:

```bash
sqlite3 ~/.lhassa/brain.db "SELECT id, accion, objetivo, params, estado, due_at FROM tasks WHERE accion='send_reminder' AND estado='pendiente';"
```

To cancel a pending reminder manually:

```bash
sqlite3 ~/.lhassa/brain.db "UPDATE tasks SET estado='rechazada' WHERE id='t_abc123';"
```

(Any non-`pendiente` estado prevents the watchlist from picking the task up.)

The legacy `~/.lhassa/memory.db` `scheduled_tasks` table still exists (for
audit / the memory export-import snapshot) but is no longer written to by the
reminder tool; see the migration note above.
