# Potts — Voice Interaction Examples

A practical reference of exact things you can say to Potts, organised by category. Say **"Potts"** first to wake it, then speak your request.

---

## Web Search

```
Potts, what's the current price of Bitcoin?

Potts, who won the Champions League last night?

Potts, what's the latest version of Python?

Potts, what does HTTP 429 mean?

Potts, find me a simple bash script to rename files in bulk.

Potts, what's the weather in Tokyo this weekend?

Potts, what are the differences between Rust and Go for systems programming?

Potts, has the Next.js 15 release been announced?
```

---

## Shell Commands

Potts runs shell commands on your machine and summarises the output.

```
Potts, how much free disk space do I have?

Potts, what processes are using the most CPU right now?

Potts, is port 8080 open on my machine?

Potts, what's my current IP address?

Potts, list all Docker containers that are running.

Potts, how long has my machine been running?

Potts, show me the last 20 lines of my nginx error log.

Potts, what packages got updated in my last system upgrade?

Potts, is the postgres service running?

Potts, what's the size of the node_modules folder in my project?

Potts, kill the process on port 3000.

Potts, run my test suite at ~/code/myapp and tell me if anything fails.
```

---

## Files

```
Potts, read my nginx config and tell me what ports it's listening on.

Potts, what's in my todo list at ~/notes/todo.txt?

Potts, summarise the README at ~/code/myproject/README.md.

Potts, read the last error in ~/logs/app.log.

Potts, create a file at ~/notes/meeting.txt with these notes:
  "Meeting with Alice on Tuesday at 10am. Topics: Q2 roadmap, budget review."

Potts, write a .gitignore file at ~/code/myproject that ignores
  node_modules, .env, and dist folders.
```

---

## Reminders

```
Potts, remind me in 15 minutes to stand up and stretch.

Potts, set a reminder in 2 hours to check the oven.

Potts, remind me at 9:30 to join the standup.

Potts, set a reminder at 17:00 called "end of day" saying "commit your work and push."

Potts, what reminders do I have?

Potts, do I have any pending reminders?
```

---

## Background Agents

Agents run autonomously in the background. Potts notifies you when they finish. Output streams live to the dashboard.

### Code Agent

Best for writing, debugging, or reviewing code.

```
Potts, delegate to the code agent: write a Python script that monitors a directory
  for new files and sends a desktop notification when one appears.

Potts, delegate to the code agent: find all TODO comments in ~/code/myproject
  and create a summary file at ~/code/myproject/TODO_SUMMARY.md.

Potts, delegate to the code agent: refactor the function get_user_data in
  ~/code/myapp/api/users.py to be async and add proper error handling.

Potts, delegate to the code agent: review ~/code/myproject/src/auth.py
  and identify any security issues.

Potts, delegate to the code agent: write unit tests for the module at
  ~/code/myapp/utils/parser.py.

Potts, delegate to the code agent: explain what the script at ~/scripts/deploy.sh does
  and suggest improvements.
```

### Research Agent

Best for synthesising information, comparisons, or in-depth topic analysis.

```
Potts, delegate to the research agent: compare PostgreSQL and MongoDB for a
  high-write, low-read workload and give me a recommendation.

Potts, delegate to the research agent: what are the best practices for
  JWT authentication in 2026?

Potts, delegate to the research agent: research the current state of WebAssembly
  for server-side use cases and summarise the key projects.

Potts, delegate to the research agent: find 5 alternatives to Celery for
  task queuing in Python and compare their trade-offs.
```

### System Agent

Best for OS administration, package management, configuration, and diagnostics.

```
Potts, delegate to the system agent: check why my machine is slow and
  identify the top resource consumers.

Potts, delegate to the system agent: set up a systemd service that runs
  ~/scripts/backup.sh every day at 3am.

Potts, delegate to the system agent: diagnose why nginx keeps returning 502
  and suggest a fix.

Potts, delegate to the system agent: list all installed packages that haven't
  been updated in over 6 months and flag any with known CVEs.

Potts, delegate to the system agent: configure my SSH server to disable
  password authentication and only allow key-based login.
```

---

## Process & File Monitoring

These start monitoring in the background and notify you when the condition is met.

```
Potts, watch my ffmpeg process and tell me when it finishes.

Potts, notify me when PID 12345 exits.

Potts, watch my build at ~/code/myapp and notify me when the build log
  contains "build succeeded".

Potts, tail /var/log/syslog and alert me if you see "Out of memory".

Potts, check "curl -s http://localhost:8080/health" every 10 seconds
  and tell me when it returns "ok".

Potts, poll "systemctl is-active myapp" every 30 seconds until it's active.

Potts, monitor "kubectl get pods" every 60 seconds until all pods show "Running".
```

---

## Vision (Screen & Images)

Requires a vision-capable API key (Anthropic or OpenAI). See [configuration.md](configuration.md).

```
Potts, what's that error on my screen?

Potts, read the text in the dialog box on my screen.

Potts, describe what's on my screen right now.

Potts, look at my screen and tell me what the test failure says.

Potts, is there anything unusual in this screenshot at ~/Desktop/error.png?

Potts, describe the architecture diagram at ~/docs/system-design.png.

Potts, what does the chart at ~/Downloads/metrics.png show?

Potts, read the table in ~/Desktop/spreadsheet-screenshot.png and
  tell me the totals.
```

---

## GitHub

Requires `GITHUB_TOKEN` in `.env`.

```
Potts, list my GitHub repos.

Potts, what are the open issues on my potts repo?

Potts, get issue 42 from torvalds/linux.

Potts, are there any open pull requests on my myapp repo?

Potts, summarise PR 7 on my potts repo.

Potts, do I have any unread GitHub notifications?

Potts, comment on issue 12 in my potts repo:
  "Fixed in commit abc1234. Please retest and close if resolved."

Potts, what CI checks are failing on PR 3 in my myapp repo?
```

---

## Email & Calendar

Requires Microsoft Graph setup. See [outlook.md](outlook.md).

```
Potts, do I have any new emails?

Potts, read my last 3 emails.

Potts, send an email to alice@example.com with subject "Quick question"
  and say "Hey Alice, are you free for a call on Thursday afternoon?"

Potts, what's on my calendar today?

Potts, what do I have scheduled for the rest of the week?

Potts, create a calendar event called "Sprint planning" for tomorrow at 10am,
  ending at 11am, in the main conference room.

Potts, add a meeting with Bob to my calendar on Friday at 2pm for one hour,
  subject "Architecture review."
```

---

## Memory & Context

Potts automatically saves facts about you and a session summary when you shut it down. On the next session it restores that context. You don't need to do anything — but you can prompt it to use what it knows.

```
Potts, what do you remember about me?

Potts, what did we talk about last time?

Potts, remember that my main project is currently called Helix and it's a FastAPI app.

Potts, my preferred editor is Neovim, keep that in mind.
```

---

## General Conversation

Potts responds in whatever language you speak (set `POTTS_STT_LANGUAGE` to match).

```
Potts, what time is it?

Potts, set a timer for 5 minutes.   (uses schedule_reminder under the hood)

Potts, explain what a race condition is in simple terms.

Potts, what's the difference between a mutex and a semaphore?

Potts, write me a haiku about distributed systems.

Potts, I need to focus for the next hour, remind me at the end.
```

---

## Spanish examples (set `POTTS_STT_LANGUAGE=es`)

```
Potts, ¿cuál es el precio actual del euro frente al dólar?

Potts, recuérdame en 20 minutos que tengo que llamar a Juan.

Potts, ¿qué procesos están consumiendo más CPU ahora mismo?

Potts, delega en el agente de código: escribe un script en Python que
  monitorice una carpeta y avise cuando aparezca un nuevo archivo.

Potts, ¿tienes algún recordatorio pendiente?

Potts, lee el archivo ~/notas/tareas.txt y resúmelo.
```

---

## Tips

**Be specific about file paths.** Instead of "my project", say "at ~/code/myapp". Potts can't guess directory locations unless you've told it before.

**Agents handle complexity.** If a task needs more than one or two steps, delegate to an agent. Potts's direct responses are intentionally brief for voice.

**You can interrupt.** If Potts is speaking and you say "Potts" again, it stops and listens immediately.

**The dashboard shows everything.** Open http://localhost:7766 to see agent output in full, memory facts, session history, and pending reminders.

**Wake timeout.** After 5 minutes of no interaction, Potts goes to sleep (low beep). Say "Potts" to wake it again. Change the timeout with `POTTS_WAKE_TIMEOUT` in `.env`.
