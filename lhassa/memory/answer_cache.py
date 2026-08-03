# SPDX-License-Identifier: AGPL-3.0-or-later
"""The answer cache — a $0 semantic cache over past chat answers.

The Telegram chat think (``notify.telegram_inbound._brain_reply``) spends a real
model call on every non-command message, even when it has effectively answered the
exact same STABLE question before ("what's the capital of France", "how do I undo
a git commit"). This module is the store/lookup half of that cache: it never
decides WHETHER a question is cacheable (that is ``notify.intent.classify``'s job,
the deterministic gate) — it only remembers and recalls, embedding-similarity-first,
using the same offline-capable :func:`lhassa.memory.embeddings.get_embedder` /
:func:`lhassa.memory.embeddings.cosine` pair memory layer 4 already uses.

Both :func:`store` and :func:`lookup` are best-effort and NEVER raise: any failure
(a DB error, an embedder hiccup) degrades to a no-op store / a cache MISS — the
calling chat turn always falls through to a normal think, it never breaks.

TTL matters even for a "cacheable" (rot-resistant) question: an entry older than
``config.answer_cache_ttl`` is ignored on lookup, so a stale answer never wins
just because it is a close embedding match — cacheable means "usually stable",
not "eternal".
"""

from __future__ import annotations

import contextlib
import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from lhassa.config import Config

# How many of the most recent cached rows a lookup scans (a linear cosine scan —
# fine at this scale; a real ANN index would be overkill for a personal cache).
_LOOKUP_LIMIT = 500


def _age_seconds(creado_en: "str | None", *, now: Optional[float] = None) -> float:
    """Seconds since an ISO-8601 ``creado_en`` timestamp. Unparseable → +inf (stale)."""
    if not creado_en:
        return float("inf")
    try:
        dt = datetime.fromisoformat(str(creado_en))
    except ValueError:
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now if now is not None else time.time()
    return max(0.0, now - dt.timestamp())


async def store(
    instance_id: str,
    question: str,
    answer: str,
    *,
    tags: "list[str] | None" = None,
    config=None,
) -> Optional[str]:
    """Persist one (question, answer) pair for future cache lookups.

    Embeds ``question`` with the configured embedder (offline hashing embedder
    by default, so this always works with $0 and no network) and inserts a new
    row. Best-effort: any failure is logged at DEBUG and swallowed — a cache
    write can never break the chat turn that just produced the answer.
    Returns the new row's id, or ``None`` on a no-op / failure.
    """
    config = config or Config
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not answer:
        return None
    try:
        from lhassa.core import db
        from lhassa.memory.embeddings import get_embedder

        embedder = get_embedder(config)
        vector = embedder.embed([question])[0]
        return await db.insert_answer_cache(
            {
                "instance_id": instance_id,
                "question": question,
                "answer": answer,
                "embedding": vector,
                "tags": list(tags or []),
            }
        )
    except Exception as exc:  # never break the caller over a cache write
        logger.debug(f"answer_cache: store failed, degrading to no-op: {exc}")
        return None


async def lookup(
    instance_id: str,
    question: str,
    *,
    threshold: float = 0.90,
    config=None,
    stats: "dict | None" = None,
) -> "tuple[str, float] | None":
    """Return ``(answer, similarity)`` for the best cached match, or ``None``.

    Loads the ``_LOOKUP_LIMIT`` most recent (non-expired) cached rows for
    ``instance_id``, embeds ``question`` with the same embedder, and returns the
    highest-cosine match at or above ``threshold``. A row older than
    ``config.answer_cache_ttl`` seconds is ignored (TTL is applied here, at read
    time, so entries age out without a separate sweep). The winning row's
    ``hits`` counter is bumped (best-effort). Never raises: any failure degrades
    to a MISS (``None``), so a broken cache never breaks a chat reply.

    ``stats`` is an optional, keyword-only diagnostics out-param — pass an empty
    dict and this fills it with ``{"best_similarity": float, "scanned": int,
    "hit": bool}`` regardless of hit/miss, so a caller can tell WHY a lookup
    missed (a near-miss under threshold vs. nothing to scan at all vs. an
    embedder/DB failure). Existing callers that don't pass ``stats`` see no
    change in behavior or return value.
    """
    config = config or Config
    question = (question or "").strip()
    if stats is not None:
        stats.clear()
        stats.update({"best_similarity": -1.0, "scanned": 0, "hit": False})
    if not question:
        return None
    try:
        from lhassa.core import db
        from lhassa.memory.embeddings import cosine, get_embedder

        ttl = float(getattr(config, "answer_cache_ttl", 604800.0) or 604800.0)
        rows = await db.list_answer_cache(instance_id, limit=_LOOKUP_LIMIT)
        if not rows:
            return None

        now = time.time()
        fresh = [r for r in rows if _age_seconds(r.get("creado_en"), now=now) <= ttl]
        if not fresh:
            return None

        embedder = get_embedder(config)
        qvec = embedder.embed([question])[0]

        best_row = None
        best_sim = -1.0
        for row in fresh:
            sim = cosine(qvec, row.get("embedding") or [])
            if sim > best_sim:
                best_sim = sim
                best_row = row

        if stats is not None:
            stats["best_similarity"] = best_sim
            stats["scanned"] = len(fresh)

        if best_row is None or best_sim < threshold:
            return None

        with contextlib.suppress(Exception):
            await db.bump_answer_cache_hit(instance_id, best_row["id"])

        if stats is not None:
            stats["hit"] = True

        return str(best_row["answer"]), best_sim
    except Exception as exc:  # never break the caller over a cache read
        logger.debug(f"answer_cache: lookup failed, degrading to a miss: {exc}")
        return None
