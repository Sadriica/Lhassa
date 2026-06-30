"""
Pluggable, offline-capable embedders for memory layer 4 (semantic retrieval).

Layer 4 was deferred since Phase 1; this is its minimal, dependency-free core.
The default :class:`HashingEmbedder` is pure-stdlib and DETERMINISTIC (same text →
same vector), so the whole semantic layer works and is testable with zero config
and no network. :class:`OpenAICompatEmbedder` is an optional upgrade that hits an
OpenAI-compatible ``/embeddings`` endpoint (local model first, else cloud); it
NEVER crashes memory loading — any failure degrades to the hashing embedder.

No numpy: cosine and the vectors are plain Python ``list[float]`` / ``math``.
"""

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

from lhassa.config import Config

# Default dimensionality of the offline hashing embedder. Small enough to be
# cheap, large enough that token collisions are rare for short fact strings.
_HASH_DIM = 256

_TOKEN_RE = re.compile(r"[\wáéíóúñü]+", re.UNICODE)


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

    name = "hashing"

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


class OpenAICompatEmbedder:
    """Optional embedder hitting an OpenAI-compatible ``/embeddings`` endpoint.

    Used only when ``config.embedding_model`` is non-empty. The base URL is the
    local model URL when configured (data never leaves the machine), else the
    cloud endpoint. Any network/parse failure degrades to a :class:`HashingEmbedder`
    so memory loading never crashes.
    """

    # Cloud default base URL (OpenAI-compatible). [SUPUESTO]: not exercised
    # offline; the local URL is preferred whenever ``local_model_url`` is set.
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
            # NEVER crash memory loading — fall back to the offline embedder.
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
        # Preserve request order via the returned ``index``.
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


def get_embedder(config=Config) -> Embedder:
    """Return the best available embedder for ``config``.

    :class:`OpenAICompatEmbedder` when ``embedding_model`` is set and the endpoint
    is reachable; otherwise the always-available offline :class:`HashingEmbedder`.
    """
    model = getattr(config, "embedding_model", "") or ""
    if not model:
        return HashingEmbedder()
    base_url = getattr(config, "local_model_url", "") or OpenAICompatEmbedder._CLOUD_BASE
    candidate = OpenAICompatEmbedder(model, base_url)
    if candidate.reachable():
        return candidate
    return HashingEmbedder()


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
