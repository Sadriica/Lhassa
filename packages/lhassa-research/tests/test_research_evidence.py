"""Research source custody; deterministic and fully offline."""

import pytest

from lhassa.research.evidence import (
    build_evidence_bundle,
    bundle_for_model,
    extract_urls,
)


def test_extract_urls_is_ordered_and_deduplicated():
    assert extract_urls("See https://a.test/x and https://a.test/x, then https://b.test.") == [
        "https://a.test/x",
        "https://b.test",
    ]


@pytest.mark.asyncio
async def test_explicit_source_is_captured_and_primary_nested_resource_is_chased():
    calls = []

    async def fetch(url):
        calls.append(url)
        if url == "https://example.test/post":
            return {
                "requested_url": url,
                "final_url": url,
                "status": "captured",
                "sha256": "a" * 64,
                "text": "The post claims a 20% improvement.",
                "links": [
                    {"url": "https://example.test/about", "label": "About"},
                    {"url": "https://arxiv.org/abs/1234", "label": "Paper"},
                ],
            }
        return {
            "requested_url": url,
            "final_url": url,
            "status": "captured",
            "sha256": "b" * 64,
            "text": "Primary paper.",
            "links": [],
        }

    async def must_not_search(*args, **kwargs):
        raise AssertionError("an explicit URL must not trigger search")

    bundle = await build_evidence_bundle(
        "Investigate https://example.test/post",
        search_fn=must_not_search,
        fetch_fn=fetch,
    )
    assert calls == ["https://example.test/post", "https://arxiv.org/abs/1234"]
    assert [source["id"] for source in bundle["sources"]] == ["S1", "S2"]
    assert bundle["sources"][1]["discovered_from"] == "S1"
    assert bundle["trust"] == "MUNDO"
    assert len(bundle["bundle_sha256"]) == 64


@pytest.mark.asyncio
async def test_text_idea_searches_then_records_capture_failure_honestly():
    async def search(query, max_results):
        assert query == "bounded agent loops"
        return [{"title": "Result", "body": "snippet only", "url": "https://x.test"}]

    async def fetch(url):
        return {"requested_url": url, "final_url": url, "status": "failed", "error": "paywall"}

    bundle = await build_evidence_bundle(
        "bounded agent loops", search_fn=search, fetch_fn=fetch
    )
    source = bundle["sources"][0]
    assert source["provenance"] == "search"
    assert source["status"] == "failed"
    assert source["search_snippet"] == "snippet only"
    assert bundle["search_performed"] is True


def test_model_bundle_trims_text_without_changing_custody_digest():
    bundle = {
        "bundle_sha256": "digest",
        "sources": [{"id": "S1", "text": "x" * 9000, "links": [{}] * 20}],
    }
    trimmed = bundle_for_model(bundle)
    assert len(trimmed["sources"][0]["text"]) == 5000
    assert len(trimmed["sources"][0]["links"]) == 12
    assert bundle["sources"][0]["text"] == "x" * 9000
