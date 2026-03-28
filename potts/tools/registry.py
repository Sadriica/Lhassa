"""
Potts tool registry — defines LLM-visible schemas and async handlers for all tools.
"""

import asyncio
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from duckduckgo_search import DDGS
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams


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
            description="Run a shell command on the user's Linux/Arch machine. Use for system operations, file listings, process management, etc.",
            properties={
                "command": {"type": "string", "description": "The shell command to execute"},
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
            name="schedule_reminder",
            description="Schedule a reminder that fires at a specific time. Potts will notify you when it triggers.",
            properties={
                "name": {"type": "string", "description": "Short name for the reminder"},
                "message": {"type": "string", "description": "Message Potts will say when the reminder fires"},
                "in_minutes": {"type": "integer", "description": "Fire after this many minutes from now"},
                "at_time": {"type": "string", "description": "Fire at this exact time — HH:MM (24h local) or ISO 8601"},
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
    ]
)


# ── Handlers ───────────────────────────────────────────────────────────────────

def _bump_tool():
    """Increment the tool_calls metric."""
    from potts.state import state
    state.metrics["tool_calls"] += 1


async def handle_web_search(params: FunctionCallParams):
    query = params.arguments.get("query", "")
    max_results = params.arguments.get("max_results", 4)
    logger.info(f"[tool] web_search: {query!r}")
    print(f"\033[2m[🔍 searching: {query}]\033[0m")
    _bump_tool()

    try:
        from potts.tools.search import web_search
        results = await web_search(query, max_results)
        if not results:
            await params.result_callback("No results found for that query.")
            return
        formatted = "\n\n".join(f"**{r['title']}**\n{r['body']}" for r in results)
        await params.result_callback(formatted)
    except Exception as e:
        logger.error(f"web_search error: {e}")
        await params.result_callback(f"Search error: {e}")


async def handle_shell(params: FunctionCallParams):
    command = params.arguments.get("command", "")
    logger.info(f"[tool] shell: {command!r}")
    print(f"\033[2m[⚡ running: {command}]\033[0m")
    _bump_tool()

    try:
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        if len(output) > 2000:
            output = output[:2000] + "\n... (truncated)"
        await params.result_callback(output)
    except subprocess.TimeoutExpired:
        await params.result_callback("Command timed out after 30 seconds and was cancelled.")
    except Exception as e:
        logger.error(f"shell error: {e}")
        await params.result_callback(f"Shell error: {e}")


async def handle_read_file(params: FunctionCallParams):
    path = params.arguments.get("path", "")
    logger.info(f"[tool] read_file: {path!r}")
    print(f"\033[2m[📄 reading: {path}]\033[0m")
    _bump_tool()

    try:
        content = Path(path).expanduser().read_text()
        if len(content) > 4000:
            content = content[:4000] + "\n... (truncated)"
        await params.result_callback(content)
    except FileNotFoundError:
        await params.result_callback(f"File not found: {path}")
    except Exception as e:
        await params.result_callback(f"Read error: {e}")


async def handle_write_file(params: FunctionCallParams):
    path = params.arguments.get("path", "")
    content = params.arguments.get("content", "")
    logger.info(f"[tool] write_file: {path!r}")
    print(f"\033[2m[✏️  writing: {path}]\033[0m")
    _bump_tool()

    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        await params.result_callback(f"File written: {path}")
    except Exception as e:
        await params.result_callback(f"Write error: {e}")


async def handle_delegate_to_agent(params: FunctionCallParams):
    agent_type = params.arguments.get("agent", "research")
    task = params.arguments.get("task", "")
    logger.info(f"[tool] delegate_to_agent: {agent_type} → {task[:60]!r}")
    print(f"\033[2m[🤖 delegating to {agent_type}: {task[:60]}]\033[0m")
    _bump_tool()

    from potts.agents.code import CodeAgent
    from potts.agents.research import ResearchAgent
    from potts.agents.system import SystemAgent

    agent_map = {"code": CodeAgent, "research": ResearchAgent, "system": SystemAgent}
    agent = agent_map.get(agent_type, ResearchAgent)()

    async def _run():
        try:
            result = await agent.run(task)
            from potts.voice.notifier import notify
            summary = result[:200] + "..." if len(result) > 200 else result
            await notify(f"Agent {agent_type} finished. {summary}")
        except Exception as exc:
            logger.error(f"[delegate_to_agent] background task failed: {exc}")

    asyncio.create_task(_run(), name=f"agent-{agent_type}")
    await params.result_callback(
        f"Agent '{agent_type}' started in the background. I'll notify you when it's done."
    )


async def handle_schedule_reminder(params: FunctionCallParams):
    name = params.arguments.get("name", "Reminder")
    message = params.arguments.get("message", "")
    in_minutes = params.arguments.get("in_minutes")
    at_time = params.arguments.get("at_time")
    _bump_tool()

    from potts.memory.store import add_scheduled_task

    now = datetime.now(timezone.utc)

    if in_minutes is not None:
        run_at = now + timedelta(minutes=int(in_minutes))
        when_str = f"in {in_minutes} minute{'s' if in_minutes != 1 else ''}"
    elif at_time:
        try:
            if len(at_time) <= 5 and ":" in at_time:
                # HH:MM interpreted as local time; convert to UTC-aware datetime
                h, m = at_time.split(":")
                local_naive = datetime.now().replace(
                    hour=int(h), minute=int(m), second=0, microsecond=0
                )
                run_at = local_naive.astimezone(timezone.utc)
                if run_at <= now:
                    run_at += timedelta(days=1)
                when_str = f"at {at_time}"
            else:
                run_at = datetime.fromisoformat(at_time)
                if run_at.tzinfo is None:
                    run_at = run_at.astimezone(timezone.utc)
                when_str = f"at {at_time}"
        except Exception:
            await params.result_callback(
                f"Could not parse time: {at_time!r}. Use HH:MM or ISO 8601."
            )
            return
    else:
        await params.result_callback(
            "Please specify either in_minutes or at_time for the reminder."
        )
        return

    task_id = await add_scheduled_task(name, run_at.isoformat(), message)
    logger.info(f"[scheduler] Task #{task_id} '{name}' scheduled for {run_at.isoformat()}")
    await params.result_callback(f"Reminder '{name}' scheduled {when_str}.")


async def handle_list_reminders(params: FunctionCallParams):
    _bump_tool()
    from potts.memory.store import load_all_scheduled_tasks

    tasks = await load_all_scheduled_tasks()
    pending = [t for t in tasks if t["status"] == "pending"]

    if not pending:
        await params.result_callback("No pending reminders.")
        return

    lines = [
        f"#{t['id']} '{t['name']}' — {t['run_at'][:16].replace('T', ' ')}: {t['message']}"
        for t in pending
    ]
    await params.result_callback("\n".join(lines))


# ── Microsoft Graph helpers ────────────────────────────────────────────────────

async def _graph_token() -> str:
    from potts.config import Config
    from potts.tools.graph import get_access_token
    config = Config()
    return await get_access_token(config.ms_tenant_id, config.ms_client_id)


async def handle_read_emails(params: FunctionCallParams):
    count = params.arguments.get("count", 5)
    unread_only = params.arguments.get("unread_only", True)
    logger.info(f"[tool] read_emails count={count} unread={unread_only}")
    print(f"\033[2m[📧 reading emails...]\033[0m")
    _bump_tool()

    try:
        from potts.tools.graph import read_emails
        token = await _graph_token()
        emails = await read_emails(token, count=count, unread_only=unread_only)

        if not emails:
            suffix = " unread" if unread_only else ""
            await params.result_callback(f"No{suffix} emails in your inbox.")
            return

        lines = []
        for e in emails:
            sender = e.get("from", {}).get("emailAddress", {}).get("name", "?")
            subject = e.get("subject", "(no subject)")
            preview = e.get("bodyPreview", "")[:100]
            date = e.get("receivedDateTime", "")[:10]
            lines.append(f"[{date}] From: {sender} — {subject}. {preview}")

        await params.result_callback("\n\n".join(lines))
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        logger.error(f"read_emails error: {e}")
        await params.result_callback(f"Error reading emails: {e}")


async def handle_send_email(params: FunctionCallParams):
    to = params.arguments.get("to", "")
    subject = params.arguments.get("subject", "")
    body = params.arguments.get("body", "")
    logger.info(f"[tool] send_email to={to!r}")
    print(f"\033[2m[📤 sending email to {to}...]\033[0m")
    _bump_tool()

    try:
        from potts.tools.graph import send_email
        token = await _graph_token()
        await send_email(token, to=to, subject=subject, body=body)
        await params.result_callback(f"Email sent to {to}.")
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        logger.error(f"send_email error: {e}")
        await params.result_callback(f"Error sending email: {e}")


async def handle_read_calendar(params: FunctionCallParams):
    days = params.arguments.get("days_ahead", 3)
    logger.info(f"[tool] read_calendar days={days}")
    print(f"\033[2m[📅 reading calendar ({days} days)...]\033[0m")
    _bump_tool()

    try:
        from potts.tools.graph import read_calendar
        token = await _graph_token()
        events = await read_calendar(token, days_ahead=days)

        if not events:
            await params.result_callback(f"No events in the next {days} day{'s' if days != 1 else ''}.")
            return

        lines = []
        for ev in events:
            subject = ev.get("subject", "(no title)")
            start = ev.get("start", {}).get("dateTime", "")[:16].replace("T", " ")
            end = ev.get("end", {}).get("dateTime", "")[:16].replace("T", " ")
            loc = ev.get("location", {}).get("displayName", "")
            loc_str = f" @ {loc}" if loc else ""
            lines.append(f"{start} – {end}{loc_str}: {subject}")

        await params.result_callback("\n".join(lines))
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        logger.error(f"read_calendar error: {e}")
        await params.result_callback(f"Error reading calendar: {e}")


async def handle_create_event(params: FunctionCallParams):
    subject = params.arguments.get("subject", "")
    start = params.arguments.get("start", "")
    end = params.arguments.get("end", "")
    body = params.arguments.get("body", "")
    location = params.arguments.get("location", "")
    logger.info(f"[tool] create_event subject={subject!r} start={start}")
    print(f"\033[2m[📅 creating event: {subject}]\033[0m")
    _bump_tool()

    try:
        from potts.tools.graph import create_event
        token = await _graph_token()
        await create_event(token, subject=subject, start=start, end=end, body=body, location=location)
        await params.result_callback(
            f"Event '{subject}' created for {start[:16].replace('T', ' ')}."
        )
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        logger.error(f"create_event error: {e}")
        await params.result_callback(f"Error creating event: {e}")


# ── GitHub handlers ───────────────────────────────────────────────────────────

def _github_token() -> str:
    from potts.config import Config
    token = Config().github_token
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set. Add it to your .env file.")
    return token


async def handle_github_list_repos(params: FunctionCallParams):
    limit = params.arguments.get("limit", 10)
    _bump_tool()
    print(f"\033[2m[🐙 GitHub: listing repos...]\033[0m")
    try:
        from potts.tools.github import list_repos
        repos = await list_repos(_github_token(), limit)
        lines = [f"- {r['name']} ({'⭐' + str(r['stars']) if r['stars'] else 'no stars'}): {r['description'] or '(no description)'}" for r in repos]
        await params.result_callback("\n".join(lines) if lines else "No repositories found.")
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        await params.result_callback(f"GitHub error: {e}")


async def handle_github_list_issues(params: FunctionCallParams):
    repo = params.arguments.get("repo", "")
    state_filter = params.arguments.get("state", "open")
    limit = params.arguments.get("limit", 10)
    _bump_tool()
    print(f"\033[2m[🐙 GitHub: issues for {repo}...]\033[0m")
    try:
        from potts.tools.github import list_issues
        issues = await list_issues(_github_token(), repo, state_filter, limit)
        if not issues:
            await params.result_callback(f"No {state_filter} issues in {repo}.")
            return
        lines = [f"#{i['number']} [{i['state']}] {i['title']} — {i['url']}" for i in issues]
        await params.result_callback("\n".join(lines))
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        await params.result_callback(f"GitHub error: {e}")


async def handle_github_get_issue(params: FunctionCallParams):
    repo = params.arguments.get("repo", "")
    number = params.arguments.get("number", 0)
    _bump_tool()
    try:
        from potts.tools.github import get_issue
        issue = await get_issue(_github_token(), repo, number)
        text = f"#{issue['number']} {issue['title']} [{issue['state']}]\nAuthor: {issue['author']}\n{issue['body'][:800]}\n{issue['url']}"
        await params.result_callback(text)
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        await params.result_callback(f"GitHub error: {e}")


async def handle_github_list_prs(params: FunctionCallParams):
    repo = params.arguments.get("repo", "")
    state_filter = params.arguments.get("state", "open")
    limit = params.arguments.get("limit", 10)
    _bump_tool()
    print(f"\033[2m[🐙 GitHub: PRs for {repo}...]\033[0m")
    try:
        from potts.tools.github import list_prs
        prs = await list_prs(_github_token(), repo, state_filter, limit)
        if not prs:
            await params.result_callback(f"No {state_filter} PRs in {repo}.")
            return
        lines = [f"#{p['number']} [{p['state']}{'·draft' if p['draft'] else ''}] {p['title']} by {p['author']}" for p in prs]
        await params.result_callback("\n".join(lines))
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        await params.result_callback(f"GitHub error: {e}")


async def handle_github_get_pr(params: FunctionCallParams):
    repo = params.arguments.get("repo", "")
    number = params.arguments.get("number", 0)
    _bump_tool()
    try:
        from potts.tools.github import get_pr
        pr = await get_pr(_github_token(), repo, number)
        text = (
            f"PR #{pr['number']}: {pr['title']} [{pr['state']}]\n"
            f"Author: {pr['author']} | {pr['head']} → {pr['base']}\n"
            f"+{pr['additions']} -{pr['deletions']} across {pr['changed_files']} files\n"
            f"{(pr['body'] or '')[:500]}\n{pr['url']}"
        )
        await params.result_callback(text)
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        await params.result_callback(f"GitHub error: {e}")


async def handle_github_comment(params: FunctionCallParams):
    repo = params.arguments.get("repo", "")
    number = params.arguments.get("number", 0)
    body = params.arguments.get("body", "")
    _bump_tool()
    print(f"\033[2m[🐙 GitHub: commenting on {repo}#{number}...]\033[0m")
    try:
        from potts.tools.github import create_issue_comment
        url = await create_issue_comment(_github_token(), repo, number, body)
        await params.result_callback(f"Comment posted: {url}")
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        await params.result_callback(f"GitHub error: {e}")


async def handle_github_notifications(params: FunctionCallParams):
    limit = params.arguments.get("limit", 10)
    _bump_tool()
    print(f"\033[2m[🐙 GitHub: checking notifications...]\033[0m")
    try:
        from potts.tools.github import list_notifications
        notifs = await list_notifications(_github_token(), limit)
        if not notifs:
            await params.result_callback("No unread GitHub notifications.")
            return
        lines = [f"[{n['repo']}] {n['type']}: {n['title']} ({n['reason']})" for n in notifs]
        await params.result_callback("\n".join(lines))
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        await params.result_callback(f"GitHub error: {e}")


# ── Registration ───────────────────────────────────────────────────────────────

def register_tools(llm):
    """Register all tool handlers with the LLM service."""
    llm.register_function("web_search", handle_web_search)
    llm.register_function("shell", handle_shell)
    llm.register_function("read_file", handle_read_file)
    llm.register_function("write_file", handle_write_file)
    llm.register_function("delegate_to_agent", handle_delegate_to_agent)
    llm.register_function("schedule_reminder", handle_schedule_reminder)
    llm.register_function("list_reminders", handle_list_reminders)
    llm.register_function("read_emails", handle_read_emails)
    llm.register_function("send_email", handle_send_email)
    llm.register_function("read_calendar", handle_read_calendar)
    llm.register_function("create_event", handle_create_event)
    llm.register_function("github_list_repos", handle_github_list_repos)
    llm.register_function("github_list_issues", handle_github_list_issues)
    llm.register_function("github_get_issue", handle_github_get_issue)
    llm.register_function("github_list_prs", handle_github_list_prs)
    llm.register_function("github_get_pr", handle_github_get_pr)
    llm.register_function("github_comment", handle_github_comment)
    llm.register_function("github_notifications", handle_github_notifications)
    logger.info(
        "Tools registered: web_search, shell, read_file, write_file, "
        "delegate_to_agent, schedule_reminder, list_reminders, "
        "read_emails, send_email, read_calendar, create_event, "
        "github_list_repos, github_list_issues, github_get_issue, "
        "github_list_prs, github_get_pr, github_comment, github_notifications"
    )
