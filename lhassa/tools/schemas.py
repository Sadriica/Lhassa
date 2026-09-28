# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Lhassa tool schemas; the JSON tool definitions the LLM sees.

This is the single ``TOOLS`` catalog (a :class:`ToolsSchema` of
:class:`FunctionSchema` entries). Handlers live in ``lhassa.tools.handlers.*``
and the wiring lives in ``lhassa.tools.registry``. Keeping the schema list here
lets the catalog grow without touching handler code.
"""

try:  # the voice extra is optional; the catalog itself is not
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
except ModuleNotFoundError:  # pragma: no cover - exercised by the no-voice install
    # The default install (API/heartbeat/telegram/CLI) ships without pipecat, and
    # this catalog is what the CLI door sends the model; importing it must not
    # drag the voice stack in. These two are plain data holders there, so a local
    # equivalent keeps every non-voice door working on the default install.
    # ``tests/test_tool_schema_shim.py`` pins the rendered dict to pipecat's own
    # whenever pipecat IS installed, so the two can never drift apart.
    from dataclasses import dataclass, field

    @dataclass
    class FunctionSchema:  # type: ignore[no-redef]
        """Minimal stand-in for pipecat's ``FunctionSchema``."""

        name: str
        description: str
        properties: dict
        required: list = field(default_factory=list)

        def to_default_dict(self) -> dict:
            return {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": self.required,
                },
            }

    @dataclass
    class ToolsSchema:  # type: ignore[no-redef]
        """Minimal stand-in for pipecat's ``ToolsSchema``."""

        standard_tools: list
        custom_tools: dict | None = None


# ── Tool schemas (what the LLM sees) ──────────────────────────────────────────

TOOLS = ToolsSchema(
    standard_tools=[
        FunctionSchema(
            name="web_search",
            description="Search the internet for up-to-date information, news, or facts you don't have.",
            properties={
                "query": {"type": "string", "description": "The search query"},
                "max_results": {"type": "integer", "description": "Number of results to return (default 4)", "default": 4},
            },
            required=["query"],
        ),
        FunctionSchema(
            name="shell",
            description="Run a shell command on the user's machine. Use for system operations, file listings, process management, etc.",
            properties={
                "command": {"type": "string", "description": "The shell command to execute"},
            },
            required=["command"],
        ),
        FunctionSchema(
            name="run_privileged",
            description=(
                "Run an administrative command as root (via sudo) when the normal shell "
                "isn't enough. Only an allowlisted set of admin commands is permitted "
                "(systemctl, journalctl, pacman, tlp, power-mode, dmesg, sensors); anything "
                "outside that list or anything destructive is refused. Pass the bare command "
                "without a leading 'sudo'."
            ),
            properties={
                "command": {"type": "string", "description": "Admin command to run as root, e.g. 'systemctl restart bluetooth'"},
            },
            required=["command"],
        ),
        FunctionSchema(
            name="read_file",
            description="Read the contents of a file from the filesystem.",
            properties={
                "path": {"type": "string", "description": "Absolute or relative path to the file"},
            },
            required=["path"],
        ),
        FunctionSchema(
            name="write_file",
            description="Write or overwrite a file on the filesystem.",
            properties={
                "path": {"type": "string", "description": "Path of the file to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            required=["path", "content"],
        ),
        FunctionSchema(
            name="delegate_to_agent",
            description=(
                "Delegate a complex task to a specialised background agent. "
                "Use 'code' for programming/debugging, 'research' for deep information gathering, "
                "'system' for Linux administration. The agent runs in the background and notifies you when done."
            ),
            properties={
                "agent": {
                    "type": "string",
                    "enum": ["code", "research", "system"],
                    "description": "Agent type: 'code', 'research', or 'system'",
                },
                "task": {"type": "string", "description": "Detailed description of the task to perform"},
            },
            required=["agent", "task"],
        ),
        FunctionSchema(
            name="run_ai_cli",
            description=(
                "Dispatch a coding task to a local AI coding CLI (claude, codex, or opencode) "
                "running non-interactively on the user's machine, using their existing subscription. "
                "Streams the CLI's output to the dashboard and returns the result. "
                "Leave 'cli' empty to use the configured/auto-detected default (LHASSA_AI_CLI)."
            ),
            properties={
                "task": {"type": "string", "description": "Detailed description of the coding task to perform"},
                "cli": {
                    "type": "string",
                    "enum": ["claude", "codex", "opencode"],
                    "description": "Which local AI CLI to use. Omit to use the configured default.",
                },
            },
            required=["task"],
        ),
        FunctionSchema(
            name="schedule_reminder",
            description="Schedule a reminder that fires at a specific time. Lhassa will notify you when it triggers.",
            properties={
                "name": {"type": "string", "description": "Short name for the reminder"},
                "message": {"type": "string", "description": "Message Lhassa will say when the reminder fires"},
                "in_minutes": {"type": "integer", "description": "Fire after this many minutes from now"},
                "at_time": {"type": "string", "description": "Fire at this exact time. HH:MM (24h local) or ISO 8601"},
            },
            required=["name", "message"],
        ),
        FunctionSchema(
            name="list_reminders",
            description="List all pending scheduled reminders.",
            properties={},
            required=[],
        ),
        FunctionSchema(
            name="read_emails",
            description="Read the latest emails from the user's Outlook inbox. Defaults to unread only.",
            properties={
                "count": {"type": "integer", "description": "Number of emails to fetch (default 5)"},
                "unread_only": {"type": "boolean", "description": "If true, only return unread emails (default true)"},
            },
            required=[],
        ),
        FunctionSchema(
            name="send_email",
            description="Send an email from the user's Outlook account.",
            properties={
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body as plain text"},
            },
            required=["to", "subject", "body"],
        ),
        FunctionSchema(
            name="read_calendar",
            description="Read upcoming events from the user's Outlook calendar.",
            properties={
                "days_ahead": {"type": "integer", "description": "How many days ahead to look (default 3)"},
            },
            required=[],
        ),
        FunctionSchema(
            name="create_event",
            description="Create a calendar event in the user's Outlook calendar.",
            properties={
                "subject": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start time in ISO 8601 UTC format (e.g. 2024-01-15T14:00:00)"},
                "end": {"type": "string", "description": "End time in ISO 8601 UTC format"},
                "body": {"type": "string", "description": "Event description (optional)"},
                "location": {"type": "string", "description": "Event location (optional)"},
            },
            required=["subject", "start", "end"],
        ),
        FunctionSchema(
            name="github_list_repos",
            description="List the user's GitHub repositories, sorted by most recently updated.",
            properties={
                "limit": {"type": "integer", "description": "Max number of repos to return (default 10)"},
            },
            required=[],
        ),
        FunctionSchema(
            name="github_list_issues",
            description="List open issues for a GitHub repository.",
            properties={
                "repo": {"type": "string", "description": "Repository in owner/name format (e.g. torvalds/linux)"},
                "state": {"type": "string", "description": "'open' or 'closed' (default 'open')"},
                "limit": {"type": "integer", "description": "Max number of issues (default 10)"},
            },
            required=["repo"],
        ),
        FunctionSchema(
            name="github_get_issue",
            description="Get the full details of a specific GitHub issue.",
            properties={
                "repo": {"type": "string", "description": "Repository in owner/name format"},
                "number": {"type": "integer", "description": "Issue number"},
            },
            required=["repo", "number"],
        ),
        FunctionSchema(
            name="github_list_prs",
            description="List pull requests for a GitHub repository.",
            properties={
                "repo": {"type": "string", "description": "Repository in owner/name format"},
                "state": {"type": "string", "description": "'open', 'closed', or 'all' (default 'open')"},
                "limit": {"type": "integer", "description": "Max number of PRs (default 10)"},
            },
            required=["repo"],
        ),
        FunctionSchema(
            name="github_get_pr",
            description="Get full details of a specific GitHub pull request.",
            properties={
                "repo": {"type": "string", "description": "Repository in owner/name format"},
                "number": {"type": "integer", "description": "PR number"},
            },
            required=["repo", "number"],
        ),
        FunctionSchema(
            name="github_comment",
            description="Post a comment on a GitHub issue or pull request.",
            properties={
                "repo": {"type": "string", "description": "Repository in owner/name format"},
                "number": {"type": "integer", "description": "Issue or PR number"},
                "body": {"type": "string", "description": "Comment text"},
            },
            required=["repo", "number", "body"],
        ),
        FunctionSchema(
            name="github_notifications",
            description="List unread GitHub notifications (mentions, reviews, issues assigned to you, etc.).",
            properties={
                "limit": {"type": "integer", "description": "Max notifications to return (default 10)"},
            },
            required=[],
        ),
        FunctionSchema(
            name="analyze_screen",
            description="Take a screenshot and describe what is on the screen using a vision AI model. Useful when the user says 'what's on my screen', 'what does this error say', etc.",
            properties={
                "question": {"type": "string", "description": "Specific question about the screen content (default: describe what you see)"},
            },
            required=[],
        ),
        FunctionSchema(
            name="analyze_image",
            description="Analyse an image file from the filesystem using a vision AI model.",
            properties={
                "path": {"type": "string", "description": "Path to the image file"},
                "question": {"type": "string", "description": "Question to ask about the image"},
            },
            required=["path"],
        ),
        FunctionSchema(
            name="watch_process",
            description="Monitor a running process and notify you when it finishes. Useful for long builds, downloads, scripts, etc.",
            properties={
                "pid": {"type": "integer", "description": "Process ID to watch"},
                "name": {"type": "string", "description": "Process name substring to find and watch (used if pid is not given)"},
                "label": {"type": "string", "description": "Friendly name for notifications (optional)"},
            },
            required=[],
        ),
        FunctionSchema(
            name="watch_file",
            description="Watch a file for new content or a specific pattern and notify you when it appears. Good for log files.",
            properties={
                "path": {"type": "string", "description": "Path to the file to watch"},
                "pattern": {"type": "string", "description": "Notify when a line containing this text appears. If omitted, notify on any change."},
                "label": {"type": "string", "description": "Friendly name for notifications (optional)"},
            },
            required=["path"],
        ),
        FunctionSchema(
            name="watch_command",
            description="Run a shell command repeatedly until it succeeds or its output contains a string, then notify you.",
            properties={
                "command": {"type": "string", "description": "Shell command to run"},
                "interval": {"type": "integer", "description": "Seconds between checks (default 30)"},
                "until_output_contains": {"type": "string", "description": "Stop and notify when output contains this string. If omitted, stop when exit code is 0."},
                "label": {"type": "string", "description": "Friendly name for notifications (optional)"},
            },
            required=["command"],
        ),
        FunctionSchema(
            name="track_postulaciones",
            description=(
                "Track job/internship applications (CVs sent). op='add' records a new "
                "application (needs empresa), op='update' changes one by id+estado (and "
                "optionally link, notas, categoria, prioridad), "
                "op='list' (default) lists tracked applications, optionally filtered by estado."
            ),
            properties={
                "op": {"type": "string", "enum": ["add", "update", "list"], "description": "add | update | list (default list)"},
                "empresa": {"type": "string", "description": "Company name (required for add)"},
                "rol": {"type": "string", "description": "Role / position"},
                "estado": {"type": "string", "description": "Application status, e.g. pendiente, enviada, entrevista, oferta, rechazada"},
                "fuente": {"type": "string", "description": "Where it came from, e.g. LinkedIn, email"},
                "link": {"type": "string", "description": "Link to the posting. On op='update' this backfills or corrects the stored URL."},
                "notas": {"type": "string", "description": "Free-form notes"},
                "id": {"type": "string", "description": "Application id (required for update)"},
                "categoria": {"type": "string", "description": "Role category, e.g. IA/LLMs, HPC/CUDA, research. Stored, so it stays stable between renders."},
                "prioridad": {"type": "string", "description": "Priority against the owner's stated criteria, e.g. critica, alta, media, baja. Stored, not recomputed."},
            },
            required=[],
        ),
    ]
)
