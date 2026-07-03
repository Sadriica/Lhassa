"""
Pluggable, offline-capable embedders for memory layer 4 (semantic retrieval).

Layer 4 was deferred since Phase 1; this is its minimal, dependency-free core.
The default :class:`HashingEmbedder` is pure-stdlib and DETERMINISTIC (same text →
same vector), so the whole semantic layer works and is testable with zero config
and no network. :class:`RemoteEmbedder` is an optional upgrade that hits an
OpenAI-compatible ``/v1/embeddings`` endpoint (local Ollama first, else cloud);
it NEVER crashes memory loading — any failure degrades silently to the hashing
embedder. The fallback warning is logged at most ONCE per process.

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
    """Stable hash of a token into ``[0, dim)`` — uses blake2b, NOT ``hash()``.

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
    """OpenAI-compatible remote embedder with ``HashingEmbedder`` fallback.

    Resolution is PER CALL: each ``embed()`` attempts the configured endpoint
    (POST ``{base_url}/v1/embeddings``, timeout 5 s) and falls back silently to
    :class:`HashingEmbedder` on any failure (connection error, HTTP error,
    timeout, bad payload). The fallback warning is logged at most ONCE per
    process via the module-level ``_warned_fallback`` flag.

    ``name`` reflects the ACTUAL backend used in the last ``embed()`` call:
    the configured model name on success, or ``HashingEmbedder.name`` on
    fallback. Stamping stored vectors with this post-call name guarantees that
    vectors from different backends are never compared against each other in the
    semantic layer.
    """

    def __init__(self, model: str, base_url: str, *, api_key: str = ""):
        self._remote_name = model
        self.name = model  # updated after each embed() call
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._fallback = HashingEmbedder()

    def embed(self, texts: list[str]) -> list[list[float]]:
        global _warned_fallback
        try:
            result = self._embed_remote(texts)
            self.name = self._remote_name  # remote succeeded
            return result
        except Exception as exc:
            if not _warned_fallback:
                _warned_fallback = True
                logger.warning(
                    f"RemoteEmbedder: falling back to HashingEmbedder "
                    f"(will not warn again this process): {exc}"
                )
            self.name = self._fallback.name  # reflect actual backend
            return self._fallback.embed(texts)

    def _embed_remote(self, texts: list[str]) -> list[list[float]]:
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


def get_embedder(config=Config) -> "Embedder":
    """Return the best available embedder for ``config``.

    Resolution (per call, not at import):
    1. If ``config.embeddings_model`` is set, return a :class:`RemoteEmbedder`
       pointing at ``config.embeddings_url``. The remote is tried on each
       ``embed()`` call; any failure silently falls back to
       :class:`HashingEmbedder` inside the instance.
    2. If ``config.embedding_model`` is set (legacy knob), same as above but
       using ``config.local_model_url`` as the base URL.
    3. Otherwise return the always-available offline :class:`HashingEmbedder`.
    """
    # New dedicated knobs (Phase 5).
    model = getattr(config, "embeddings_model", "") or ""
    if model:
        base_url = getattr(config, "embeddings_url", "") or "http://localhost:11434"
        api_key = getattr(config, "embeddings_api_key", "") or ""
        return RemoteEmbedder(model, base_url, api_key=api_key)

    # Legacy knob (Phase 6 — kept for backward compatibility).
    legacy_model = getattr(config, "embedding_model", "") or ""
    if legacy_model:
        legacy_url = getattr(config, "local_model_url", "") or OpenAICompatEmbedder._CLOUD_BASE
        api_key = getattr(config, "embeddings_api_key", "") or ""
        return RemoteEmbedder(legacy_model, legacy_url, api_key=api_key)

    return HashingEmbedder()


async def reindex(instance_id: str, config=None) -> int:
    """Re-embed ALL facts for ``instance_id`` with the current embedder.

    Unlike :func:`lhassa.memory.load.index_facts` (which skips already-indexed
    facts), ``reindex`` overwrites every stored vector and model stamp. Use this
    after switching the embedder model so all stored vectors become consistent
    with the current backend.

    Returns the number of facts re-embedded.
    """
    from lhassa.core import db
    from lhassa.memory.facts import load_facts

    config = config or Config
    facts = await load_facts(instance_id)
    if not facts:
        return 0
    embedder = get_embedder(config)
    vectors = embedder.embed([f.contenido for f in facts])
    for fact, vec in zip(facts, vectors):
        await db.upsert_fact_embedding(instance_id, fact.id, vec, model=embedder.name)
    return len(facts)


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
