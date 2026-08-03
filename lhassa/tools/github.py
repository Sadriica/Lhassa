# SPDX-License-Identifier: AGPL-3.0-or-later
"""
GitHub tools — read repos, issues, PRs, and create comments via the GitHub REST API.
Requires GITHUB_TOKEN in .env (Settings → Developer settings → Personal access tokens).
"""

import httpx

GITHUB_BASE = "https://api.github.com"

# Cached client — reuses TCP connections across calls
_http: httpx.AsyncClient | None = None


def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=15)
    return _http


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get(token: str, path: str, params: dict | None = None) -> dict | list:
    r = await _get_http().get(
        f"{GITHUB_BASE}{path}",
        headers=_headers(token),
        params=params or {},
    )
    r.raise_for_status()
    return r.json()


async def _post(token: str, path: str, body: dict) -> dict:
    r = await _get_http().post(
        f"{GITHUB_BASE}{path}",
        headers=_headers(token),
        json=body,
    )
    r.raise_for_status()
    return r.json()


# ── Repos ──────────────────────────────────────────────────────────────────────

async def list_repos(token: str, limit: int = 10) -> list[dict]:
    """List the authenticated user's repositories."""
    data = await _get(token, "/user/repos", {"sort": "updated", "per_page": limit})
    return [
        {"name": r["full_name"], "description": r.get("description", ""), "stars": r["stargazers_count"], "url": r["html_url"]}
        for r in data
    ]


# ── Issues ─────────────────────────────────────────────────────────────────────

async def list_issues(token: str, repo: str, state: str = "open", limit: int = 10) -> list[dict]:
    """List issues for owner/repo."""
    data = await _get(token, f"/repos/{repo}/issues", {"state": state, "per_page": limit})
    # GitHub returns PRs in the issues endpoint too — filter them out
    issues = [i for i in data if "pull_request" not in i]
    return [
        {"number": i["number"], "title": i["title"], "state": i["state"], "url": i["html_url"], "body": (i.get("body") or "")[:500]}
        for i in issues
    ]


async def get_issue(token: str, repo: str, number: int) -> dict:
    data = await _get(token, f"/repos/{repo}/issues/{number}")
    return {
        "number": data["number"],
        "title": data["title"],
        "state": data["state"],
        "body": data.get("body", ""),
        "url": data["html_url"],
        "author": data["user"]["login"],
        "comments": data["comments"],
    }


async def create_issue_comment(token: str, repo: str, number: int, body: str) -> str:
    data = await _post(token, f"/repos/{repo}/issues/{number}/comments", {"body": body})
    return data["html_url"]


# ── Pull requests ──────────────────────────────────────────────────────────────

async def list_prs(token: str, repo: str, state: str = "open", limit: int = 10) -> list[dict]:
    data = await _get(token, f"/repos/{repo}/pulls", {"state": state, "per_page": limit})
    return [
        {"number": p["number"], "title": p["title"], "state": p["state"], "url": p["html_url"],
         "author": p["user"]["login"], "draft": p.get("draft", False)}
        for p in data
    ]


async def get_pr(token: str, repo: str, number: int) -> dict:
    data = await _get(token, f"/repos/{repo}/pulls/{number}")
    return {
        "number": data["number"],
        "title": data["title"],
        "state": data["state"],
        "body": data.get("body", ""),
        "url": data["html_url"],
        "author": data["user"]["login"],
        "base": data["base"]["ref"],
        "head": data["head"]["ref"],
        "mergeable": data.get("mergeable"),
        "changed_files": data.get("changed_files", 0),
        "additions": data.get("additions", 0),
        "deletions": data.get("deletions", 0),
    }


# ── Notifications ─────────────────────────────────────────────────────────────

async def list_notifications(token: str, limit: int = 10) -> list[dict]:
    """List unread GitHub notifications."""
    data = await _get(token, "/notifications", {"per_page": limit})
    return [
        {
            "id": n["id"],
            "repo": n["repository"]["full_name"],
            "type": n["subject"]["type"],
            "title": n["subject"]["title"],
            "reason": n["reason"],
        }
        for n in data
    ]
