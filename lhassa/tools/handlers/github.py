# SPDX-License-Identifier: AGPL-3.0-or-later
"""GitHub tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.llm_service import FunctionCallParams

from lhassa.tools.handlers._shared import _bump_tool


def _github_token() -> str:
    from lhassa.config import Config
    token = Config().github_token
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set. Add it to your .env file.")
    return token


async def handle_github_list_repos(params: FunctionCallParams):
    limit = params.arguments.get("limit", 10)
    _bump_tool()
    print("\033[2m[🐙 GitHub: listing repos...]\033[0m")
    try:
        from lhassa.tools.github import list_repos
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
        from lhassa.tools.github import list_issues
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
        from lhassa.tools.github import get_issue
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
        from lhassa.tools.github import list_prs
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
        from lhassa.tools.github import get_pr
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
        from lhassa.tools.github import create_issue_comment
        url = await create_issue_comment(_github_token(), repo, number, body)
        await params.result_callback(f"Comment posted: {url}")
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        await params.result_callback(f"GitHub error: {e}")


async def handle_github_notifications(params: FunctionCallParams):
    limit = params.arguments.get("limit", 10)
    _bump_tool()
    print("\033[2m[🐙 GitHub: checking notifications...]\033[0m")
    try:
        from lhassa.tools.github import list_notifications
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
