"""research_reply — fetch live web results and answer the owner over Telegram.

The Telegram chat think is deliberately TOOL-LESS (fast, cheap) — it cannot see
the web. When the owner asks for live information, the inbound poller mints THIS
action instead (the ``[[research: …]]`` marker protocol in
:mod:`lhassa.notify.telegram_inbound`) and the next heartbeat runs it: one web
search (Brave → DuckDuckGo fallback — the same seam the voice tool uses), a
compact deterministic summary with sources, delivered back through the Telegram
channel. No LLM call: the results speak for themselves, with their links. $0.
"""

from __future__ import annotations

from loguru import logger

# A chat-born query is a sentence, not a document.
_MAX_QUERY = 300

# Results kept in the reply — a phone screen, not a report.
_MAX_RESULTS = 4


def _format_results(query: str, results: list[dict]) -> str:
    """A compact, sourced digest of the results — deterministic, chat-sized."""
    lines = [f"🔎 What I found for “{query}”:"]
    for r in results[:_MAX_RESULTS]:
        title = str(r.get("title") or "").strip()[:90]
        body = str(r.get("body") or "").strip()[:160]
        url = str(r.get("url") or "").strip()
        entry = f"\n• {title}" if title else "\n•"
        if body:
            entry += f"\n  {body}"
        if url:
            entry += f"\n  {url}"
        lines.append(entry)
    return "\n".join(lines)


async def research_reply(params: dict | None = None) -> str:
    """Search the live web for ``params['query']`` and message the owner the sources.

    Deterministic and defensive: a failed search degrades to an honest "couldn't
    reach a search backend" message rather than silence — the owner was promised
    a follow-up, so a follow-up always arrives. Returns the action-result string
    for the episode.
    """
    params = params or {}
    query = str(params.get("query") or "").strip()[:_MAX_QUERY]
    if not query:
        return "research_reply: no query"

    results: list[dict] = []
    try:
        from lhassa.tools.search import web_search

        results = await web_search(query, max_results=_MAX_RESULTS) or []
    except Exception as exc:  # a failed search still owes the owner a message
        logger.warning(f"research_reply: search failed for {query!r}: {exc}")

    if results:
        text = _format_results(query, results)
    else:
        text = (
            f"I went looking for “{query}” but couldn't reach a search backend "
            "just now. Ask me again in a while, or refine the query."
        )

    delivered = False
    try:
        from lhassa.notify import telegram

        delivered = await telegram.send(text)
    except Exception as exc:
        logger.warning(f"research_reply: telegram delivery failed: {exc}")

    status = "sent" if delivered else "not delivered"
    return f"research_reply: {len(results)} result(s) {status} for {query!r}"
