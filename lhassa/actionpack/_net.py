# SPDX-License-Identifier: AGPL-3.0-or-later
"""Net actions: web_fetch, ping_url.

Both share the SSRF guard from _ssrf.py. Content retrieved by web_fetch is
MUNDO-trust: anything derived from it must degrade to TrustLevel.MUNDO.
"""

from __future__ import annotations

import time
from html.parser import HTMLParser

from lhassa.actionpack._ssrf import ssrf_check

# Hard caps so a large response never bloats an episode.
_FETCH_SIZE_CAP = 500 * 1024   # 500 KB response body cap
_FETCH_TEXT_CAP = 2000          # chars returned to the caller
_HTTP_TIMEOUT = 10.0
_MAX_REDIRECTS = 3


# ── HTML → plain text (stdlib only) ──────────────────────────────────────────


class _TextExtractor(HTMLParser):
    """Strip tags/scripts/styles and accumulate visible text nodes."""

    _SKIP_TAGS = frozenset({"script", "style", "head", "meta", "link", "noscript"})

    def __init__(self) -> None:
        super().__init__()
        self._depth = 0   # nesting depth inside a skip tag
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag.lower() in self._SKIP_TAGS:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag.lower() in self._SKIP_TAGS and self._depth > 0:
            self._depth -= 1

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _strip_html(raw: str) -> str:
    """Extract visible text from HTML; fall back to raw if the parser errors."""
    try:
        extractor = _TextExtractor()
        extractor.feed(raw)
        return extractor.get_text()
    except Exception:
        return raw


# ── Actions ───────────────────────────────────────────────────────────────────


async def web_fetch(params: dict) -> str:
    """HTTP GET a URL and return its plain-text content (first 2000 chars).

    SECURITY: scheme must be http/https; host must not resolve to a loopback,
    private, or link-local address (SSRF guard). Content is capped at 500 KB
    before stripping; only the first 2000 chars of extracted text are returned.

    TRUST: the returned content is MUNDO-trust; anything the caller derives
    from it must degrade to TrustLevel.MUNDO.

    params:
        url (required) ; the URL to fetch.

    Returns a short string (URL + first 2000 chars of extracted text) on
    success, or an error string on any failure. Never raises.
    """
    import httpx

    try:
        url = str(params.get("url") or "").strip()
        if not url:
            return "web_fetch: 'url' param is required"

        err = ssrf_check(url)
        if err:
            return f"web_fetch: refused; {err}"

        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            timeout=_HTTP_TIMEOUT,
        ) as client:
            resp = await client.get(url)

        # Cap raw body before processing.
        raw_bytes = resp.content[:_FETCH_SIZE_CAP]
        encoding = resp.encoding or "utf-8"
        try:
            raw_text = raw_bytes.decode(encoding, errors="replace")
        except Exception:
            raw_text = raw_bytes.decode("utf-8", errors="replace")

        # Strip HTML when the content-type suggests it (or try anyway).
        ct = resp.headers.get("content-type", "").lower()
        if "html" in ct or raw_text.lstrip().startswith("<"):
            text = _strip_html(raw_text)
        else:
            text = raw_text

        snippet = text[:_FETCH_TEXT_CAP]
        truncated = "…" if len(text) > _FETCH_TEXT_CAP else ""
        return f"web_fetch {resp.status_code} {url}: {snippet}{truncated}"

    except httpx.TimeoutException:
        url = str(params.get("url") or "")
        return f"web_fetch: timed out ({_HTTP_TIMEOUT}s) fetching {url!r}"
    except httpx.TooManyRedirects:
        url = str(params.get("url") or "")
        return f"web_fetch: too many redirects (max {_MAX_REDIRECTS}) for {url!r}"
    except Exception as exc:
        return f"web_fetch: error: {exc}"


async def ping_url(params: dict) -> str:
    """HTTP HEAD a URL and report the status code and elapsed time (ms).

    Applies the same SSRF guard as web_fetch (shared helper). Never raises.

    params:
        url (required) ; the URL to ping.

    Returns "ping_url <status> <url> <elapsed>ms" on success, or an error
    string. Never raises.
    """
    import httpx

    try:
        url = str(params.get("url") or "").strip()
        if not url:
            return "ping_url: 'url' param is required"

        err = ssrf_check(url)
        if err:
            return f"ping_url: refused; {err}"

        t0 = time.monotonic()
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            timeout=_HTTP_TIMEOUT,
        ) as client:
            resp = await client.head(url)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        return f"ping_url {resp.status_code} {url} {elapsed_ms}ms"

    except httpx.TimeoutException:
        url = str(params.get("url") or "")
        return f"ping_url: timed out ({_HTTP_TIMEOUT}s) pinging {url!r}"
    except Exception as exc:
        return f"ping_url: error: {exc}"
