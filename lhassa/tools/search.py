"""
Web search — tries Brave Search API first, falls back to DuckDuckGo.

Brave Search is more stable (official REST API), DuckDuckGo is the fallback
when no BRAVE_API_KEY is configured or when Brave fails.

Get a free Brave API key at: https://api.search.brave.com/register
"""

import asyncio
from loguru import logger

import httpx

from lhassa.config import Config

# Cached client — reuses TCP connections across calls
_http: httpx.AsyncClient | None = None


def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=10)
    return _http


async def web_search(query: str, max_results: int = 4) -> list[dict]:
    """
    Search the web. Returns a list of {title, body, url} dicts.
    Tries Brave first, falls back to DuckDuckGo.
    """
    config = Config()

    if config.brave_api_key:
        try:
            return await _brave_search(query, max_results, config.brave_api_key)
        except Exception as e:
            logger.warning(f"Brave search failed ({e}), falling back to DuckDuckGo")

    return await _ddg_search(query, max_results)


async def _brave_search(query: str, max_results: int, api_key: str) -> list[dict]:
    r = await _get_http().get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        },
        params={"q": query, "count": max_results, "text_decorations": False},
    )
    r.raise_for_status()
    data = r.json()

    results = []
    for item in data.get("web", {}).get("results", [])[:max_results]:
        results.append({
            "title": item.get("title", ""),
            "body": item.get("description", ""),
            "url": item.get("url", ""),
        })
    return results


async def _ddg_search(query: str, max_results: int) -> list[dict]:
    from duckduckgo_search import DDGS
    raw = await asyncio.wait_for(
        asyncio.to_thread(lambda: list(DDGS().text(query, max_results=max_results))),
        timeout=15.0,
    )
    return [{"title": r["title"], "body": r["body"], "url": r.get("href", "")} for r in raw]
