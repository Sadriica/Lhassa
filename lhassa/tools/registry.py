"""
Lhassa tool registry — the public seam that composes the LLM-visible schemas and
async handlers for all tools.

This module is the stable import surface: ``TOOLS`` (the schema catalog),
``register_tools()`` (the wiring), and every ``handle_<name>`` handler (plus the
shared/family helpers) are re-exported here so ``from lhassa.tools.registry
import X`` keeps resolving exactly as before. The definitions themselves live in:

  * ``lhassa.tools.schemas``           — the ``TOOLS`` catalog (JSON schemas).
  * ``lhassa.tools.handlers._shared``  — cross-family machinery (``_bump_tool``).
  * ``lhassa.tools.handlers.*``        — the ``handle_<name>`` handlers, by family.
"""

from loguru import logger

from lhassa.tools.schemas import TOOLS
from lhassa.tools.handlers._shared import _bump_tool
from lhassa.tools.handlers.web import handle_web_search
from lhassa.tools.handlers.shell import handle_shell, handle_run_privileged
from lhassa.tools.handlers.files import handle_read_file, handle_write_file
from lhassa.tools.handlers.agents import (
    handle_delegate_to_agent,
    handle_run_ai_cli,
)
from lhassa.tools.handlers.schedule import (
    handle_schedule_reminder,
    handle_list_reminders,
)
from lhassa.tools.handlers.outlook import (
    _graph_token,
    handle_read_emails,
    handle_send_email,
    handle_read_calendar,
    handle_create_event,
)
from lhassa.tools.handlers.github import (
    _github_token,
    handle_github_list_repos,
    handle_github_list_issues,
    handle_github_get_issue,
    handle_github_list_prs,
    handle_github_get_pr,
    handle_github_comment,
    handle_github_notifications,
)
from lhassa.tools.handlers.vision import handle_analyze_screen, handle_analyze_image
from lhassa.tools.handlers.monitor import (
    handle_watch_process,
    handle_watch_file,
    handle_watch_command,
)
from lhassa.tools.handlers.jobs import handle_track_postulaciones

__all__ = [
    "TOOLS",
    "register_tools",
    "_bump_tool",
    "_graph_token",
    "_github_token",
    "handle_web_search",
    "handle_shell",
    "handle_run_privileged",
    "handle_read_file",
    "handle_write_file",
    "handle_delegate_to_agent",
    "handle_run_ai_cli",
    "handle_schedule_reminder",
    "handle_list_reminders",
    "handle_read_emails",
    "handle_send_email",
    "handle_read_calendar",
    "handle_create_event",
    "handle_github_list_repos",
    "handle_github_list_issues",
    "handle_github_get_issue",
    "handle_github_list_prs",
    "handle_github_get_pr",
    "handle_github_comment",
    "handle_github_notifications",
    "handle_analyze_screen",
    "handle_analyze_image",
    "handle_watch_process",
    "handle_watch_file",
    "handle_watch_command",
    "handle_track_postulaciones",
]


# ── Registration ───────────────────────────────────────────────────────────────

def register_tools(llm):
    """Register all tool handlers with the LLM service."""
    llm.register_function("web_search", handle_web_search)
    llm.register_function("shell", handle_shell)
    llm.register_function("run_privileged", handle_run_privileged)
    llm.register_function("read_file", handle_read_file)
    llm.register_function("write_file", handle_write_file)
    llm.register_function("delegate_to_agent", handle_delegate_to_agent)
    llm.register_function("run_ai_cli", handle_run_ai_cli)
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
    llm.register_function("analyze_screen", handle_analyze_screen)
    llm.register_function("analyze_image", handle_analyze_image)
    llm.register_function("watch_process", handle_watch_process)
    llm.register_function("watch_file", handle_watch_file)
    llm.register_function("watch_command", handle_watch_command)
    llm.register_function("track_postulaciones", handle_track_postulaciones)
    logger.info(
        "Tools registered: web_search, shell, run_privileged, read_file, write_file, "
        "delegate_to_agent, run_ai_cli, schedule_reminder, list_reminders, "
        "read_emails, send_email, read_calendar, create_event, "
        "github_list_repos, github_list_issues, github_get_issue, "
        "github_list_prs, github_get_pr, github_comment, github_notifications, "
        "analyze_screen, analyze_image, watch_process, watch_file, watch_command, "
        "track_postulaciones"
    )
