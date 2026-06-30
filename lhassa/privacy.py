"""
Pseudonymization hook (§3-Fase 0/6) — deterministic, regex-based PII scrubbing.

The brain's heartbeat used to lean on free cloud models, so any context leaving
the machine had to be stripped of obvious PII first. This module provides that
hook: a pure-stdlib, no-LLM scrubber that swaps emails, phone numbers, IPv4
addresses and secret-shaped tokens for stable, reversible placeholders
(``<email_1>``, ``<phone_1>``, …) and can put them back afterwards.

The hook is enforced at the provider boundary (``PseudonymizingProvider`` in
``lhassa.llm.provider``) and RELAXES when the active provider is local — local
inference never leaves the box, so there is nothing to scrub.

Hard rule: nothing here may ever raise. On any error it degrades to passthrough
(returns the input unchanged with an empty mapping), so a scrubber bug can never
take down a heartbeat.
"""

from __future__ import annotations

import re

# Placeholder kinds are applied in this fixed order so the result is
# deterministic and earlier (more specific) patterns win over later ones.
# Each match becomes ``<{kind}_{n}>``; a literal value seen twice reuses its
# placeholder so the mapping stays minimal and reversible.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# Prefixed API-key shapes (sk-…, ghp_…, xoxb-…, AKIA…) plus long high-entropy
# tokens. Kept conservative so it does not eat ordinary prose words.
_SECRET = re.compile(
    r"\b(?:"
    r"(?:sk|pk|rk|ghp|gho|ghs|ghu|ghr|xox[baprs])[-_][A-Za-z0-9]{8,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|[A-Za-z0-9_\-]{32,}"
    r")\b"
)
# Phone numbers: an optional + and country code, then groups of digits with the
# usual separators, at least ~7 digits total. Word boundaries avoid swallowing
# adjacent letters.
_PHONE = re.compile(
    r"(?<![\w.])\+?\d(?:[\d\s().\-]{5,}\d)(?![\w.])"
)

# Order matters: email and IPv4 first (most specific), then secrets, then phones
# (the loosest pattern). Once a span is replaced with a ``<kind_n>`` placeholder
# later patterns cannot match inside it.
_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("email", _EMAIL),
    ("ip", _IPV4),
    ("secret", _SECRET),
    ("phone", _PHONE),
]


def _scrub(
    text: str,
    seen: dict[str, str],
    mapping: dict[str, str],
    counters: dict[str, int],
) -> str:
    """Scrub one string using the shared ``seen``/``mapping``/``counters`` state.

    Sharing the state across calls lets the same PII value get the same
    placeholder across several messages of one request.
    """
    if not isinstance(text, str) or not text:
        return text

    def make_repl(kind: str):
        def repl(match: "re.Match[str]") -> str:
            value = match.group(0)
            existing = seen.get(value)
            if existing is not None:
                return existing
            counters[kind] = counters.get(kind, 0) + 1
            placeholder = f"<{kind}_{counters[kind]}>"
            seen[value] = placeholder
            mapping[placeholder] = value
            return placeholder

        return repl

    out = text
    for kind, pattern in _PATTERNS:
        out = pattern.sub(make_repl(kind), out)
    return out


def pseudonymize(text: str) -> tuple[str, dict[str, str]]:
    """Scrub PII from ``text``.

    Returns ``(scrubbed_text, mapping)`` where ``mapping`` is
    ``{placeholder: original}`` (reversible via :func:`depseudonymize`). Never
    raises — on any error returns the input unchanged with an empty mapping.
    """
    try:
        mapping: dict[str, str] = {}
        scrubbed = _scrub(text, {}, mapping, {})
        return scrubbed, mapping
    except Exception:
        return text, {}


def pseudonymize_messages(
    messages: list[dict],
) -> tuple[list[dict], dict[str, str]]:
    """Scrub PII from a chat ``messages`` list, sharing one mapping.

    Each message's string ``content`` is scrubbed; non-string content and
    non-dict entries pass through untouched. The shared mapping means the same
    PII value maps to the same placeholder across every message. Never raises.
    """
    try:
        seen: dict[str, str] = {}
        mapping: dict[str, str] = {}
        counters: dict[str, int] = {}
        scrubbed: list[dict] = []
        for msg in messages:
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                new_msg = dict(msg)
                new_msg["content"] = _scrub(msg["content"], seen, mapping, counters)
                scrubbed.append(new_msg)
            else:
                scrubbed.append(msg)
        return scrubbed, mapping
    except Exception:
        return messages, {}


def depseudonymize(text: str, mapping: dict[str, str]) -> str:
    """Restore originals in ``text`` using ``mapping`` from :func:`pseudonymize`.

    Placeholders are angle-bracket delimited so plain substitution is safe.
    Never raises — on any error returns ``text`` unchanged.
    """
    try:
        if not mapping or not isinstance(text, str):
            return text
        out = text
        for placeholder, original in mapping.items():
            out = out.replace(placeholder, original)
        return out
    except Exception:
        return text
