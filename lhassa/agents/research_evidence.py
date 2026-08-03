# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic source custody for Lhassa's research line.

Models do not fetch or bless sources here.  This module turns an ``/idea`` input
into a bounded, JSON-serialisable evidence bundle *before* the first research
brain runs:

* explicit URLs are captured directly; text-only ideas use the existing
  Brave/DuckDuckGo search seam;
* redirects are followed one hop at a time, with the SSRF guard re-applied
  before every request;
* every capture records requested/final URL, timestamp, HTTP status, content
  type, SHA-256, visible text, and discovered links;
* a small, deterministic set of likely-primary nested resources is captured;
* failures remain in the bundle as evidence of what could not be inspected.

The bundle is MUNDO-trust input.  It is a chain of custody, not a truth oracle:
the skeptic still has to map each claim to a cited source or mark it unverified.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Awaitable, Callable, Optional
from urllib.parse import urljoin, urlparse

from lhassa.tools.net_safety import ssrf_check

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_MAX_INITIAL_SOURCES = 3
_MAX_TOTAL_SOURCES = 5
_MAX_NESTED_PER_SOURCE = 2
_MAX_REDIRECTS = 3
_RAW_CAP = 256 * 1024
_TEXT_CAP = 8_000
_MODEL_TEXT_CAP = 5_000
_TIMEOUT = 15.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_url(url: str) -> str:
    return (url or "").rstrip(".,;:!?)\"]}'")


def extract_urls(text: str) -> list[str]:
    """Return de-duplicated HTTP(S) URLs in first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.findall(text or ""):
        url = _clean_url(match)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


class _HTMLCapture(HTMLParser):
    _SKIP = frozenset({"script", "style", "head", "meta", "link", "noscript"})

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        low = tag.lower()
        if low in self._SKIP:
            self.depth += 1
        if low == "title":
            self._in_title = True
        if low == "a":
            self._anchor_href = dict(attrs).get("href", "")
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        low = tag.lower()
        if low in self._SKIP and self.depth > 0:
            self.depth -= 1
        if low == "title":
            self._in_title = False
        if low == "a" and self._anchor_href:
            self.links.append((self._anchor_href, " ".join(self._anchor_text).strip()))
            self._anchor_href = ""
            self._anchor_text = []

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        text = " ".join((data or "").split())
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
        if self.depth == 0:
            self.text.append(text)
            if self._anchor_href:
                self._anchor_text.append(text)


def _decode(raw: bytes, encoding: str) -> str:
    try:
        return raw.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _parse_content(raw: bytes, content_type: str, encoding: str, base_url: str) -> dict:
    decoded = _decode(raw, encoding)
    if "html" not in content_type.lower() and not decoded.lstrip().startswith("<"):
        return {"title": "", "text": decoded[:_TEXT_CAP], "links": []}
    parser = _HTMLCapture()
    try:
        parser.feed(decoded)
    except Exception:
        return {"title": "", "text": decoded[:_TEXT_CAP], "links": []}
    links: list[dict] = []
    seen: set[str] = set()
    for href, label in parser.links:
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or absolute in seen:
            continue
        seen.add(absolute)
        links.append({"url": absolute, "label": label[:160]})
    return {
        "title": parser.title[:300],
        "text": " ".join(parser.text)[:_TEXT_CAP],
        "links": links[:80],
    }


async def fetch_source(url: str) -> dict:
    """Capture one URL with bounded I/O and SSRF-safe manual redirects."""
    import httpx

    requested = _clean_url(url)
    current = requested
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT) as client:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                refusal = ssrf_check(current)
                if refusal:
                    return {
                        "requested_url": requested,
                        "final_url": current,
                        "status": "refused",
                        "error": refusal,
                    }
                response = await client.get(
                    current,
                    headers={"User-Agent": "LhassaResearch/1.0 (+local evidence capture)"},
                )
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        break
                    if redirect_count >= _MAX_REDIRECTS:
                        return {
                            "requested_url": requested,
                            "final_url": current,
                            "status": "failed",
                            "error": f"too many redirects (max {_MAX_REDIRECTS})",
                        }
                    current = urljoin(current, location)
                    continue

                raw = response.content[:_RAW_CAP]
                content_type = response.headers.get("content-type", "")
                parsed = _parse_content(
                    raw, content_type, response.encoding or "utf-8", str(response.url)
                )
                return {
                    "requested_url": requested,
                    "final_url": str(response.url),
                    "status": "captured" if response.is_success else "http_error",
                    "http_status": response.status_code,
                    "content_type": content_type,
                    "bytes_captured": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    **parsed,
                }
        return {
            "requested_url": requested,
            "final_url": current,
            "status": "failed",
            "error": "redirect response had no usable location",
        }
    except httpx.TimeoutException:
        return {
            "requested_url": requested,
            "final_url": current,
            "status": "failed",
            "error": f"timeout after {_TIMEOUT}s",
        }
    except Exception as exc:
        return {
            "requested_url": requested,
            "final_url": current,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _nested_score(link: dict) -> tuple[int, str]:
    url = str(link.get("url") or "")
    label = str(link.get("label") or "")
    haystack = f"{url} {label}".lower()
    score = 0
    for token, weight in (
        ("doi.org", 10),
        ("arxiv.org", 10),
        ("github.com", 8),
        (".pdf", 8),
        ("paper", 6),
        ("documentation", 5),
        ("docs", 4),
        ("source", 3),
        ("benchmark", 3),
        ("study", 3),
    ):
        if token in haystack:
            score += weight
    return (-score, url)


def _bundle_digest(bundle: dict) -> str:
    material = {
        "idea": bundle.get("idea"),
        "sources": [
            {
                key: source.get(key)
                for key in (
                    "requested_url",
                    "final_url",
                    "status",
                    "http_status",
                    "sha256",
                    "provenance",
                )
            }
            for source in bundle.get("sources") or []
        ],
    }
    raw = json.dumps(material, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


async def build_evidence_bundle(
    idea: str,
    *,
    search_fn: Optional[Callable[..., Awaitable[list[dict]]]] = None,
    fetch_fn: Optional[Callable[[str], Awaitable[dict]]] = None,
) -> dict:
    """Build the pre-model source dossier for one idea.

    ``search_fn`` and ``fetch_fn`` are injectable so the complete custody logic
    is tested without network access.
    """
    if search_fn is None:
        from lhassa.tools.search import web_search

        search_fn = web_search
    fetch_fn = fetch_fn or fetch_source

    captured_at = _now()
    explicit = extract_urls(idea)
    candidates: list[dict] = []
    search_error = ""
    if explicit:
        candidates = [{"url": url, "provenance": "owner"} for url in explicit]
    else:
        try:
            results = await search_fn(idea, max_results=_MAX_INITIAL_SOURCES)
            candidates = [
                {
                    "url": str(row.get("url") or ""),
                    "title": str(row.get("title") or ""),
                    "search_snippet": str(row.get("body") or ""),
                    "provenance": "search",
                }
                for row in (results or [])
                if row.get("url")
            ]
        except Exception as exc:
            search_error = f"{type(exc).__name__}: {exc}"

    sources: list[dict] = []
    seen: set[str] = set()

    async def capture(candidate: dict) -> None:
        url = _clean_url(str(candidate.get("url") or ""))
        if not url or url in seen or len(sources) >= _MAX_TOTAL_SOURCES:
            return
        seen.add(url)
        result = await fetch_fn(url)
        source = {
            "id": f"S{len(sources) + 1}",
            "captured_at": _now(),
            "provenance": candidate.get("provenance") or "nested",
            "discovered_from": candidate.get("discovered_from"),
            "search_title": candidate.get("title") or "",
            "search_snippet": candidate.get("search_snippet") or "",
            **(result or {}),
        }
        source.setdefault("requested_url", url)
        source.setdefault("final_url", url)
        source.setdefault("status", "failed")
        sources.append(source)

    for candidate in candidates[:_MAX_INITIAL_SOURCES]:
        await capture(candidate)

    # Chase only a bounded, deterministic set of likely-primary nested resources.
    nested: list[dict] = []
    for source in list(sources):
        ranked = [
            link
            for link in sorted(source.get("links") or [], key=_nested_score)
            if _nested_score(link)[0] < 0
        ]
        for link in ranked[:_MAX_NESTED_PER_SOURCE]:
            nested.append(
                {
                    "url": link.get("url"),
                    "title": link.get("label") or "",
                    "provenance": "nested",
                    "discovered_from": source["id"],
                }
            )
    for candidate in nested:
        await capture(candidate)

    bundle = {
        "schema_version": 1,
        "trust": "MUNDO",
        "idea": idea,
        "captured_at": captured_at,
        "search_performed": not bool(explicit),
        "search_error": search_error or None,
        "sources": sources,
        "custody_notes": [
            "A capture proves what bytes Lhassa inspected, not that the source is true.",
            "Every claim must cite a source id from this bundle or be UNVERIFIED.",
            "Only Lhassa instrument rows may be described as our evidence.",
        ],
    }
    bundle["bundle_sha256"] = _bundle_digest(bundle)
    return bundle


def bundle_for_model(bundle: dict) -> dict:
    """Return a bounded copy suitable for the cumulative model context."""
    clean = json.loads(json.dumps(bundle, ensure_ascii=False, default=str))
    for source in clean.get("sources") or []:
        source["text"] = str(source.get("text") or "")[:_MODEL_TEXT_CAP]
        source["links"] = (source.get("links") or [])[:12]
    return clean


__all__ = [
    "build_evidence_bundle",
    "bundle_for_model",
    "extract_urls",
    "fetch_source",
]
