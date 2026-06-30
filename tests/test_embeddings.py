"""Tests for lhassa.memory.embeddings — the offline-capable layer-4 embedders."""

from lhassa.memory.embeddings import (
    Embedder,
    HashingEmbedder,
    OpenAICompatEmbedder,
    cosine,
    get_embedder,
)


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


def test_get_embedder_defaults_to_hashing_offline():
    # No embedding_model → always the offline hashing embedder (no network).
    assert isinstance(get_embedder(_Cfg), HashingEmbedder)


def test_get_embedder_unreachable_model_degrades_to_hashing():
    class Cfg:
        embedding_model = "text-embedding-3-small"
        # An unroutable local URL — the reachability probe fails fast.
        local_model_url = "http://127.0.0.1:1/v1"

    assert isinstance(get_embedder(Cfg), HashingEmbedder)


def test_openai_compat_embed_degrades_on_failure():
    # Unreachable endpoint: embed() must NOT raise — it falls back to hashing.
    emb = OpenAICompatEmbedder("m", "http://127.0.0.1:1/v1")
    out = emb.embed(["texto"])
    assert out == HashingEmbedder().embed(["texto"])
