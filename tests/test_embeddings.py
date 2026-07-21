"""Tests for lhassa.memory.embeddings — the offline-capable layer-4 embedders."""

import httpx

from lhassa.memory.embeddings import (
    Embedder,
    HashingEmbedder,
    OpenAICompatEmbedder,
    RemoteEmbedder,
    cosine,
    get_embedder,
    needs_reindex,
)
import lhassa.memory.embeddings as emb_mod


# ── HashingEmbedder: deterministic, offline ──────────────────────────────────────

def test_hashing_is_deterministic():
    e = HashingEmbedder()
    v1 = e.embed(["le gusta el chocolate con menta"])[0]
    v2 = e.embed(["le gusta el chocolate con menta"])[0]
    assert v1 == v2  # same text → identical vector, no randomness


def test_hashing_fresh_instances_agree():
    # Determinism must hold across instances/processes (no per-run salt).
    a = HashingEmbedder().embed(["hola mundo"])[0]
    b = HashingEmbedder().embed(["hola mundo"])[0]
    assert a == b


def test_hashing_identical_text_cosine_is_one():
    e = HashingEmbedder()
    v = e.embed(["proyecto de optimización"])[0]
    assert abs(cosine(v, v) - 1.0) < 1e-9


def test_hashing_related_beats_unrelated():
    e = HashingEmbedder()
    query = e.embed(["cómo optimizar el rendimiento del sistema"])[0]
    related = e.embed(["optimizar el rendimiento es clave"])[0]
    unrelated = e.embed(["receta de pastel de chocolate"])[0]
    assert cosine(query, related) > cosine(query, unrelated)


def test_hashing_vector_is_l2_normalized():
    e = HashingEmbedder()
    v = e.embed(["algo con texto"])[0]
    norm = sum(x * x for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-9


def test_hashing_empty_text_is_zero_vector():
    e = HashingEmbedder()
    v = e.embed([""])[0]
    assert len(v) == e.dim
    assert all(x == 0.0 for x in v)


def test_hashing_batch_matches_singletons():
    e = HashingEmbedder()
    batch = e.embed(["uno", "dos"])
    assert batch[0] == e.embed(["uno"])[0]
    assert batch[1] == e.embed(["dos"])[0]


def test_hashing_satisfies_embedder_protocol():
    assert isinstance(HashingEmbedder(), Embedder)


# ── cosine edge cases ────────────────────────────────────────────────────────────

def test_cosine_orthogonal_and_degenerate():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([], []) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([1.0, 2.0], [1.0]) == 0.0  # dim mismatch never raises


# ── get_embedder selection ───────────────────────────────────────────────────────

class _Cfg:
    embedding_model = ""
    local_model_url = ""
    embedder_backend = "hash"  # hermetic: never probe a live local Ollama


def test_get_embedder_defaults_to_hashing_offline():
    # No embedding_model → always the offline hashing embedder (no network).
    assert isinstance(get_embedder(_Cfg), HashingEmbedder)


def test_get_embedder_returns_remote_embedder_when_model_set():
    """get_embedder returns RemoteEmbedder when a model is configured; the per-call
    fallback happens inside embed(), not inside get_embedder."""
    class Cfg:
        embeddings_model = "text-embedding-3-small"
        embeddings_url = "http://127.0.0.1:1"  # guaranteed unreachable
        embeddings_api_key = ""
        embedding_model = ""
        local_model_url = ""

    emb = get_embedder(Cfg)
    assert isinstance(emb, RemoteEmbedder)
    # embed() falls back to hashing output when the endpoint is unreachable.
    result = emb.embed(["texto"])
    assert result == HashingEmbedder().embed(["texto"])


def test_openai_compat_embed_degrades_on_failure():
    # Unreachable endpoint: embed() must NOT raise — it falls back to hashing.
    emb = OpenAICompatEmbedder("m", "http://127.0.0.1:1/v1")
    out = emb.embed(["texto"])
    assert out == HashingEmbedder().embed(["texto"])


# ── fingerprint ───────────────────────────────────────────────────────────────────

def test_hashing_fingerprint_is_stable():
    assert HashingEmbedder().fingerprint == "hash:v1"


def test_remote_embedder_fingerprint_reflects_configured_backend_and_model():
    openai_emb = RemoteEmbedder("text-embedding-3-small", "http://127.0.0.1:1")
    assert openai_emb.fingerprint == "openai:text-embedding-3-small"

    ollama_emb = RemoteEmbedder("nomic-embed-text", "http://127.0.0.1:1", backend="ollama")
    assert ollama_emb.fingerprint == "ollama:nomic-embed-text"


def test_remote_embedder_fingerprint_survives_a_fallback_call():
    # fingerprint reflects the CONFIGURED identity even after embed() falls
    # back to hashing (unlike .name, which flips to reflect the actual backend
    # used by the last call).
    emb = RemoteEmbedder("nomic-embed-text", "http://127.0.0.1:1", backend="ollama")
    emb.embed(["texto"])  # unreachable → falls back, mutates .name
    assert emb.name == HashingEmbedder.name
    assert emb.fingerprint == "ollama:nomic-embed-text"


def test_openai_compat_fingerprint():
    assert OpenAICompatEmbedder("m", "http://x").fingerprint == "openai:m"


# ── needs_reindex ─────────────────────────────────────────────────────────────────

def test_needs_reindex_true_on_mismatch():
    assert needs_reindex("old-model", HashingEmbedder()) is True


def test_needs_reindex_false_on_match():
    h = HashingEmbedder()
    assert needs_reindex(h.name, h) is False


def test_needs_reindex_true_on_missing_stamp():
    assert needs_reindex("", HashingEmbedder()) is True
    assert needs_reindex(None, HashingEmbedder()) is True


# ── RemoteEmbedder(backend="ollama") — native Ollama embeddings API ──────────────

def _json_response(payload, status_code=200, url="http://localhost:11434/x"):
    return httpx.Response(status_code, json=payload, request=httpx.Request("POST", url))


def test_ollama_backend_embed_uses_native_batch_endpoint(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        assert url == "http://localhost:11434/api/embed"
        assert json == {"model": "nomic-embed-text", "input": ["uno", "dos"]}
        return _json_response({"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    monkeypatch.setattr(httpx, "post", fake_post)

    emb = RemoteEmbedder("nomic-embed-text", "http://localhost:11434", backend="ollama")
    out = emb.embed(["uno", "dos"])

    assert out == [[0.1, 0.2], [0.3, 0.4]]
    assert emb.name == "nomic-embed-text"  # remote succeeded, name reflects it
    assert calls == ["http://localhost:11434/api/embed"]


def test_ollama_backend_falls_back_to_singular_endpoint(monkeypatch):
    """When /api/embed fails (older Ollama), retry per-text against
    /api/embeddings before giving up to the hashing fallback."""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/api/embed"):
            return _json_response({"error": "not found"}, status_code=404)
        assert url.endswith("/api/embeddings")
        return _json_response({"embedding": [0.5, 0.6]})

    monkeypatch.setattr(httpx, "post", fake_post)

    emb = RemoteEmbedder("nomic-embed-text", "http://localhost:11434", backend="ollama")
    out = emb.embed(["uno"])

    assert out == [[0.5, 0.6]]
    assert calls == [
        "http://localhost:11434/api/embed",
        "http://localhost:11434/api/embeddings",
    ]


def test_ollama_backend_degrades_to_hashing_when_totally_unreachable(monkeypatch):
    emb_mod._warned_fallback = False

    def fake_post(url, json=None, headers=None, timeout=None):
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    emb = RemoteEmbedder("nomic-embed-text", "http://localhost:11434", backend="ollama")
    out = emb.embed(["texto"])

    assert out == HashingEmbedder().embed(["texto"])
    assert emb.name == HashingEmbedder.name
    emb_mod._warned_fallback = False


# ── get_embedder — embedder_backend selection ("hash" / "ollama" / "auto") ───────

class _BareCfg:
    """No embedding-related attributes at all — every knob resolves via defaults."""


def test_get_embedder_hash_backend_forces_hashing_even_if_legacy_knobs_set():
    class Cfg:
        embedder_backend = "hash"
        embeddings_model = "text-embedding-3-small"  # would normally win — overridden

    assert isinstance(get_embedder(Cfg), HashingEmbedder)


def test_get_embedder_auto_degrades_to_hashing_when_ollama_unreachable(monkeypatch):
    def fake_get(url, timeout=None):
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    assert isinstance(get_embedder(_BareCfg), HashingEmbedder)


def test_get_embedder_auto_prefers_ollama_when_reachable(monkeypatch):
    def fake_get(url, timeout=None):
        assert url == "http://localhost:11434/api/tags"
        return _json_response({"models": []})

    monkeypatch.setattr(httpx, "get", fake_get)

    emb = get_embedder(_BareCfg)
    assert isinstance(emb, RemoteEmbedder)
    assert emb.backend == "ollama"
    assert emb.model == "nomic-embed-text"
    assert emb.base_url == "http://localhost:11434"


def test_get_embedder_ollama_backend_forced_and_reachable(monkeypatch):
    class Cfg:
        embedder_backend = "ollama"
        embedder_model = "mxbai-embed-large"
        ollama_base_url = "http://localhost:11434"

    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: _json_response({}))

    emb = get_embedder(Cfg)
    assert isinstance(emb, RemoteEmbedder)
    assert emb.backend == "ollama"
    assert emb.model == "mxbai-embed-large"


def test_get_embedder_ollama_backend_forced_and_unreachable_warns_and_degrades(monkeypatch):
    emb_mod._warned_fallback = False

    class Cfg:
        embedder_backend = "ollama"

    def fake_get(url, timeout=None):
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    emb = get_embedder(Cfg)
    assert isinstance(emb, HashingEmbedder)
    assert emb_mod._warned_fallback is True
    emb_mod._warned_fallback = False


def test_get_embedder_auto_still_prefers_legacy_knob_over_ollama_probe(monkeypatch):
    """Explicit legacy configuration wins over the new auto-ollama path, even
    when embedder_backend is left at its default 'auto' — backward compat."""
    def fake_get(url, timeout=None):
        raise AssertionError("must not probe Ollama when a legacy knob is set")

    monkeypatch.setattr(httpx, "get", fake_get)

    class Cfg:
        embeddings_model = "text-embedding-3-small"
        embeddings_url = "http://127.0.0.1:1"
        embeddings_api_key = ""

    emb = get_embedder(Cfg)
    assert isinstance(emb, RemoteEmbedder)
    assert emb.backend == "openai"
