# Tools

Potts has access to a set of built-in tools it can call automatically when needed. You don't invoke them by name — you just describe what you want and Potts decides which tool to use. This document describes what each tool does, its limitations, and realistic example interactions.

---

## web_search

**What it does:** Searches the web using DuckDuckGo and returns the top results as text snippets. Used for anything Potts doesn't know from training data: current events, live prices, documentation, recent releases, local information, etc.

**Parameters:**
- `query` — the search string
- `max_results` — number of results (default 4)

**Limitations:**
- Results are text snippets only, not full web pages.
- DuckDuckGo may not index very niche or very recent pages.
- No JavaScript rendering — dynamic pages may not be captured fully.

**Example interactions:**

```
You: Potts, what's the current price of Bitcoin?
[searching: Bitcoin current price USD]
Potts: Bitcoin is currently trading at around $67,000, up about 2% in the last 24 hours.

You: Potts, has the new Arch Linux kernel been released?
[searching: Arch Linux kernel update 2026]
Potts: Yes, the 6.13 kernel landed in Arch's testing repo yesterday. You can update with pacman -Syu.

You: Potts, what's a good recipe for miso soup?
[searching: simple miso soup recipe]
Potts: A basic miso soup needs dashi stock, white miso paste, tofu, and wakame seaweed. Dissolve about two tablespoons of miso in simmering dashi, then add sliced tofu and rehydrated wakame. Serve immediately.
```

---

## shell

**What it does:** Runs an arbitrary shell command on your Linux machine as the user running Potts. Output (stdout and stderr) is returned to the LLM and summarised in Potts's response. Useful for system information, file listings, running scripts, process management, and more.

**Parameters:**
- `command` — the shell command to run

**Limitations:**
- **30-second timeout** — commands that run longer than 30 seconds are killed. For long-running tasks, consider delegating to the SystemAgent.
- Output is **truncated at 2000 characters** if it's too long.
- Runs as your user — no `sudo` unless your user has passwordless sudo configured.
- The working directory is wherever Potts was launched from.

**Example interactions:**

```
You: Potts, how much disk space do I have left?
[running: df -h /]
Potts: Your root partition has 47 gigabytes free out of 200 gigabytes total.

You: Potts, is the nginx service running?
[running: systemctl is-active nginx]
Potts: Nginx is not running. Would you like me to start it?

You: Potts, list the Python files in my project.
[running: find ~/code/myproject -name "*.py" | head -20]
Potts: I found 12 Python files. The main ones are main.py, config.py, and three files in the utils folder.

You: Potts, what's my public IP address?
[running: curl -s ifconfig.me]
Potts: Your public IP address is 203.0.113.42.
```

---

## read_file

**What it does:** Reads the contents of any file on the filesystem and gives the content to the LLM. Useful for reviewing code, config files, logs, notes, or any text document.

**Parameters:**
- `path` — absolute or relative path (tilde expansion supported, e.g. `~/notes/todo.txt`)

**Limitations:**
- **Truncated at 4000 characters** for very large files. Only the first 4000 characters are returned.
- Binary files will produce garbled output.
- Returns a "File not found" message if the path doesn't exist.

**Example interactions:**

```
You: Potts, read my nginx config and tell me which port it's listening on.
[reading: /etc/nginx/nginx.conf]
Potts: Your nginx config is set to listen on port 80 for HTTP and port 443 for HTTPS.

You: Potts, what's in my todo list?
[reading: ~/notes/todo.txt]
Potts: You have five items: buy groceries, call the dentist, finish the API refactor, review the pull request, and update the server.

You: Potts, is there anything suspicious in the last 50 lines of my auth log?
[running: tail -50 /var/log/auth.log]
Potts: I see three failed SSH login attempts from IP 192.168.1.105 in the last hour. Everything else looks normal.
```

---

## write_file

**What it does:** Writes (or overwrites) a file at the specified path. Parent directories are created automatically if they don't exist.

**Parameters:**
- `path` — path to write to
- `content` — text content to write

**Limitations:**
- Overwrites existing files without warning — be precise about the path.
- No append mode; the entire file is replaced.
- Binary content is not supported.

**Example interactions:**

```
You: Potts, create a file at ~/notes/meeting.txt with the notes from our discussion.
Potts: What should I write in it?
You: Just put: "Meeting with Alice on Tuesday. Discuss Q2 roadmap. Bring the metrics."
[writing: ~/notes/meeting.txt]
Potts: Done. The file has been created at ~/notes/meeting.txt.

You: Potts, write a simple Python hello world script to ~/scripts/hello.py.
[writing: ~/scripts/hello.py]
Potts: Written. The file contains a standard hello world script with a main guard.
```

---

## delegate_to_agent

**What it does:** Hands off a complex, time-consuming task to a specialised background agent. The agent runs asynchronously — Potts immediately tells you it has started, and you'll be notified when it finishes (via TTS if you're in a voice session, or a desktop notification if Potts has gone idle).

**Parameters:**
- `agent` — one of `"code"`, `"research"`, or `"system"`
- `task` — a detailed description of what to do

**Agent types:**
| Agent | Best for |
|---|---|
| `code` | Writing code, debugging, code review, refactoring, explaining algorithms |
| `research` | Synthesising information, comparing options, summarising topics |
| `system` | Arch Linux admin, package management, systemd, network config |

**Limitations:**
- Agents run on the same Groq model as the main pipeline — complex tasks may use significant quota.
- Agents have a single pass (up to 4096 tokens output) — they can't iterate or use tools themselves.
- There's no way to cancel an agent once it's started from the voice interface.

**Example interactions:**

```
You: Potts, delegate to the code agent: write a Python script that watches a directory
     for new files and prints their names as they appear.
Potts: Code agent started in the background. I'll let you know when it's done.
[...some time later...]
Potts: Code agent finished. It wrote a script using watchdog that monitors a directory
       and prints new file names in real time.

You: Potts, have the research agent look into the trade-offs between PostgreSQL and SQLite
     for a single-user local application.
Potts: Research agent started. I'll notify you when it has results.

You: Potts, ask the system agent how to set up a systemd user service that auto-starts
     my dashboard on login.
Potts: System agent started in the background.
```

The full output from the agent appears in the dashboard's Agents panel (see [dashboard.md](dashboard.md)). Each agent streams tokens in real time so you can watch progress.

---

## schedule_reminder

**What it does:** Schedules a reminder to fire at a specific time or after a delay. When it fires, Potts speaks the message aloud (if in a voice session) or sends a desktop notification (if idle).

**Parameters:**
- `name` — short label for the reminder
- `message` — what Potts will say when it fires
- `in_minutes` — fire after this many minutes from now (optional)
- `at_time` — fire at a specific time: HH:MM (24-hour local time) or ISO 8601 (optional)

You must provide either `in_minutes` or `at_time`.

**Example interactions:**

```
You: Potts, remind me in 20 minutes to take my medication.
Potts: Reminder set for 20 minutes from now.
[20 minutes later]
Potts: Reminder — take your medication.

You: Potts, set a reminder at 15:30 to call John.
Potts: Reminder "call John" set for 15:30.

You: Potts, remind me in 2 hours to check the backup status.
Potts: Done. I'll remind you in 2 hours.
```

---

## list_reminders

**What it does:** Lists all pending (not yet fired) reminders.

**Parameters:** None

**Example interactions:**

```
You: Potts, what reminders do I have?
Potts: You have two pending reminders. Number one: call John at 15:30.
       Number two: check backup status in about 90 minutes.

You: Potts, do I have any upcoming reminders?
Potts: No pending reminders.
```

---

## read_emails

**What it does:** Reads emails from your Outlook inbox via Microsoft Graph. Requires the Microsoft integration to be configured (see [outlook.md](outlook.md)).

**Parameters:**
- `count` — number of emails to fetch (default 5)
- `unread_only` — if true, only return unread emails (default true)

**Limitations:**
- Only reads from the inbox folder, not other folders.
- Email body is truncated to a short preview — not the full text.
- Requires `MS_TENANT_ID` and `MS_CLIENT_ID` in your `.env`.

**Example interactions:**

```
You: Potts, do I have any new emails?
[reading emails...]
Potts: You have three unread emails. One from Alice about the project timeline,
       sent this morning. One from GitHub with a pull request notification.
       And one newsletter you probably want to delete.

You: Potts, show me the last 10 emails, including read ones.
Potts: Reading your last 10 emails...
```

---

## send_email

**What it does:** Sends an email from your Outlook account. The sent message is saved to your Sent Items folder.

**Parameters:**
- `to` — recipient email address
- `subject` — email subject line
- `body` — email body as plain text

**Limitations:**
- Plain text only — no HTML, no attachments.
- Single recipient only per call.
- Requires Microsoft integration.

**Example interactions:**

```
You: Potts, send an email to alice@example.com with subject "Meeting tomorrow"
     and say "Hi Alice, just confirming our 10am call tomorrow. See you then."
[sending email to alice@example.com...]
Potts: Email sent to alice@example.com.

You: Potts, email bob@company.com, subject "Quick question", body "Hey Bob,
     do you have 15 minutes this week to sync on the project?"
Potts: Done. Email sent to Bob.
```

---

## read_calendar

**What it does:** Reads upcoming events from your Outlook calendar for the next N days.

**Parameters:**
- `days_ahead` — how many days to look ahead (default 3, max ~15 events returned)

**Limitations:**
- Shows up to 15 events per call.
- Event body/description is not returned, only subject, time, and location.
- Requires Microsoft integration.

**Example interactions:**

```
You: Potts, what's on my calendar today?
[reading calendar (3 days)...]
Potts: You have two events today. A team standup at 9:30, and a one-on-one
       with your manager at 14:00 in room 3B.

You: Potts, what does my week look like?
Potts: Reading the next 7 days...
       You have 6 events: standup every day at 9:30, a client call Wednesday
       at 15:00, and a dentist appointment Friday at 11:00.
```

---

## create_event

**What it does:** Creates a new event in your Outlook calendar.

**Parameters:**
- `subject` — event title (required)
- `start` — start time in ISO 8601 UTC format, e.g. `2026-04-01T14:00:00` (required)
- `end` — end time in ISO 8601 UTC format (required)
- `body` — event description (optional)
- `location` — event location (optional)

**Limitations:**
- Times are stored in UTC — specify with timezone offset or Potts will interpret as UTC.
- No attendee invitations — this creates only your own calendar entry.
- Requires Microsoft integration.

**Example interactions:**

```
You: Potts, create a calendar event called "Team sync" for tomorrow at 10am,
     ending at 10:30, in the main conference room.
Potts: Event "Team sync" created for tomorrow at 10:00 in the main conference room.

You: Potts, add a dentist appointment to my calendar on Friday at 11am for 1 hour.
Potts: Done. Dentist appointment added for Friday at 11:00, ending at 12:00.
```
