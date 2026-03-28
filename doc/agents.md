# Background Agents

Sub-agents let Potts hand off complex, time-consuming work to a dedicated background worker while keeping the voice conversation free for other things. You ask Potts to delegate, it starts the agent, and tells you it'll notify you when the work is done.

---

## What Sub-Agents Are

Each agent is an instance of `BaseAgent` — a wrapper around an **AsyncGroq streaming** call. When you trigger a delegation, Potts:

1. Instantiates the appropriate agent class
2. Registers a run record in `AppState`
3. Launches the agent's `run()` coroutine as an independent asyncio task
4. Immediately responds to you: `"Agent 'code' started in the background. I'll notify you when it's done."`
5. Streams every token of the agent's response to the dashboard via WebSocket
6. Notifies you when the agent finishes

Agents run in parallel with the voice pipeline — you can have multiple agents running at the same time and continue talking to Potts normally while they work.

Each agent run gets a short random ID (e.g. `a3f19c`) and goes through three states: `running` → `done` (or `error`).

---

## The Three Agents

### CodeAgent

**Name:** `code`

**Best for:**
- Writing new code or scripts
- Debugging an error message
- Reviewing or explaining existing code
- Refactoring suggestions
- Generating boilerplate

**System prompt excerpt:**
> You are an expert software engineer and coding assistant running on an Arch Linux machine. You write clean, idiomatic code. You debug efficiently by reading error messages carefully. When asked to write code, provide complete, working implementations.

**Example delegation:**
```
You: Potts, delegate to the code agent: write a Bash script that monitors
     /var/log/syslog and sends me a desktop notification if the word "error"
     appears in a new line.
Potts: Code agent started in the background. I'll let you know when it's done.
[...~15 seconds later...]
Potts: Code agent finished. It wrote a script using tail -f and grep
       that fires notify-send whenever a line containing "error" appears.
```

### ResearchAgent

**Name:** `research`

**Best for:**
- Comparing technologies, tools, or approaches
- Synthesising information from multiple angles
- Summarising a complex topic
- Gathering background knowledge before making a decision

**System prompt excerpt:**
> You are a thorough research assistant. You synthesize information clearly and accurately. Identify what is known vs uncertain, present findings in a structured, readable way, cite sources when possible, and flag caveats.

**Example delegation:**
```
You: Potts, ask the research agent to compare Tailwind CSS v4 and v3.
     I want to know the main differences before I upgrade.
Potts: Research agent started. I'll notify you when it has results.
[...~20 seconds later...]
Potts: Research agent finished. The main changes in Tailwind v4 include
       a new CSS-first config approach, a Vite plugin, and performance
       improvements. It breaks the JS config file approach from v3.
```

### SystemAgent

**Name:** `system`

**Best for:**
- Arch Linux package management and pacman tips
- systemd service setup
- Network configuration
- System maintenance tasks
- Writing shell scripts for system operations

**System prompt excerpt:**
> You are a Linux/Arch system administration expert running on an Arch Linux machine with PipeWire audio, systemd, and pacman. Provide exact shell commands that work on Arch Linux. Prefer pacman/yay for package management. Use systemctl for service management.

**Example delegation:**
```
You: Potts, have the system agent explain how to set up a user systemd service
     that starts my backup script at login.
Potts: System agent started in the background.
[...~15 seconds later...]
Potts: System agent finished. It says to create a file at
       ~/.config/systemd/user/backup.service, then enable it with
       systemctl --user enable --now backup.service.
       It also gave you a complete example service file.
```

---

## How to Trigger an Agent via Voice

The most natural phrasing is:

```
"Potts, delegate [task description] to the [type] agent."
"Potts, have the code agent [task]."
"Potts, ask the research agent to [task]."
"Potts, use the system agent for [task]."
```

The LLM picks the right agent from context. If you don't specify, it defaults to `ResearchAgent`. You can be as detailed as you like in the task description — longer is usually better for complex tasks.

---

## Notification When Done

When an agent finishes, Potts routes the notification through the best available channel:

| Potts state | Notification method |
|---|---|
| Voice pipeline active (you're in a session) | Potts **speaks** the result summary aloud via TTS |
| Idle / sleeping | **Desktop notification** via `notify-send` |
| `notify-send` not installed | Falls back to printing to the terminal |

The spoken summary is truncated to the first 200 characters of the agent's output to keep it brief. The full output is always available in the dashboard.

---

## Real-Time Streaming in the Dashboard

While an agent is running, its output streams into the dashboard's **Agents panel** token by token via WebSocket. You can watch the agent think in real time:

- **Running** — amber indicator, output accumulates as text
- **Done** — green indicator, full output visible
- **Error** — red indicator, error message shown

Click on any agent card to expand it and read the full output. Agents are sorted most-recent-first.

---

## Dashboard Agent Cards

Each card shows:
- Agent name (`code`, `research`, or `system`)
- Status badge (running / done / error)
- The task that was requested
- The full streaming output
- Timestamps (started at, finished at)

Cards from the current session persist until Potts is restarted. The WebSocket delivers a snapshot of existing runs to any new browser tab that connects, so you can open the dashboard mid-session and see all previous agent results.

---

## Limitations

- **Single pass only.** Agents make one LLM call with up to 4096 output tokens. They cannot use tools, browse the web, or iterate on their results.
- **No cancellation.** Once an agent task is started, it runs to completion. There's no voice command to cancel a running agent.
- **Sequential within type.** If you start two code agents back-to-back, they both run in parallel (they're separate asyncio tasks), which may increase Groq API rate limit pressure.
- **Context is isolated.** Agents don't share the voice conversation history. You need to provide full context in the task description if it's needed.
