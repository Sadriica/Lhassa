# Background Agents

Sub-agents let Potts hand off complex, time-consuming work to a dedicated background worker while keeping the voice conversation free for other things. You ask Potts to delegate, it starts the agent, and tells you it'll notify you when the work is done.

---

## What Sub-Agents Are

Each agent is an instance of `BaseAgent` — a wrapper around a **multi-turn tool loop** backed by whichever LLM provider is configured (`POTTS_LLM_PROVIDER`). All providers are supported: Groq, Anthropic, OpenAI, OpenRouter, Cerebras, and Ollama.

By default all agents run through `claude --print` (`POTTS_AGENT_BACKEND=claude_code`), using your Claude Code subscription with the full Claude Code toolset. Set `POTTS_AGENT_BACKEND=builtin` to use the internal LLM tool loop with `POTTS_LLM_PROVIDER` instead. See [configuration.md](configuration.md) for details.

When you trigger a delegation, Potts:

1. Instantiates the appropriate agent class
2. Registers a run record in `AppState`
3. Launches the agent's `run()` coroutine as an independent asyncio task
4. Immediately responds to you: `"Agent 'code' started in the background. I'll notify you when it's done."`
5. Streams every token of the agent's response to the dashboard via WebSocket
6. Notifies you when the agent finishes

Agents run in parallel with the voice pipeline — you can have multiple agents running at the same time and continue talking to Potts normally while they work.

Each agent run gets a short random ID (e.g. `a3f19c`) and goes through three states: `running` → `done` (or `error`).

### Tool loop

Agents are not single-shot — they have a **tool loop** that lets them call tools and iterate on their results, up to a maximum of 10 iterations per run. This means an agent can:

- Search the web to gather current information
- Run shell commands to inspect your system
- Read and write files

Each individual LLM call within the loop has a **60-second timeout**. If an agent reaches the maximum number of iterations without finishing, it returns whatever partial output it has produced up to that point and logs a warning.

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

**System prompt focus:** expert software engineer on Arch Linux; writes clean, idiomatic code; debugs by reading error messages carefully; provides complete, working implementations.

When `POTTS_AGENT_BACKEND=claude_code`, this agent runs through `claude --print` and gets the full Claude Code toolset instead of the four basic tools.

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

**System prompt focus:** thorough research assistant; synthesises information clearly and accurately; identifies what is known vs uncertain; presents findings in a structured way; cites sources and flags caveats.

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

**System prompt focus:** Linux/Arch system administration expert; provides exact shell commands that work on Arch Linux; prefers pacman/yay for packages; uses systemctl for services.

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

- **Maximum 10 tool iterations.** The agent loop runs up to 10 LLM calls per run. If the task isn't finished by then, the agent returns whatever partial output it has and logs a warning. For most tasks this limit is never reached.
- **60-second per-call timeout.** Each individual LLM call within the loop must complete within 60 seconds, or the run is aborted with an error.
- **No cancellation.** Once an agent task is started, it runs to completion. There's no voice command to cancel a running agent.
- **Parallel agents share API quota.** Multiple agents running simultaneously all call the same LLM provider, which may increase rate limit pressure on providers with tight quotas (e.g. free Groq tiers).
- **Context is isolated.** Agents don't share the voice conversation history. Provide full context in the task description if the agent needs it.
- **Limited tool set.** Agents have access to `web_search`, `shell`, `read_file`, and `write_file` only. GitHub, vision, and monitor tools are voice-pipeline-only and cannot be called from within an agent run.
