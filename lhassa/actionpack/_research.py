# SPDX-License-Identifier: AGPL-3.0-or-later
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

import re

from loguru import logger

# A chat-born query is a sentence, not a document.
_MAX_QUERY = 300

# Results kept in the reply — a phone screen, not a report.
_MAX_RESULTS = 4

# News-intent detection: these queries route to the NEWS vertical first — the
# reason a general engine answers "noticias de hoy" and a bare text search
# doesn't is exactly this routing.
_NEWS_RE = re.compile(
    r"\b(noticias?|news|headlines?|titulares|última hora|breaking)\b", re.IGNORECASE
)

# TODAY-intent: "de hoy"/"today" means the SYSTEM date, not whatever the index
# coughs up. These queries get timelimit="d" (free, when the backend honors it)
# plus OUR deterministic freshness sort/filter below — the reliable half.
_TODAY_RE = re.compile(
    r"\b(hoy|today|ahora|right now|esta (mañana|tarde|noche)|tonight|latest|"
    r"últim[oa]s?( hora)?)\b",
    re.IGNORECASE,
)

# For today-intent, keep results at most this many days old (when enough exist).
_FRESH_DAYS = 2


def _age_days(date_str: str, *, now=None) -> float:
    """Days since a result's date. Relative stamps ('6 h', '35 min') mean TODAY
    (they are the freshest results the vertical returns); unparseable → +inf."""
    from datetime import datetime, timezone

    raw = str(date_str or "").strip()
    if not raw:
        return float("inf")
    if re.search(r"\d+\s*(h|min|hora|horas|m)\b", raw, re.IGNORECASE):
        return 0.0
    m = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    if not m:
        return float("inf")
    try:
        dt = datetime.strptime(m.group(0), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _freshest_first(results: list[dict], *, today_only: bool, now=None) -> list[dict]:
    """Sort by freshness; for today-intent keep the ≤``_FRESH_DAYS``-old subset
    when at least two such results exist (never fabricate freshness — with too
    few fresh items the sorted full list is the honest answer)."""
    ordered = sorted(results, key=lambda r: _age_days(r.get("date", ""), now=now))
    if today_only:
        fresh = [r for r in ordered if _age_days(r.get("date", ""), now=now) <= _FRESH_DAYS]
        if len(fresh) >= 2:
            return fresh
    return ordered


def _format_results(query: str, results: list[dict]) -> str:
    """A compact, sourced digest of the results — deterministic, chat-sized."""
    lines = [f"🔎 What I found for “{query}”:"]
    for r in results[:_MAX_RESULTS]:
        title = str(r.get("title") or "").strip()[:90]
        body = str(r.get("body") or "").strip()[:160]
        url = str(r.get("url") or "").strip()
        source = str(r.get("source") or "").strip()[:40]
        date = str(r.get("date") or "").strip()[:10]
        entry = f"\n• {title}" if title else "\n•"
        if source or date:
            entry += f"\n  {' · '.join(x for x in (source, date) if x)}"
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
    errored = False
    try:
        from lhassa.tools import search as _search

        today_intent = bool(_TODAY_RE.search(query))

        # News intent routes to the news vertical first — that's what makes
        # "noticias de hoy" answerable at all. Today-intent adds timelimit="d"
        # (free when honored) and — reliably — OUR freshness sort/filter, keyed
        # to the SYSTEM date. Any vertical failure falls through to the plain
        # web path rather than ending the run.
        if _NEWS_RE.search(query):
            try:
                results = await _search.news_search(
                    query,
                    max_results=_MAX_RESULTS * 2 if today_intent else _MAX_RESULTS,
                    timelimit="d" if today_intent else None,
                ) or []
                results = _freshest_first(results, today_only=today_intent)
            except Exception as exc:
                logger.debug(f"research_reply: news vertical failed, falling back: {exc}")

        if not results:
            results = await _search.web_search(query, max_results=_MAX_RESULTS) or []
        if not results:
            # A vague query often finds nothing where a dated one succeeds —
            # one deterministic retry anchored to the SYSTEM date (exact for
            # today-intent, month/year otherwise).
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            stamp = now.strftime("%B %d %Y") if today_intent else now.strftime("%B %Y")
            anchored = f"{query} {stamp}"
            results = await _search.web_search(anchored, max_results=_MAX_RESULTS) or []
            if results:
                query = anchored
    except Exception as exc:  # a failed search still owes the owner a message
        errored = True
        logger.warning(f"research_reply: search failed for {query!r}: {exc}")

    if results:
        text = _format_results(query, results)
    elif errored:
        text = (
            f"I went looking for “{query}” but couldn't reach a search backend "
            "just now. Ask me again in a while."
        )
    else:
        text = (
            f"I searched for “{query}” and found nothing useful. A more specific "
            "phrasing — topic, name, place — usually does it; ask me again."
        )

    delivered = False
    try:
        from lhassa.config import Config
        from lhassa.org.deliver import deliver

        # research_reply used to send over Telegram only (``telegram.send``); the
        # deliver seam's text path fans out over ``notify.dispatch`` instead, whose
        # default channel set is "telegram,desktop" (config.notify_channels). To
        # keep this action's UX IDENTICAL (Telegram-only, as promised to the owner
        # who asked a question in that chat) we pin the channel set to "telegram"
        # for this one call rather than letting the broader default apply.
        research_config = Config()
        research_config.notify_channels = "telegram"

        delivered, _artifact_id = await deliver(
            text=text,
            kind="research",
            metadata={"query": query, "results": len(results)},
            origin_department="",  # research is Lhassa's own hand — root-only hop
            config=research_config,
        )
    except Exception as exc:
        logger.warning(f"research_reply: delivery failed: {exc}")

    status = "sent" if delivered else "not delivered"
    return f"research_reply: {len(results)} result(s) {status} for {query!r}"
