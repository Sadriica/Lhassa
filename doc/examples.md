# Lhassa — Voice Interaction Examples

A practical reference of exact things you can say to Lhassa, organised by category. Say **"Lhassa"** first to wake it, then speak your request.

---

## Web Search

```
Lhassa, what's the current price of Bitcoin?

Lhassa, who won the Champions League last night?

Lhassa, what's the latest version of Python?

Lhassa, what does HTTP 429 mean?

Lhassa, find me a simple bash script to rename files in bulk.

Lhassa, what's the weather in Tokyo this weekend?

Lhassa, what are the differences between Rust and Go for systems programming?

Lhassa, has the Next.js 15 release been announced?
```

---

## Shell Commands

Lhassa runs shell commands on your machine and summarises the output.

```
Lhassa, how much free disk space do I have?

Lhassa, what processes are using the most CPU right now?

Lhassa, is port 8080 open on my machine?

Lhassa, what's my current IP address?

Lhassa, list all Docker containers that are running.

Lhassa, how long has my machine been running?

Lhassa, show me the last 20 lines of my nginx error log.

Lhassa, what packages got updated in my last system upgrade?

Lhassa, is the postgres service running?

Lhassa, what's the size of the node_modules folder in my project?

Lhassa, kill the process on port 3000.

Lhassa, run my test suite at ~/code/myapp and tell me if anything fails.
```

---

## Files

```
Lhassa, read my nginx config and tell me what ports it's listening on.

Lhassa, what's in my todo list at ~/notes/todo.txt?

Lhassa, summarise the README at ~/code/myproject/README.md.

Lhassa, read the last error in ~/logs/app.log.

Lhassa, create a file at ~/notes/meeting.txt with these notes:
  "Meeting with Alice on Tuesday at 10am. Topics: Q2 roadmap, budget review."

Lhassa, write a .gitignore file at ~/code/myproject that ignores
  node_modules, .env, and dist folders.
```

---

## Reminders

```
Lhassa, remind me in 15 minutes to stand up and stretch.

Lhassa, set a reminder in 2 hours to check the oven.

Lhassa, remind me at 9:30 to join the standup.

Lhassa, set a reminder at 17:00 called "end of day" saying "commit your work and push."

Lhassa, what reminders do I have?

Lhassa, do I have any pending reminders?
```

---

## Background Agents

Agents run autonomously in the background. Lhassa notifies you when they finish. Output streams live to the dashboard.

### Code Agent

Best for writing, debugging, or reviewing code.

```
Lhassa, delegate to the code agent: write a Python script that monitors a directory
  for new files and sends a desktop notification when one appears.

Lhassa, delegate to the code agent: find all TODO comments in ~/code/myproject
  and create a summary file at ~/code/myproject/TODO_SUMMARY.md.

Lhassa, delegate to the code agent: refactor the function get_user_data in
  ~/code/myapp/api/users.py to be async and add proper error handling.

Lhassa, delegate to the code agent: review ~/code/myproject/src/auth.py
  and identify any security issues.

Lhassa, delegate to the code agent: write unit tests for the module at
  ~/code/myapp/utils/parser.py.

Lhassa, delegate to the code agent: explain what the script at ~/scripts/deploy.sh does
  and suggest improvements.
```

### Research Agent

Best for synthesising information, comparisons, or in-depth topic analysis.

```
Lhassa, delegate to the research agent: compare PostgreSQL and MongoDB for a
  high-write, low-read workload and give me a recommendation.

Lhassa, delegate to the research agent: what are the best practices for
  JWT authentication in 2026?

Lhassa, delegate to the research agent: research the current state of WebAssembly
  for server-side use cases and summarise the key projects.

Lhassa, delegate to the research agent: find 5 alternatives to Celery for
  task queuing in Python and compare their trade-offs.
```

### System Agent

Best for OS administration, package management, configuration, and diagnostics.

```
Lhassa, delegate to the system agent: check why my machine is slow and
  identify the top resource consumers.

Lhassa, delegate to the system agent: set up a systemd service that runs
  ~/scripts/backup.sh every day at 3am.

Lhassa, delegate to the system agent: diagnose why nginx keeps returning 502
  and suggest a fix.

Lhassa, delegate to the system agent: list all installed packages that haven't
  been updated in over 6 months and flag any with known CVEs.

Lhassa, delegate to the system agent: configure my SSH server to disable
  password authentication and only allow key-based login.
```

---

## Process & File Monitoring

These start monitoring in the background and notify you when the condition is met.

```
Lhassa, watch my ffmpeg process and tell me when it finishes.

Lhassa, notify me when PID 12345 exits.

Lhassa, watch my build at ~/code/myapp and notify me when the build log
  contains "build succeeded".

Lhassa, tail /var/log/syslog and alert me if you see "Out of memory".

Lhassa, check "curl -s http://localhost:8080/health" every 10 seconds
  and tell me when it returns "ok".

Lhassa, poll "systemctl is-active myapp" every 30 seconds until it's active.

Lhassa, monitor "kubectl get pods" every 60 seconds until all pods show "Running".
```

---

## Vision (Screen & Images)

Requires a vision-capable API key (Anthropic or OpenAI). See [configuration.md](configuration.md).

```
Lhassa, what's that error on my screen?

Lhassa, read the text in the dialog box on my screen.

Lhassa, describe what's on my screen right now.

Lhassa, look at my screen and tell me what the test failure says.

Lhassa, is there anything unusual in this screenshot at ~/Desktop/error.png?

Lhassa, describe the architecture diagram at ~/docs/system-design.png.

Lhassa, what does the chart at ~/Downloads/metrics.png show?

Lhassa, read the table in ~/Desktop/spreadsheet-screenshot.png and
  tell me the totals.
```

---

## GitHub

Requires `GITHUB_TOKEN` in `.env`.

```
Lhassa, list my GitHub repos.

Lhassa, what are the open issues on my potts repo?

Lhassa, get issue 42 from torvalds/linux.

Lhassa, are there any open pull requests on my myapp repo?

Lhassa, summarise PR 7 on my potts repo.

Lhassa, do I have any unread GitHub notifications?

Lhassa, comment on issue 12 in my potts repo:
  "Fixed in commit abc1234. Please retest and close if resolved."

Lhassa, what CI checks are failing on PR 3 in my myapp repo?
```

---

## Email & Calendar

Requires Microsoft Graph setup. See [outlook.md](outlook.md).

```
Lhassa, do I have any new emails?

Lhassa, read my last 3 emails.

Lhassa, send an email to alice@example.com with subject "Quick question"
  and say "Hey Alice, are you free for a call on Thursday afternoon?"

Lhassa, what's on my calendar today?

Lhassa, what do I have scheduled for the rest of the week?

Lhassa, create a calendar event called "Sprint planning" for tomorrow at 10am,
  ending at 11am, in the main conference room.

Lhassa, add a meeting with Bob to my calendar on Friday at 2pm for one hour,
  subject "Architecture review."
```

---

## Memory & Context

Lhassa automatically saves facts about you and a session summary when you shut it down. On the next session it restores that context. You don't need to do anything — but you can prompt it to use what it knows.

```
Lhassa, what do you remember about me?

Lhassa, what did we talk about last time?

Lhassa, remember that my main project is currently called Helix and it's a FastAPI app.

Lhassa, my preferred editor is Neovim, keep that in mind.
```

---

## General Conversation

Lhassa responds in whatever language you speak (set `LHASSA_STT_LANGUAGE` to match).

```
Lhassa, what time is it?

Lhassa, set a timer for 5 minutes.   (uses schedule_reminder under the hood)

Lhassa, explain what a race condition is in simple terms.

Lhassa, what's the difference between a mutex and a semaphore?

Lhassa, write me a haiku about distributed systems.

Lhassa, I need to focus for the next hour, remind me at the end.
```

---

## Spanish examples (set `LHASSA_STT_LANGUAGE=es`)

```
Lhassa, ¿cuál es el precio actual del euro frente al dólar?

Lhassa, recuérdame en 20 minutos que tengo que llamar a Juan.

Lhassa, ¿qué procesos están consumiendo más CPU ahora mismo?

Lhassa, delega en el agente de código: escribe un script en Python que
  monitorice una carpeta y avise cuando aparezca un nuevo archivo.

Lhassa, ¿tienes algún recordatorio pendiente?

Lhassa, lee el archivo ~/notas/tareas.txt y resúmelo.
```

---

## Tips

**Be specific about file paths.** Instead of "my project", say "at ~/code/myapp". Lhassa can't guess directory locations unless you've told it before.

**Agents handle complexity.** If a task needs more than one or two steps, delegate to an agent. Lhassa's direct responses are intentionally brief for voice.

**You can interrupt.** If Lhassa is speaking and you say "Lhassa" again, it stops and listens immediately.

**The dashboard shows everything.** Open http://localhost:7766 to see agent output in full, memory facts, session history, and pending reminders.

**Wake timeout.** After 5 minutes of no interaction, Lhassa goes to sleep (low beep). Say "Lhassa" to wake it again. Change the timeout with `LHASSA_WAKE_TIMEOUT` in `.env`.
