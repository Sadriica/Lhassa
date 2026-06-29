# Tools

Potts has access to a set of built-in tools it can call automatically when needed. You don't invoke them by name — you just describe what you want and Potts decides which tool to use. This document describes what each tool does, its limitations, and realistic example interactions.

---

## web_search

**What it does:** Searches the web and returns the top results as text snippets. Uses **Brave Search** when `BRAVE_API_KEY` is configured; falls back to **DuckDuckGo** otherwise. Used for anything Potts doesn't know from training data: current events, live prices, documentation, recent releases, local information, etc.

**Parameters:**
- `query` — the search string
- `max_results` — number of results (default 4)

**Limitations:**
- Results are text snippets only, not full web pages.
- Dynamic pages rendered by JavaScript may not be captured fully.
- DuckDuckGo (fallback) may not index very niche or very recent pages.

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
Potts: A basic miso soup needs dashi stock, white miso paste, tofu, and wakame seaweed.
       Dissolve about two tablespoons of miso in simmering dashi, then add sliced tofu
       and rehydrated wakame. Serve immediately.
```

---

## shell

**What it does:** Runs an arbitrary shell command on your machine as the user running Potts. Output (stdout and stderr) is returned to the LLM and summarised in Potts's response. Useful for system information, file listings, running scripts, process management, and more.

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
- **Truncated at 4000 characters** for very large files.
- Binary files will produce garbled output.
- Returns a "File not found" message if the path doesn't exist.

**Example interactions:**

```
You: Potts, read my nginx config and tell me which port it's listening on.
[reading: /etc/nginx/nginx.conf]
Potts: Your nginx config is set to listen on port 80 for HTTP and port 443 for HTTPS.

You: Potts, what's in my todo list?
[reading: ~/notes/todo.txt]
Potts: You have five items: buy groceries, call the dentist, finish the API refactor,
       review the pull request, and update the server.
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
You: Potts, create a file at ~/notes/meeting.txt with these notes:
     "Meeting with Alice on Tuesday. Discuss Q2 roadmap. Bring the metrics."
[writing: ~/notes/meeting.txt]
Potts: Done. The file has been created at ~/notes/meeting.txt.
```

---

## delegate_to_agent

**What it does:** Hands off a complex, time-consuming task to a specialised background agent. The agent runs asynchronously — Potts immediately tells you it has started, and you'll be notified when it finishes (via TTS if you're in a voice session, or a desktop notification if Potts has gone idle). Agents have their own tool loop and can call tools themselves while working.

**Parameters:**
- `agent` — one of `"code"`, `"research"`, or `"system"`
- `task` — a detailed description of what to do

**Agent types:**
| Agent | Best for |
|---|---|
| `code` | Writing code, debugging, code review, refactoring, explaining algorithms |
| `research` | Synthesising information, comparing options, summarising topics |
| `system` | OS administration, package management, services, network config |

**Example interactions:**

```
You: Potts, delegate to the code agent: write a Python script that watches a directory
     for new files and prints their names as they appear.
Potts: Code agent started in the background. I'll let you know when it's done.
[...some time later...]
Potts: Code agent finished. It wrote a script using watchdog that monitors a directory
       and prints new file names in real time.
```

The full output from the agent appears in the dashboard's Agents panel in real time. See [agents.md](agents.md) for a full description of each agent type.

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
```

---

## GitHub tools

These tools require `GITHUB_TOKEN` in your `.env` (see [configuration.md](configuration.md)).

### github_list_repos

**What it does:** Lists repositories for a GitHub user or organisation, ordered by most recently updated.

**Parameters:**
- `owner` — GitHub username or organisation name (optional; defaults to the token owner)
- `limit` — max results (default 10)

```
You: Potts, list my GitHub repos.
Potts: You have 8 repositories. The most recently updated ones are: potts,
       my-blog, dotfiles, and advent-of-code-2025.
```

### github_list_issues

**What it does:** Lists open issues for a repository.

**Parameters:**
- `owner` — repository owner
- `repo` — repository name
- `limit` — max results (default 10)

```
You: Potts, what are the open issues on my potts repo?
Potts: There are 3 open issues: "Add Ollama support" opened 2 days ago,
       "Wake word detection on Pipewire 1.x breaks" from last week,
       and "Session summary sometimes blank" from yesterday.
```

### github_get_issue

**What it does:** Fetches the full title, body, and first 5 comments of a specific issue.

**Parameters:**
- `owner`, `repo`, `issue_number`

```
You: Potts, get issue 12 from my potts repo.
Potts: Issue 12 is titled "Wake word detection on Pipewire 1.x breaks".
       The body says the STT service loses connection after 30 seconds on Pipewire 1.2.
       There's one comment asking about the audio device name.
```

### github_list_prs

**What it does:** Lists open pull requests for a repository.

**Parameters:**
- `owner`, `repo`, `limit` (default 10)

```
You: Potts, are there any open PRs on my repo?
Potts: There are 2 open pull requests: "Add Brave Search support" from 3 days ago,
       and "Fix WebSocket reconnect loop" from today.
```

### github_get_pr

**What it does:** Fetches the full description, diff stats, and review status of a specific pull request.

**Parameters:**
- `owner`, `repo`, `pr_number`

```
You: Potts, summarise PR 7 on my potts repo.
Potts: PR 7 is "Add Brave Search support". It adds a BRAVE_API_KEY option that falls
       back to DuckDuckGo. It touches 3 files, adds 60 lines, removes 10.
       No reviews yet. CI is passing.
```

### github_comment

**What it does:** Posts a comment on an issue or pull request.

**Parameters:**
- `owner`, `repo`, `issue_number`, `body`

```
You: Potts, comment on issue 12 in my potts repo:
     "Fixed in the latest commit — upgrading to pipecat 0.0.110 resolves it."
Potts: Comment posted on issue 12.
```

### github_notifications

**What it does:** Fetches your unread GitHub notifications (mentions, reviews, CI failures, etc.).

**Parameters:**
- `limit` — max results (default 10)

```
You: Potts, do I have any GitHub notifications?
Potts: You have 3 unread notifications. A review request on PR 7, a mention in
       issue 15 on the dotfiles repo, and a CI failure on the main branch of potts.
```

---

## Vision tools

Vision tools use the provider configured by `POTTS_VISION_PROVIDER` (default: `auto`). In auto mode, Potts uses the active `POTTS_LLM_PROVIDER` if it supports vision (Anthropic, OpenAI, OpenRouter), then falls back to `ANTHROPIC_API_KEY` → `OPENAI_API_KEY`. See [configuration.md](configuration.md) for setup details.

### analyze_screen

**What it does:** Takes a screenshot of your current screen and sends it to a vision LLM for analysis. Useful for asking Potts about error messages, UI elements, or anything visible on screen without having to describe it verbally.

Screenshot capture is platform-aware: `screencapture` on macOS (built-in), PowerShell or Pillow on Windows, and `scrot` / `maim` / ImageMagick on Linux. No configuration needed on macOS; on Linux install `scrot` (`pacman -S scrot`, `apt install scrot`).

**Parameters:**
- `question` — specific question to ask about the screen (optional; defaults to "Describe what you see in detail.")

```
You: Potts, what's that error on my screen?
[👁 analyzing screen...]
Potts: The terminal shows a Python traceback. The last line says
       "ModuleNotFoundError: No module named 'anthropic'".
       You need to install the anthropic package with pip or uv.

You: Potts, what's on my screen right now?
[👁 analyzing screen...]
Potts: Your screen shows a code editor on the left with a Python file open,
       and a terminal on the right running a test suite. All tests are passing.
```

### analyze_image

**What it does:** Analyses an image file from the filesystem using a vision LLM.

**Parameters:**
- `path` — path to the image file (required)
- `question` — question to ask about the image (optional)

```
You: Potts, describe the diagram at ~/docs/architecture.png.
[👁 analyzing image: ~/docs/architecture.png]
Potts: The diagram shows a three-tier architecture with a React frontend,
       a FastAPI backend, and a PostgreSQL database. Arrows show the data flow
       from the browser through a REST API to the database.

You: Potts, what does the screenshot at ~/Downloads/error.png say?
[👁 analyzing image: ~/Downloads/error.png]
Potts: The screenshot shows a 403 Forbidden error from nginx.
       The URL in the address bar is /api/admin/settings.
```

---

## Monitor tools

Monitor tools watch a process, file, or command in the background and notify you when a condition is met. They return immediately — the monitoring runs as a background asyncio task. Notification is spoken aloud via TTS if you're in a voice session, or sent as a desktop notification (`notify-send`) otherwise.

### watch_process

**What it does:** Watches a running process by PID or name and notifies you when it exits.

**Parameters:**
- `pid` — process ID to watch (optional)
- `name` — process name substring to find (used if `pid` is not given)
- `label` — friendly name for notifications (optional)

Polls every 5 seconds. If the process doesn't exist, returns an error immediately.

```
You: Potts, watch my ffmpeg process and tell me when it's done.
Potts: Watching 'ffmpeg' (PID 18432) — I'll notify you when it finishes.
[...some time later, via TTS or desktop notification...]
Potts: Process ffmpeg (PID 18432) exited with code 0.

You: Potts, notify me when PID 9821 finishes.
Potts: Watching process (PID 9821) — I'll notify you when it finishes.
```

### watch_file

**What it does:** Tails a file and notifies you when a line matching a pattern appears, or when the file changes (if no pattern is given). Works like `tail -f` with an optional grep.

**Parameters:**
- `path` — path to the file to watch (required)
- `pattern` — notify when a line containing this text appears (optional; if omitted, notifies on any change)
- `label` — friendly name for notifications (optional)

```
You: Potts, watch /var/log/nginx/error.log and tell me if you see "upstream timeout".
Potts: Watching error.log for 'upstream timeout' — I'll notify you when it appears.
[...later...]
Potts: File error.log: 2026/03/28 14:22:31 [error] upstream timed out reading response

You: Potts, watch my build log at ~/builds/current.log and tell me when anything happens.
Potts: Watching current.log — I'll notify you when it changes.
```

### watch_command

**What it does:** Runs a shell command on a repeating interval until it succeeds (exit code 0) or its output contains a specified string, then notifies you. Useful for polling a service, waiting for a port to open, or waiting for a deployment to complete.

**Parameters:**
- `command` — shell command to run (required)
- `interval` — seconds between checks (default 30)
- `until_output_contains` — stop and notify when output contains this string (optional; if omitted, stops when exit code is 0)
- `label` — friendly name for notifications (optional)

Maximum 120 iterations (~1 hour at 30-second intervals).

```
You: Potts, poll "curl -s http://localhost:8080/health" every 10 seconds
     and tell me when it returns "ok".
Potts: Monitoring 'curl -s http://localhost:8080/health' every 10s
       until output contains 'ok'.
[...later...]
Potts: Command 'curl -s http://localhost:8080/health' completed. ok

You: Potts, check "systemctl is-active myapp" every 30 seconds until it's active.
Potts: Monitoring 'systemctl is-active myapp' every 30s until it succeeds.
[...later...]
Potts: Command 'systemctl is-active myapp' completed.
```

---

## Email and calendar tools

These tools require Microsoft Graph credentials. See [outlook.md](outlook.md) for setup.

### read_emails

**What it does:** Reads emails from your Outlook inbox via Microsoft Graph.

**Parameters:**
- `count` — number of emails to fetch (default 5)
- `unread_only` — if true, only return unread emails (default true)

```
You: Potts, do I have any new emails?
Potts: You have three unread emails. One from Alice about the project timeline,
       one from GitHub with a pull request notification, and one newsletter.
```

### send_email

**What it does:** Sends an email from your Outlook account. Plain text only; saved to Sent Items.

**Parameters:**
- `to` — recipient email address
- `subject` — email subject line
- `body` — email body as plain text

```
You: Potts, send an email to alice@example.com with subject "Meeting tomorrow"
     and say "Hi Alice, just confirming our 10am call tomorrow. See you then."
Potts: Email sent to alice@example.com.
```

### read_calendar

**What it does:** Reads upcoming events from your Outlook calendar for the next N days.

**Parameters:**
- `days_ahead` — how many days to look ahead (default 3; up to ~15 events returned)

```
You: Potts, what's on my calendar today?
Potts: You have two events today. A team standup at 9:30, and a one-on-one
       with your manager at 14:00 in room 3B.
```

### create_event

**What it does:** Creates a new event in your Outlook calendar.

**Parameters:**
- `subject` — event title (required)
- `start` — start time in ISO 8601 format (required)
- `end` — end time in ISO 8601 format (required)
- `body` — event description (optional)
- `location` — event location (optional)

```
You: Potts, create a calendar event called "Team sync" for tomorrow at 10am,
     ending at 10:30, in the main conference room.
Potts: Event "Team sync" created for tomorrow at 10:00 in the main conference room.
```
