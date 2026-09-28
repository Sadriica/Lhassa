# SPDX-License-Identifier: AGPL-3.0-or-later
"""Document perception; the brain reads the OWNER'S OWN documents, $0, local.

The owner points Lhassa at a small set of KNOWLEDGE ROOTS (``config.knowledge_roots``,
``LHASSA_KNOWLEDGE_ROOTS``, a CSV of directories; notes, papers, projects), NOT
"read the whole PC": nothing is indexed until this is set, and a HARD,
non-negotiable ignore-list keeps secrets out regardless of what a root contains
(mirrors the calendar design; specific sources, not all your data; see
``doc/boundaries.md``). :func:`ingest` walks each root, chunks and embeds every
allowed text/PDF file with the same offline-capable embedder memory layer 4 uses
(:func:`lhassa.memory.embeddings.get_embedder`), and stores the chunks in the
``knowledge`` table (:mod:`lhassa.core.db`); local to ``~/.lhassa/brain.db``,
never the repo. :func:`search` is a bounded cosine scan over this instance's
chunks, the same pattern as :mod:`lhassa.memory.answer_cache`. Every public
function here is $0, deterministic, and NEVER raises; a failure degrades to an
empty result, exactly like the recall/semantic layers in ``memory/load.py``.

Interface layer: imports only ``memory.embeddings``, ``core.db``, stdlib, and
``pypdf`` (lazy, optional); no shell. See ``tests/test_architecture.py``.
"""

from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path

from loguru import logger

from lhassa.config import Config
from lhassa.cv.extract import extract_text
from lhassa.memory.embeddings import cosine, get_embedder

# ── the HARD ignore-list (privacy-critical, non-negotiable) ─────────────────
# Applied regardless of what a knowledge root contains; this is not a default
# that the owner can accidentally widen; it is baked into ``ingest`` itself.

# Directory names pruned outright (walk never descends into them).
_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".cache",
        "dist",
        "build",
        ".ssh",
    }
)

# Filename glob patterns that are always secret-shaped, however deep in a root.
_IGNORE_FILE_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "id_rsa*",
    "*.p12",
    "*secret*",
    "*credential*",
    "*token*",
)

# Only these extensions are indexed by default (text + PDF). Code and anything
# else is out of scope on purpose; this is document perception, not a code
# indexer.
_ALLOWED_TEXT_EXTS: frozenset[str] = frozenset(
    {".md", ".txt", ".rst", ".org", ".tex", ".markdown"}
)
_PDF_EXT = ".pdf"

# Cosine floor below which a search hit is not worth returning.
_SEARCH_FLOOR = 0.05

# Bounded recent-set scanned per search (mirrors answer_cache's _LOOKUP_LIMIT).
_SEARCH_SCAN_LIMIT = 5000


def _is_hard_ignored_dir(name: str) -> bool:
    return name in _IGNORE_DIRS or name.startswith(".")


def _is_hard_ignored_file(name: str) -> bool:
    if name.startswith("."):
        return True
    lower = name.lower()
    return any(fnmatch.fnmatch(lower, pat) for pat in _IGNORE_FILE_GLOBS)


def _is_indexable_ext(name: str) -> bool:
    ext = Path(name).suffix.lower()
    return ext in _ALLOWED_TEXT_EXTS or ext == _PDF_EXT


def _resolve_root(root: str) -> "Path | None":
    try:
        expanded = os.path.expanduser(str(root or "").strip())
        if not expanded:
            return None
        path = Path(expanded)
        if not path.is_absolute():
            return None
        if not path.exists() or not path.is_dir():
            return None
        return path.resolve()
    except Exception:
        return None


def _roots_from_config(config) -> list[str]:
    raw = getattr(config, "knowledge_roots", "") or ""
    return [r.strip() for r in raw.split(",") if r.strip()]


def _extract_text(path: Path) -> str:
    """Read one indexable file's text. Returns ``""`` on any failure (skip).

    Delegates PDF/text extraction to :func:`lhassa.cv.extract.extract_text`
    (the leaf file-I/O seam); one pypdf/UTF-8 implementation, not two.
    """
    try:
        data = path.read_bytes()
    except Exception as exc:
        logger.debug("memory.knowledge: could not read {} ({})", path, exc)
        return ""
    return extract_text(data, filename=path.name)


def _chunk_text(text: str, chunk_chars: int) -> list[str]:
    """Split ``text`` into ~``chunk_chars``-sized pieces (a char-count proxy for
    ~500-token chunks, matching the estimate used elsewhere in memory/load.py)."""
    text = (text or "").strip()
    if not text:
        return []
    chunk_chars = max(200, int(chunk_chars or 2000))
    chunks = []
    for i in range(0, len(text), chunk_chars):
        piece = text[i : i + chunk_chars].strip()
        if piece:
            chunks.append(piece)
    return chunks


def _iter_files(root: Path):
    """Yield every file path under ``root``, pruning hard-ignored directories
    from the walk. Per-file ignore/extension/size checks happen in the caller."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_hard_ignored_dir(d)]
        for name in filenames:
            yield Path(dirpath) / name


async def ingest(instance_id: str, *, config=None) -> dict:
    """Walk every root in ``config.knowledge_roots``, indexing new/changed files.

    Returns ``{"roots": int, "files_scanned": int, "files_indexed": int,
    "chunks": int, "skipped_secrets": int}``. Never raises; any per-file or
    per-root failure is logged and skipped, the walk continues.
    """
    config = config or Config
    instance_id = instance_id or getattr(config, "instance_id", "inst_personal")
    result = {
        "roots": 0,
        "files_scanned": 0,
        "files_indexed": 0,
        "chunks": 0,
        "skipped_secrets": 0,
    }
    try:
        roots = _roots_from_config(config)
        if not roots:
            return result

        from lhassa.core import db

        await db.init_db()
        existing = await db.list_knowledge_files(instance_id)
        max_bytes = int(getattr(config, "knowledge_max_file_kb", 512) or 512) * 1024
        chunk_chars = int(getattr(config, "knowledge_chunk_chars", 2000) or 2000)
        embedder = get_embedder(config)

        for raw_root in roots:
            resolved = _resolve_root(raw_root)
            if resolved is None:
                continue
            result["roots"] += 1
            for path in _iter_files(resolved):
                try:
                    name = path.name
                    # Hard ignore: dotfiles, secret-shaped names, disallowed ext.
                    if _is_hard_ignored_file(name):
                        result["skipped_secrets"] += 1
                        continue
                    if not _is_indexable_ext(name):
                        continue

                    result["files_scanned"] += 1

                    try:
                        stat = path.stat()
                    except Exception:
                        continue
                    if stat.st_size > max_bytes:
                        continue

                    source_path = str(path)
                    mtime = float(stat.st_mtime)
                    size = int(stat.st_size)
                    prior = existing.get(source_path)
                    if (
                        prior is not None
                        and abs(prior[0] - mtime) < 1e-6
                        # size guards against mtime-preserving rewrites; -1 =
                        # pre-size-column row, trust the mtime alone.
                        and (prior[1] < 0 or prior[1] == size)
                    ):
                        continue  # unchanged; skip

                    text = _extract_text(path)
                    if not text:
                        continue

                    chunks = _chunk_text(text, chunk_chars)
                    if not chunks:
                        continue

                    if prior is not None:
                        await db.delete_knowledge_file(instance_id, source_path)

                    vectors = embedder.embed(chunks)
                    now = time.strftime("%Y-%m-%dT%H:%M:%S")
                    for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
                        await db.insert_knowledge_chunk(
                            {
                                "instance_id": instance_id,
                                "source_path": source_path,
                                "chunk_index": idx,
                                "text": chunk,
                                "embedding": vector,
                                "mtime": mtime,
                                "size": size,
                                "ingested_at": now,
                            }
                        )
                    result["files_indexed"] += 1
                    result["chunks"] += len(chunks)
                except Exception as exc:  # one bad file must not lose the walk
                    logger.debug("memory.knowledge: skipping {} ({})", path, exc)
                    continue
        return result
    except Exception as exc:
        logger.warning("memory.knowledge: ingest failed, degrading to no-op: {}", exc)
        return result


async def search(
    instance_id: str, query: str, *, limit: int = 5, config=None
) -> list[dict]:
    """Return the top ``limit`` chunks matching ``query``: $0, never raises.

    Embeds ``query`` with the configured embedder, cosine-scans a bounded
    recent set of this instance's indexed chunks, and returns
    ``{"text", "source_path", "score"}`` above a small floor, highest first.
    Any failure degrades to ``[]``.
    """
    config = config or Config
    query = (query or "").strip()
    if not query:
        return []
    try:
        from lhassa.core import db

        rows = await db.list_knowledge_chunks(instance_id, limit=_SEARCH_SCAN_LIMIT)
        if not rows:
            return []

        embedder = get_embedder(config)
        qvec = embedder.embed([query])[0]

        scored: list[tuple[float, dict]] = []
        for row in rows:
            vec = row.get("embedding") or []
            if len(vec) != len(qvec):
                continue
            sim = cosine(qvec, vec)
            if sim >= _SEARCH_FLOOR:
                scored.append((sim, row))

        scored.sort(key=lambda s: (-s[0], s[1].get("source_path", "")))
        return [
            {
                "text": row["text"],
                "source_path": row["source_path"],
                "score": sim,
            }
            for sim, row in scored[:limit]
        ]
    except Exception as exc:
        logger.debug("memory.knowledge: search failed, degrading to empty: {}", exc)
        return []


async def clear(instance_id: str, *, config=None) -> int:
    """Wipe the whole knowledge index for ``instance_id``. Never raises."""
    config = config or Config
    try:
        from lhassa.core import db

        await db.init_db()
        return await db.clear_knowledge(instance_id)
    except Exception as exc:
        logger.debug("memory.knowledge: clear failed, degrading to no-op: {}", exc)
        return 0
