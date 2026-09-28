# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pluggable, offline-capable embedders for memory layer 4 (semantic retrieval).

Layer 4 was deferred since Phase 1; this is its minimal, dependency-free core.
The default :class:`HashingEmbedder` is pure-stdlib and DETERMINISTIC (same text →
same vector), so the whole semantic layer works and is testable with zero config
and no network. :class:`RemoteEmbedder` is an optional upgrade that hits either
an OpenAI-compatible ``/v1/embeddings`` endpoint (cloud, or Ollama's own OpenAI
compat surface) OR Ollama's NATIVE embeddings API (``/api/embed``, with a
fallback to the older singular ``/api/embeddings``) depending on ``backend``;
it NEVER crashes memory loading; any failure degrades silently to the hashing
embedder. The fallback warning is logged at most ONCE per process.

Backend selection (:func:`get_embedder`) reads three DEFENSIVE, ``getattr``-only
config knobs so this module works unmodified whether or not ``Config`` has been
extended with them:

  - ``embedder_backend`` (default ``"auto"``); ``"auto"`` prefers the existing
    explicit knobs (``embeddings_model``/``embedding_model``) when configured,
    else probes a local Ollama and falls back to hashing if unreachable;
    ``"ollama"`` forces the native Ollama backend (degrades to hashing, with a
    once-per-process warning, if unreachable); ``"hash"`` forces the offline
    embedder unconditionally.
  - ``embedder_model`` (default ``"nomic-embed-text"``); the Ollama model name
    used by the native backend.
  - ``ollama_base_url`` (falls back to ``embeddings_url``, then
    ``http://localhost:11434``); where the native Ollama API lives.

A stable ``fingerprint`` string (e.g. ``"hash:v1"`` / ``"ollama:nomic-embed-text"``)
identifies which backend+model an embedder is CONFIGURED for; distinct from the
``name`` attribute, which reflects the ACTUAL backend used by the last ``embed()``
call (and is what gets stamped onto stored vectors). Mixing vectors from
different embedders makes cosine comparisons garbage, so :func:`needs_reindex`
gives callers a cheap way to detect a stale stamp, and :func:`reindex_all` /
the ``lhassa memory reindex`` CLI command re-embeds everything with whatever
embedder is current.

No numpy: cosine and the vectors are plain Python ``list[float]`` / ``math``.
"""

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

from loguru import logger

from lhassa.config import Config

# Default dimensionality of the offline hashing embedder. Small enough to be
# cheap, large enough that token collisions are rare for short fact strings.
_HASH_DIM = 256

_TOKEN_RE = re.compile(r"[\wáéíóúñü]+", re.UNICODE)

# Module-level flag: log the fallback warning at most once per process so the
# logs are not spammed on every fact write when Ollama is not running.
_warned_fallback = False


@runtime_checkable
class Embedder(Protocol):
    """A text → vector embedder. ``name`` tags which backend produced a vector."""

    name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into equal-length float vectors."""
        ...


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens (unicode-aware), for deterministic hashing."""
    return _TOKEN_RE.findall(text.lower())


def _bucket(token: str, dim: int) -> int:
    """Stable hash of a token into ``[0, dim)``: uses blake2b, NOT ``hash()``.

    Python's builtin ``hash()`` is salted per-process; blake2b is stable across
    runs so the same text always lands in the same buckets (determinism).
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class HashingEmbedder:
    """Deterministic, offline, pure-stdlib embedder (hashed token n-grams).

    Hashes token unigrams and bigrams into a fixed-dimension vector, then
    L2-normalizes. No model, no network. Identical text → identical vector, so
    ``cosine`` of two identical strings is ``≈ 1.0``.
    """

    name = "hashing-v1"
    fingerprint = "hash:v1"

    def __init__(self, dim: int = _HASH_DIM):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _tokenize(text)
        if not tokens:
            return vec
        # Unigrams.
        for tok in tokens:
            vec[_bucket(tok, self.dim)] += 1.0
        # Bigrams (a bit of word-order signal).
        for a, b in zip(tokens, tokens[1:]):
            vec[_bucket(f"{a}\x1f{b}", self.dim)] += 1.0
        return _l2_normalize(vec)


class RemoteEmbedder:
    """Remote embedder (OpenAI-compatible or native Ollama) with a
    ``HashingEmbedder`` fallback.

    Resolution is PER CALL: each ``embed()`` attempts the configured endpoint
    and falls back silently to :class:`HashingEmbedder` on any failure
    (connection error, HTTP error, timeout, bad payload). The fallback warning
    is logged at most ONCE per process via the module-level ``_warned_fallback``
    flag.

    ``backend`` picks the wire format:
      - ``"openai"`` (default, unchanged); POST ``{base_url}/v1/embeddings``
        with ``{"model": ..., "input": [...]}``, OpenAI-compatible response
        shape (a ``data`` list of ``{embedding, index}``). Cloud providers and
        Ollama's own OpenAI-compat surface both speak this.
      - ``"ollama"``: POST ``{base_url}/api/embed`` with
        ``{"model": ..., "input": [...]}`` (Ollama's native batch API,
        response ``{"embeddings": [[...], ...]}``); on any failure of that call
        it retries via the older singular ``{base_url}/api/embeddings``
        (one ``{"model": ..., "prompt": text}`` request per text, response
        ``{"embedding": [...]}``) before giving up to the hashing fallback.

    ``name`` reflects the ACTUAL backend used in the last ``embed()`` call:
    the configured model name on success, or ``HashingEmbedder.name`` on
    fallback. Stamping stored vectors with this post-call name guarantees that
    vectors from different backends are never compared against each other in the
    semantic layer. ``fingerprint`` is the CONFIGURED identity instead; stable
    regardless of whether the last call happened to fall back; useful to detect
    a stale stamp (see :func:`needs_reindex`) before even calling ``embed()``.
    """

    def __init__(
        self, model: str, base_url: str, *, api_key: str = "", backend: str = "openai"
    ):
        self._remote_name = model
        self.name = model  # updated after each embed() call
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.backend = (backend or "openai").strip().lower()
        self._fallback = HashingEmbedder()

    @property
    def fingerprint(self) -> str:
        return f"{self.backend}:{self._remote_name}"

    # Hard per-text bound BEFORE the remote call. nomic-embed-text's context is
    # 2048 tokens and ollama may load it shorter; an over-long input 400s and
    # that call's vectors silently degrade to hashing. MIXED vector spaces in
    # the store (the answer_cache has no model stamp to filter them). Clipping
    # the tail loses little semantically and kills the whole failure class.
    _MAX_EMBED_CHARS = 4000

    def embed(self, texts: list[str]) -> list[list[float]]:
        global _warned_fallback
        texts = [
            (t if len(t) <= self._MAX_EMBED_CHARS else t[: self._MAX_EMBED_CHARS])
            for t in texts
        ]
        try:
            result = self._embed_remote(texts)
            self.name = self._remote_name  # remote succeeded
            return result
        except Exception as exc:
            if not _warned_fallback:
                _warned_fallback = True
                logger.warning(
                    f"RemoteEmbedder({self.backend}): falling back to HashingEmbedder "
                    f"(will not warn again this process): {exc}"
                )
            self.name = self._fallback.name  # reflect actual backend
            return self._fallback.embed(texts)

    def _embed_remote(self, texts: list[str]) -> list[list[float]]:
        if self.backend == "ollama":
            return self._embed_ollama(texts)
        return self._embed_openai(texts)

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = httpx.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": texts},
            headers=headers,
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # Preserve request order via the returned ``index``.
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [list(map(float, d["embedding"])) for d in ordered]

    def _embed_ollama(self, texts: list[str]) -> list[list[float]]:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            resp = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": texts},
                headers=headers,
                timeout=5.0,
            )
            resp.raise_for_status()
            embeddings = resp.json()["embeddings"]
            if not embeddings:
                raise ValueError("empty 'embeddings' in /api/embed response")
            return [list(map(float, v)) for v in embeddings]
        except Exception:
            # Older Ollama builds only speak the singular /api/embeddings shape
            # (one prompt per request); try that before giving up entirely.
            out: list[list[float]] = []
            for text in texts:
                resp = httpx.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                    headers=headers,
                    timeout=5.0,
                )
                resp.raise_for_status()
                out.append(list(map(float, resp.json()["embedding"])))
            return out


class OpenAICompatEmbedder:
    """Backward-compatible alias for code that pre-dates RemoteEmbedder.

    New code should use :class:`RemoteEmbedder` or :func:`get_embedder`.
    This class is kept so existing tests and plugins that import it by name
    continue to work without changes.
    """

    _CLOUD_BASE = "https://api.openai.com/v1"

    def __init__(self, model: str, base_url: str, *, api_key: str = "", timeout: float = 10.0):
        self.name = model
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._fallback = HashingEmbedder()

    @property
    def fingerprint(self) -> str:
        return f"openai:{self.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._embed_remote(texts)
        except Exception:
            return self._fallback.embed(texts)

    def _embed_remote(self, texts: list[str]) -> list[list[float]]:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = httpx.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": texts},
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [list(map(float, d["embedding"])) for d in ordered]

    def reachable(self) -> bool:
        """Best-effort liveness probe; any failure → ``False`` (never raises)."""
        try:
            import httpx

            httpx.get(f"{self.base_url}/models", timeout=2.0)
            return True
        except Exception:
            return False


def _ollama_reachable(base_url: str, timeout: float = 0.3) -> bool:
    """Best-effort, fast liveness probe for a local Ollama daemon.

    Short timeout on purpose: this runs on the hot ``get_embedder`` path, so a
    down/unreachable daemon (connection refused; the common case when Ollama
    simply isn't running) must fail fast, not hang. Any exception → ``False``,
    never raises.
    """
    try:
        import httpx

        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def get_embedder(config=Config) -> "Embedder":
    """Return the best available embedder for ``config``.

    Resolution (per call, not at import); ``config.embedder_backend`` (default
    ``"auto"``, read defensively via ``getattr`` so this works even before
    ``Config`` grows the attribute) gates everything:

    - ``"hash"``: always the offline :class:`HashingEmbedder`, unconditionally.
    - Otherwise, the pre-existing explicit knobs win first (unchanged, for
      backward compatibility):
        1. ``config.embeddings_model`` set → :class:`RemoteEmbedder` (OpenAI
           wire format) at ``config.embeddings_url``.
        2. ``config.embedding_model`` set (legacy) → same, at
           ``config.local_model_url``.
    - If neither explicit knob is set:
        - ``"ollama"``: force the native Ollama backend
          (``config.embedder_model`` / ``config.ollama_base_url``); degrades to
          :class:`HashingEmbedder` (with a once-per-process warning) if the
          daemon is unreachable.
        - ``"auto"`` (default); probe the local Ollama; use the native backend
          if reachable, else :class:`HashingEmbedder`.

    In every branch, a REMOTE embedder's own ``embed()`` still degrades
    per-call on failure; this function's job is only which embedder to hand
    back, never a guarantee that the remote is actually usable.
    """
    backend = (getattr(config, "embedder_backend", "auto") or "auto").strip().lower()

    if backend == "hash":
        return HashingEmbedder()

    # New dedicated knobs (Phase 5); explicit configuration always wins.
    model = getattr(config, "embeddings_model", "") or ""
    if model:
        base_url = getattr(config, "embeddings_url", "") or "http://localhost:11434"
        api_key = getattr(config, "embeddings_api_key", "") or ""
        return RemoteEmbedder(model, base_url, api_key=api_key)

    # Legacy knob (Phase 6; kept for backward compatibility).
    legacy_model = getattr(config, "embedding_model", "") or ""
    if legacy_model:
        legacy_url = getattr(config, "local_model_url", "") or OpenAICompatEmbedder._CLOUD_BASE
        api_key = getattr(config, "embeddings_api_key", "") or ""
        return RemoteEmbedder(legacy_model, legacy_url, api_key=api_key)

    # Neither explicit knob is set; native Ollama, gated by embedder_backend.
    ollama_model = getattr(config, "embedder_model", "") or "nomic-embed-text"
    ollama_base = (
        getattr(config, "ollama_base_url", "")
        or getattr(config, "embeddings_url", "")
        or "http://localhost:11434"
    )
    api_key = getattr(config, "embeddings_api_key", "") or ""

    if backend == "ollama":
        if not _ollama_reachable(ollama_base):
            global _warned_fallback
            if not _warned_fallback:
                _warned_fallback = True
                logger.warning(
                    f"embedder_backend=ollama but {ollama_base} is unreachable; "
                    "degrading to HashingEmbedder (will not warn again this process)"
                )
            return HashingEmbedder()
        return RemoteEmbedder(ollama_model, ollama_base, api_key=api_key, backend="ollama")

    # backend == "auto" (or any unrecognized value; degrade safely).
    if _ollama_reachable(ollama_base):
        return RemoteEmbedder(ollama_model, ollama_base, api_key=api_key, backend="ollama")
    return HashingEmbedder()


def needs_reindex(stored_model: str, embedder: "Embedder") -> bool:
    """Cheap guard: does a row stamped ``stored_model`` match ``embedder``'s
    CURRENT name?

    Uses ``embedder.name`` (the same tag :func:`reindex` stamps new vectors
    with, and the same tag :mod:`lhassa.memory.load` filters semantic matches
    by) rather than ``fingerprint``: the two coincide once ``embed()`` has
    been called at least once against the live backend, and this keeps the
    comparison consistent with the one the semantic-query filter already makes.
    Never raises: a missing/odd ``embedder.name`` degrades to "needs reindex".
    """
    current = getattr(embedder, "name", "") or ""
    return (stored_model or "") != current


async def reindex(instance_id: str, config=None) -> int:
    """Re-embed ALL facts for ``instance_id`` with the current embedder.

    Unlike :func:`lhassa.memory.load.index_facts` (which skips already-indexed
    facts), ``reindex`` overwrites every stored vector and model stamp. Use this
    after switching the embedder model so all stored vectors become consistent
    with the current backend.

    Best-effort PER FACT: a single fact's upsert failing (e.g. a transient DB
    error) is logged at DEBUG and skipped, not fatal to the whole batch.
    Returns the number of facts successfully re-embedded (not the total
    considered; see :func:`reindex_all` for a fuller breakdown).
    """
    from lhassa.core import db
    from lhassa.memory.facts import load_facts

    config = config or Config
    facts = await load_facts(instance_id)
    if not facts:
        return 0
    embedder = get_embedder(config)
    vectors = embedder.embed([f.contenido for f in facts])
    done = 0
    for fact, vec in zip(facts, vectors):
        try:
            await db.upsert_fact_embedding(instance_id, fact.id, vec, model=embedder.name)
            done += 1
        except Exception as exc:  # best-effort; one bad row never aborts the batch
            logger.debug(f"reindex: fact {fact.id} failed, skipping: {exc}")
    return done


async def _reindex_answer_cache(instance_id: str, embedder: "Embedder") -> tuple[int, int]:
    """Re-embed every cached question for ``instance_id`` with ``embedder``.

    ``answer_cache`` carries a per-row ``embedder`` stamp (migration 12,
    mirroring ``fact_embeddings.model``), so this writes both the new vector
    AND the current embedder's name via a raw ``UPDATE`` against the same
    table :mod:`lhassa.core.db` already owns; reusing ``db.DB_PATH`` and
    ``db.list_answer_cache`` rather than duplicating the connection/table
    definition. Best-effort per row. Returns ``(updated, failed)``.
    """
    from lhassa.core import db
    from lhassa._vendor import aiosqlite

    rows = await db.list_answer_cache(instance_id, limit=1_000_000)
    if not rows:
        return 0, 0

    updated = failed = 0
    for row in rows:
        try:
            vector = embedder.embed([row["question"]])[0]
            async with aiosqlite.connect(db.DB_PATH) as conn:
                await conn.execute(
                    "UPDATE answer_cache SET embedding = ?, embedder = ? "
                    "WHERE instance_id = ? AND id = ?",
                    (_json_dump(vector), embedder.name, instance_id, row["id"]),
                )
                await conn.commit()
            updated += 1
        except Exception as exc:  # best-effort; one bad row never aborts the batch
            logger.debug(f"reindex: answer_cache row {row.get('id')} failed, skipping: {exc}")
            failed += 1
    return updated, failed


def _json_dump(vector: list[float]) -> str:
    import json

    return json.dumps(list(vector))


async def reindex_all(instance_id: str, config=None) -> dict:
    """Re-embed BOTH facts and cached answers for ``instance_id`` with the
    CURRENT embedder; the full migration path after switching backends.

    Never raises: each half is independently best-effort (a facts failure
    doesn't block the answer-cache pass and vice versa). Returns a summary
    dict with per-table counts and the embedder's ``fingerprint``, suitable for
    ``lhassa memory reindex --json`` or a plain progress print.
    """
    config = config or Config
    embedder = get_embedder(config)

    facts_done = 0
    try:
        facts_done = await reindex(instance_id, config)
    except Exception as exc:  # never let a facts-side failure block answer_cache
        logger.warning(f"reindex_all: facts pass failed: {exc}")

    answers_done = answers_failed = 0
    try:
        answers_done, answers_failed = await _reindex_answer_cache(instance_id, embedder)
    except Exception as exc:  # never raise out of a best-effort migration helper
        logger.warning(f"reindex_all: answer_cache pass failed: {exc}")

    return {
        "facts": facts_done,
        "answers": answers_done,
        "answers_failed": answers_failed,
        "embedder": getattr(embedder, "fingerprint", getattr(embedder, "name", "")),
    }


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 on degenerate input)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
